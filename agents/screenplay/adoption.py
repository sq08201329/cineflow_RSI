"""采纳门禁与决策留痕（合同 screenplay-degraded.md C14，FR-009）。

**人工采纳才动部署指针**（SC-002：未采纳策略更新部署指针的次数恒 0——机检依据 =
指针与采纳记录双留痕）：
- `adopt(comparison_id, decision, by, reason)`：decision ∈ {adopt, reject}；
  **两种决定都留痕**（人/时间/依据 comparison_id/理由，理由必须非空）；
- `adopt` → 更新 `configs` 的部署指针（`deployment.{agent_id}.current_policy_version`，
  005 同款指向；定点改写保留注释与其他段）；
- `reject` → 指针逐字节不变；
- 采纳前机检：对比报告必须存在（依据引用）+ 报告中的部署版本必须与当前指针一致
  （防止在漂移的基线上采纳——需重新对比）。

谱系 meta 保持"只增不改"（提交时的来源记录），本模块的采纳记录是决策留痕的权威载体
（`{comparison_id}.adoption.json`，只增不改）。
"""

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agents.screenplay.policy_versions import list_policy_versions
from agents.screenplay.sandbox_compare import DEFAULT_COMPARISON_DIR, load_comparison
from core.yaml_edit import upsert_section_entries

AGENT_ID = "screenplay"
DEFAULT_ADOPTION_DIR = Path("screenplay/adoptions")
DECISIONS = ("adopt", "reject")


class AdoptionError(Exception):
    """采纳/拒绝被拒（枚举非法、理由为空、依据缺失、指针与基线不一致）。"""


@dataclass(frozen=True)
class AdoptionRecord:
    """采纳记录（C14）：结论 / 人 / 时间 / 依据（报告引用）/ 理由 / 指针前后值。"""

    comparison_id: str
    agent_id: str
    decision: str  # adopt | reject
    by: str
    reason: str
    at: str
    deployed_before: str | None
    deployed_after: str | None
    adopted_version: str | None
    record_path: Path | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["record_path"] = None if self.record_path is None else str(self.record_path)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def deployed_version(config_path: str | Path, *, agent_id: str = AGENT_ID) -> str | None:
    """部署指针读取（未配置返回 None——如实不伪造，调用方自行判定）。"""
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return config.get("deployment", {}).get(agent_id, {}).get("current_policy_version")


def _update_pointer(config_path: Path, agent_id: str, version: str) -> None:
    """部署指针更新：定点改写（注释/空行/其他段逐字节保留），缺失时幂等追加。"""
    config_path.write_text(
        upsert_section_entries(
            config_path.read_text(encoding="utf-8"),
            ("deployment", agent_id),
            {"current_policy_version": version},
        ),
        encoding="utf-8",
    )


def adopt(
    comparison_id: str,
    decision: str,
    by: str,
    reason: str,
    *,
    config_path: str | Path,
    comparison_dir: str | Path = DEFAULT_COMPARISON_DIR,
    adoption_dir: str | Path = DEFAULT_ADOPTION_DIR,
    history_root: str | Path = "policies/history",
    agent_id: str = AGENT_ID,
) -> AdoptionRecord:
    """人工采纳/拒绝（C14）：仅 adopt 更新部署指针；两种决定都留痕（理由非空）。"""
    if decision not in DECISIONS:
        raise AdoptionError(f"decision 必须为 {list(DECISIONS)} 之一，实际为 {decision!r}")
    if not isinstance(by, str) or not by:
        raise AdoptionError("决策人（by）不能为空（留痕必填）")
    if not isinstance(reason, str) or not reason.strip():
        raise AdoptionError("理由（reason）不能为空（拒绝留痕理由非空，采纳同样留痕）")

    report = load_comparison(comparison_id, comparison_dir=comparison_dir)
    config_path = Path(config_path)
    before = deployed_version(config_path, agent_id=agent_id)
    if before != report["deployed_version"]:
        raise AdoptionError(
            f"部署指针（{before!r}）与对比报告的基线版本（{report['deployed_version']!r}）不一致："
            "基线已漂移，请重新回放对比后再决策"
        )
    new_version = report["new_version"]

    if decision == "adopt":
        if new_version not in list_policy_versions(history_root=history_root, agent_id=agent_id):
            raise AdoptionError(
                f"待采纳版本 {new_version!r} 不在策略历史内（未版本化的策略不得部署）"
            )
        _update_pointer(config_path, agent_id, new_version)
    after = deployed_version(config_path, agent_id=agent_id)
    record = AdoptionRecord(
        comparison_id=comparison_id,
        agent_id=agent_id,
        decision=decision,
        by=by,
        reason=reason,
        at=datetime.now(UTC).isoformat(),
        deployed_before=before,
        deployed_after=after,
        adopted_version=new_version if decision == "adopt" else None,
    )
    directory = Path(adoption_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{comparison_id}.{decision}.json"
    if target.exists():
        raise AdoptionError(f"该对比报告的 {decision} 决策已留痕（只增不改）：{target}")
    target.write_text(record.to_json(), encoding="utf-8")
    return replace(record, record_path=target)


def load_adoption_record(
    comparison_id: str, decision: str, *, adoption_dir: str | Path = DEFAULT_ADOPTION_DIR
) -> dict:
    """读取采纳记录（审计用；不存在即报错，不静默返回空）。"""
    path = Path(adoption_dir) / f"{comparison_id}.{decision}.json"
    if not path.is_file():
        raise FileNotFoundError(f"采纳记录不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))
