"""唯一入口与自动部署执行（功能 014 US2/US3，契约 C6/C7 + FR-007）。

**唯一入口**：`evaluate_candidate` —— 由做梦轮次收口后一处接线调用（T1416）。触发收敛
为单点，避免"顺手开个自动部署"的旁路；模式**从 `mode_state(data_dir)` 读真实状态**
（不入参——否则调用方可以顺手指定 auto 绕过门禁）。三种行为（澄清 Q3）：

- `manual`：只落证据快照（现状不变，指针永不自动切换）；
- `shadow`：落快照 + 影子事件 + 影子候选计数（判定照跑、指针不动）；
- `auto`：eligible → 交 `auto_deploy` 部署；不满足 → 落快照 + 拦截记录（不部署）。

部署执行（US3 / T1422，契约 C7）：①证据快照前置 ②**指针一致性检测**（当前指针必须
等于部署留痕的最后部署版本；不一致 → 拒绝 + 告警留痕，防外部手工绕过）③定点改写部署指针
（`core/yaml_edit.upsert_section_entries`，注释与其他段逐字节保留）④部署事件留痕
`deployment/deploys/{ts}-{agent}.json`（只增不改）。谱系 `source=auto` 的落点就是这条
部署事件（引用候选版本）——**不重写 005 的 meta.json**（其只增不改，与 009/011 采纳留痕
同款口径）；全程不触树库、不改历史节点（本文档模块无 DB 依赖，机检静态断言把关）。
部署钩子可注入（`deploy=`），US2 的入口与 US3 的实现共用同一条分派路径。
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import yaml

from core.deployment import mode, shadow
from core.deployment.config import DeploymentConfig
from core.deployment.errors import DeploymentError, PointerMismatchError
from core.deployment.evidence import collect_evidence
from core.deployment.gate import gate_and_record
from core.deployment.models import (
    AutoDeployEvent,
    DeployMode,
    GateVerdict,
    HumanDecision,
    ShadowEvent,
)
from core.yaml_edit import upsert_section_entries

DEPLOYS_SUBDIR = "deploys"
ALERTS_FILE = "alerts.jsonl"


class DeployAction(StrEnum):
    """一次评估的实际动作（入口三行为的机检口径）。"""

    SNAPSHOT_ONLY = "snapshot_only"
    SHADOW_RECORDED = "shadow_recorded"
    BLOCKED = "blocked"
    DEPLOYED = "deployed"


@dataclass(frozen=True)
class EvaluationOutcome:
    """唯一入口的返回（契约 C6：判定 + 快照 + 实际动作）。"""

    agent_id: str
    candidate_version: str
    mode: DeployMode
    action: DeployAction
    verdict: GateVerdict
    snapshot_path: Path
    shadow_event: ShadowEvent | None = None
    deploy_event: AutoDeployEvent | None = None

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "candidate_version": self.candidate_version,
            "mode": self.mode.value,
            "action": self.action.value,
            "decision": self.verdict.decision.value,
            "reason": self.verdict.reason,
            "snapshot_path": str(self.snapshot_path),
            "shadow_event": None if self.shadow_event is None else self.shadow_event.to_dict(),
            "deploy_event": None if self.deploy_event is None else self.deploy_event.to_dict(),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def deploys_dir(data_dir: str | Path) -> Path:
    """部署留痕目录：deployment/deploys/。"""
    return Path(data_dir) / DEPLOYS_SUBDIR


def alerts_path(data_dir: str | Path) -> Path:
    """部署告警留痕（防绕过事件必须可追溯）：deployment/deploys/alerts.jsonl。"""
    return deploys_dir(data_dir) / ALERTS_FILE


def read_pointer(config_path: str | Path, agent_id: str) -> str | None:
    """读部署指针（configs 的 deployment.{agent}.current_policy_version）；缺段/缺键 → None。"""
    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    value = (payload.get("deployment") or {}).get(agent_id, {}).get("current_policy_version")
    return value or None


_DEPLOY_EVENT_KEYS = frozenset(
    {
        "agent_id",
        "candidate_version",
        "from_version",
        "pointer_before",
        "pointer_after",
        "deployed_at",
    }
)


def deploy_events(data_dir: str | Path, agent_id: str | None = None) -> list[dict]:
    """部署事件留痕（按时间序），每条附加 `_path`（留痕位置，便于按档追溯）。

    目录内还有同族留痕（同周期择一记录等）——按事件字段完整性过滤，非部署事件不入列
    （否则"最后一条部署"的位置会随无关文件漂移）。
    """
    directory = deploys_dir(data_dir)
    if not directory.is_dir():
        return []
    events = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not _DEPLOY_EVENT_KEYS <= set(payload):
            continue
        if agent_id is not None and payload.get("agent_id") != agent_id:
            continue
        events.append({**payload, "_path": str(path)})
    events.sort(key=lambda item: (item.get("deployed_at", ""), item["_path"]))
    return events


def deploy_event_path(data_dir: str | Path, event: AutoDeployEvent) -> Path:
    """部署事件文件名：deploys/{ts}-{agent}.json（时间戳取自 deployed_at）。"""
    stamp = datetime.fromisoformat(event.deployed_at).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return deploys_dir(data_dir) / f"{stamp}-{event.agent_id}.json"


def write_deploy_event(data_dir: str | Path, event: AutoDeployEvent) -> Path:
    """部署事件落盘（只增不改）：同刻同内容幂等；内容不同即拒绝覆盖。"""
    text = json.dumps(event.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = deploy_event_path(data_dir, event)
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return path
        raise DeploymentError(
            f"部署留痕只增不改：{path} 已存在且内容不同（两次部署落在同一秒？换时刻档）"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _record_pointer_alert(
    data_dir: str | Path, agent_id: str, pointer: str | None, expected: str | None, at: str
) -> Path:
    """指针不一致 → 告警留痕（追加写）：外部绕过必须留下可追溯的痕迹。"""
    path = alerts_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_id": agent_id,
        "kind": "pointer_mismatch",
        "pointer": pointer,
        "expected": expected,
        "at": at,
        "note": "部署指针与部署留痕不一致（疑似外部手工绕过系统）：拒绝部署，人工核对",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def auto_deploy(
    *,
    agent_id: str,
    candidate_version: str,
    snapshot_path: Path,
    from_version: str,
    cfg: DeploymentConfig,
    data_dir: Path,
    config_path: Path,
    at: str | None = None,
) -> AutoDeployEvent:
    """自动部署执行（契约 C7）：前置校验 → 指针一致性 → 定点改写 → 事件留痕。

    拒绝路径零副作用：指针、留痕都不动（只在防绕过场景追加一条告警留痕）。
    """
    moment = at or _now()
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.is_file():
        raise DeploymentError(f"证据快照未落盘：{snapshot_path}（部署前置：先判定落痕，再谈部署）")
    pointer = read_pointer(config_path, agent_id)
    ledger = deploy_events(data_dir, agent_id)
    expected = ledger[-1]["pointer_after"] if ledger else from_version
    if pointer != from_version or pointer != expected:
        alert = _record_pointer_alert(data_dir, agent_id, pointer, expected, moment)
        raise PointerMismatchError(
            "部署指针与留痕不一致（防外部绕过）："
            f"指针 {pointer!r}，留痕最后部署 {expected!r}，调用方视图 {from_version!r}；"
            f"已拒绝部署并落告警 {alert}"
        )
    if pointer == candidate_version:
        raise DeploymentError(
            f"候选 {candidate_version!r} 已是当前部署版本：无需部署（不制造空转留痕）"
        )

    config_path = Path(config_path)
    config_path.write_text(
        upsert_section_entries(
            config_path.read_text(encoding="utf-8"),
            ("deployment", agent_id),
            {"current_policy_version": candidate_version},
        ),
        encoding="utf-8",
    )
    event = AutoDeployEvent(
        agent_id=agent_id,
        candidate_version=candidate_version,
        from_version=from_version,
        evidence_snapshot=str(snapshot_path),
        deployed_at=moment,
        pointer_before=pointer,
        pointer_after=candidate_version,
        reason=f"门槛 eligible：证据快照 {snapshot_path.name}",
        source="auto",
    )
    write_deploy_event(data_dir, event)
    return event


def select_period_candidate(
    candidates: Sequence[Mapping],
    *,
    agent_id: str,
    period: str,
    data_dir: str | Path,
    at: str | None = None,
) -> dict:
    """同周期多候选按 reward 择一（其余如实记录，不批量连推；契约 C7 场景 3）。

    择一口径：reward 降序；**平分按版本号升序**（确定性，不靠字典序随机）。结果落
    `deploys/selection-{agent}-{period}.json`（只增不改，同内容幂等）。
    """
    if not candidates:
        raise DeploymentError("同周期择一至少需要一个候选（空列表无从择起）")
    for item in candidates:
        if not isinstance(item.get("version"), str) or not item["version"]:
            raise DeploymentError(f"候选必须携带非空 version，实际为 {item!r}")
        reward = item.get("reward")
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            raise DeploymentError(
                f"候选 {item['version']!r} 必须携带数值 reward，实际为 {reward!r}"
            )
    ordered = sorted(candidates, key=lambda item: (-float(item["reward"]), item["version"]))
    winner, *rest = ordered
    moment = at or _now()
    record = {
        "agent_id": agent_id,
        "period": period,
        "selected": winner["version"],
        "candidates": [
            {"version": item["version"], "reward": float(item["reward"])} for item in ordered
        ],
        "rejected": [
            {
                "version": item["version"],
                "reward": float(item["reward"]),
                "reason": "同周期按 reward 择一未中（不批量连推）",
            }
            for item in rest
        ],
        "reason": (
            f"同周期 {len(ordered)} 个候选满足门槛：按 reward 择一（{winner['version']} "
            f"reward={float(winner['reward']):g}）"
        ),
        "at": moment,
    }
    path = deploys_dir(data_dir) / f"selection-{agent_id}-{period}.json"
    text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != text:
            raise DeploymentError(
                f"同周期择一记录只增不改：{path} 已存在且内容不同（证据变化请换周期档）"
            )
        return record
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return record


DeployFn = Callable[..., AutoDeployEvent]


def evaluate_candidate(
    agent_id: str,
    candidate_version: str,
    *,
    cfg: DeploymentConfig,
    data_dir: str | Path,
    deployed_version: str | None = None,
    unbiasedness: object = None,
    reward_compare: dict | None = None,
    validation_rewards: dict | None = None,
    validation_source: str = "",
    judge_keys: tuple = (),
    drift_registry: object = None,
    period: str | None = None,
    human_decision: HumanDecision | str = HumanDecision.NONE,
    unacceptable: bool = False,
    config_path: str | Path | None = None,
    deploy: DeployFn | None = None,
    at: str | None = None,
) -> EvaluationOutcome:
    """唯一入口：收证据 → 判定 → 按当前模式落痕（manual/shadow 不部署，auto 交部署实现）。

    证据输入与 US1 的 `collect_evidence` 同口径；本轮未提供的证据一律记为 missing
    （**缺证据即拦截**，判定与快照照常落盘——留痕先于部署）。
    """
    moment = at or _now()
    state = mode.load_mode_state(data_dir, mode_default=cfg.mode_default, at=moment)
    bundle = collect_evidence(
        agent_id,
        candidate_version,
        cfg=cfg,
        deployed_version=deployed_version,
        unbiasedness=unbiasedness,
        reward_compare=reward_compare,
        validation_rewards=validation_rewards,
        validation_source=validation_source,
        judge_keys=judge_keys,
        drift_registry=drift_registry,
        collected_at=moment,
    )
    verdict, snapshot_path = gate_and_record(bundle, cfg, data_dir, at=moment)

    if state.current is DeployMode.MANUAL:
        return EvaluationOutcome(
            agent_id=agent_id,
            candidate_version=candidate_version,
            mode=state.current,
            action=DeployAction.SNAPSHOT_ONLY,
            verdict=verdict,
            snapshot_path=snapshot_path,
        )

    if state.current is DeployMode.SHADOW:
        event = shadow.record_shadow_event(
            agent_id,
            candidate_version,
            verdict.decision,
            data_dir=data_dir,
            reason=verdict.reason,
            period=period,
            human_decision=human_decision,
            unacceptable=unacceptable,
            at=moment,
        )
        mode.record_shadow_candidate(data_dir, at=moment)
        return EvaluationOutcome(
            agent_id=agent_id,
            candidate_version=candidate_version,
            mode=state.current,
            action=DeployAction.SHADOW_RECORDED,
            verdict=verdict,
            snapshot_path=snapshot_path,
            shadow_event=event,
        )

    if not verdict.allow:
        return EvaluationOutcome(
            agent_id=agent_id,
            candidate_version=candidate_version,
            mode=state.current,
            action=DeployAction.BLOCKED,
            verdict=verdict,
            snapshot_path=snapshot_path,
        )

    if config_path is None:
        raise DeploymentError(
            "auto 模式放行后必须提供 config_path（部署指针定点改写目标）："
            "拒绝静默跳过部署（判定与快照已落盘，可追溯）"
        )
    if deployed_version is None:
        raise DeploymentError(
            "auto 模式放行后必须提供 deployed_version（指针改写前值）：留痕不得缺前值"
        )
    deploy_fn: DeployFn = deploy or auto_deploy
    event = deploy_fn(
        agent_id=agent_id,
        candidate_version=candidate_version,
        snapshot_path=snapshot_path,
        from_version=deployed_version,
        cfg=cfg,
        data_dir=Path(data_dir),
        config_path=Path(config_path),
        at=moment,
    )
    return EvaluationOutcome(
        agent_id=agent_id,
        candidate_version=candidate_version,
        mode=state.current,
        action=DeployAction.DEPLOYED,
        verdict=verdict,
        snapshot_path=snapshot_path,
        deploy_event=event,
    )
