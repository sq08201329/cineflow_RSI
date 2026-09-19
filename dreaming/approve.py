"""人工审批闸门（US2 / T416，contracts/approval.md，宪章原则六）。

审批单（胜出版本、与当期最优的 diff 摘要、train/validation 双池 reward
对比、候选诊断）JSON 落盘；decide 只接受 approved/rejected；审批记录写入
{version}.meta.json；部署指针仅 approved 可更新（SC-005 机检：指针版本
必须有 approved 记录，否则报错）；rejected 记录照常落盘、指针不变。
谱系落盘复用 002 versioning.py（.py 幂等）+ 本包 meta 读写。
"""

import difflib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from core.yaml_edit import upsert_section_entries
from dreaming.lineage import read_meta, write_meta
from dreaming.pipeline import DreamRound
from policies.versioning import record_policy

DEFAULT_TICKETS_DIR = Path("dreaming/tickets")


class ApprovalError(Exception):
    """审批闸门错误（枚举校验、部署指针机检失败等）。"""


@dataclass(frozen=True)
class ApprovalTicket:
    """审批单（JSON 落盘，人工 approve/reject 的输入）。"""

    path: Path
    winner_version: str
    champion_version: str


def _diff_summary(champion_source: str, winner_source: str) -> dict:
    """与当期最优的 diff 摘要（变更行数与 unified diff 预览）。"""
    diff = list(
        difflib.unified_diff(champion_source.splitlines(), winner_source.splitlines(), lineterm="")
    )
    changed = sum(
        1 for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    return {"changed_lines": changed, "preview": diff[:40]}


def create_approval_ticket(
    dream_round: DreamRound,
    champion_version: str,
    pool,
    *,
    champion_source: str = "",
    tickets_dir: str | Path = DEFAULT_TICKETS_DIR,
    replay_fn=None,
    lambda_: float = 0.5,
) -> ApprovalTicket:
    """生成审批单：胜出版本 + diff 摘要 + 双池 reward 对比 + 候选诊断。"""
    from dreaming.reward import compute_reward

    winner = next(
        (c for c in dream_round.candidates if c.version == dream_round.winner_version),
        None,
    )
    if winner is None or dream_round.winner_version is None:
        raise ApprovalError(f"轮次 {dream_round.round_id} 无胜出者，不生成审批单")

    # 双池 reward 对比：train = 轮次回放值；validation = 胜者在 validation 池重放
    train_reward = winner.reward.reward if winner.reward else 0.0
    validation_reward = None
    if replay_fn is not None:
        trajectory = replay_fn(winner.source_code, pool)
        validation_reward = compute_reward(trajectory, lambda_).reward

    payload = {
        "round_id": dream_round.round_id,
        "agent_id": dream_round.agent_id,
        "winner_version": winner.version,
        "champion_version": champion_version,
        "winner_source": winner.source_code,
        "diff_summary": _diff_summary(champion_source, winner.source_code),
        "reward_compare": {"train": train_reward, "validation": validation_reward},
        "candidate_diagnostics": {
            "candidate_count": len(dream_round.candidates),
            "rejected_count": sum(
                1 for c in dream_round.candidates if c.static_check == "rejected"
            ),
            "notes": dream_round.diagnostics,
        },
        "created_at": datetime.now().astimezone().isoformat(),
    }
    directory = Path(tickets_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dream_round.round_id}.approval.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return ApprovalTicket(
        path=path,
        winner_version=winner.version,
        champion_version=champion_version,
    )


def decide(
    ticket_path: str | Path,
    *,
    approver: str,
    decision: str,
    reason: str,
    history_root: str | Path = "policies/history",
    config_path: str | Path | None = None,
) -> dict:
    """审批决定：approved/rejected 落盘 meta.json；approved 才更新部署指针。"""
    if decision not in ("approved", "rejected"):
        raise ApprovalError(f"decision 必须为 approved/rejected，实际为 {decision!r}")
    ticket = json.loads(Path(ticket_path).read_text(encoding="utf-8"))
    version = ticket["winner_version"]
    agent_id = ticket["agent_id"]

    train_reward = ticket.get("reward_compare", {}).get("train")
    meta = {
        "version": version,
        "parent_version": ticket["champion_version"],
        "created_round": ticket["round_id"],
        "reward": {
            "pareto_auc": train_reward if train_reward is not None else 0.0,
            "parallel_penalty": 0.0,
            "lambda": 0.5,
            "reward": train_reward if train_reward is not None else 0.0,
        },
        "source": "dreaming",
        "approval": {
            "approver": approver,
            "at": datetime.now().astimezone().isoformat(),
            "decision": decision,
            "reason": reason,
        },
    }
    write_meta(history_root, agent_id, meta)  # 审批记录落盘（两种决定都落）

    if decision == "approved":
        # 谱系落盘：策略源码幂等写 policies/history/{agent}/{version}.py
        record_policy(ticket["winner_source"], agent_id, history_root=history_root)
        if config_path is not None:
            _update_pointer(config_path, agent_id, version)
    return meta


def _load_config(config_path: str | Path) -> dict:
    return yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))


def _update_pointer(config_path: str | Path, agent_id: str, version: str) -> None:
    """部署指针更新：configs 的 deployment.{agent_id}.current_policy_version。

    定点改写（core/yaml_edit.upsert_section_entries）：注释/空行/其他段逐字节
    保留，仅指针行变更；deployment 段或指针键缺失时幂等追加（WS3 遗留收口，
    不再 yaml.safe_load + safe_dump 全量重写丢注释）。
    """
    path = Path(config_path)
    path.write_text(
        upsert_section_entries(
            path.read_text(encoding="utf-8"),
            ("deployment", agent_id),
            {"current_policy_version": version},
        ),
        encoding="utf-8",
    )


def current_policy_version(
    agent_id: str,
    config_path: str | Path,
    *,
    history_root: str | Path = "policies/history",
) -> str:
    """部署指针读取 + SC-005 机检：指针版本必须有 approved 审批记录。"""
    config = _load_config(config_path)
    pointer = config.get("deployment", {}).get(agent_id, {}).get("current_policy_version")
    if not pointer:
        raise ApprovalError(f"部署指针未配置：deployment.{agent_id}.current_policy_version")
    meta = read_meta(history_root, agent_id, pointer)
    if meta is None or (meta.get("approval") or {}).get("decision") != "approved":
        raise ApprovalError(
            f"SC-005 机检失败：指针版本 {pointer} 无 approved 审批记录"
            "（未 approve 的版本不得成为当期策略）"
        )
    return pointer
