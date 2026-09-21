"""唯一入口与自动部署执行（功能 014 US2/US3，契约 C6/C7 + FR-007）。

**唯一入口**：`evaluate_candidate` —— 由做梦轮次收口后一处接线调用（T1416）。触发收敛
为单点，避免"顺手开个自动部署"的旁路；模式**从 `mode_state(data_dir)` 读真实状态**
（不入参——否则调用方可以顺手指定 auto 绕过门禁）。三种行为（澄清 Q3）：

- `manual`：只落证据快照（现状不变，指针永不自动切换）；
- `shadow`：落快照 + 影子事件 + 影子候选计数（判定照跑、指针不动）；
- `auto`：eligible → 交 `auto_deploy` 部署；不满足 → 落快照 + 拦截记录（不部署）。

本批（US2）交付入口分派、影子路径与拦截路径；`auto_deploy`（指针一致性检测 + 定点改写 +
部署事件留痕）由 US3/T1422 落地，当前**显式 NotImplementedError**（不静默跳过部署、
也不伪装成功）。部署钩子可注入（`deploy=`），US3 与测试共用同一分派路径。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from core.deployment import mode, shadow
from core.deployment.config import DeploymentConfig
from core.deployment.errors import DeploymentError
from core.deployment.evidence import collect_evidence
from core.deployment.gate import gate_and_record
from core.deployment.models import (
    AutoDeployEvent,
    DeployMode,
    GateVerdict,
    HumanDecision,
    ShadowEvent,
)


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
    """自动部署执行（契约 C7）：US3/T1422 落地——指针一致性检测 + 定点改写 + 留痕。

    本批（US2）**显式未实现**：宁可报错，也不允许"看起来部署了"的静默跳过。
    """
    raise NotImplementedError(
        "自动部署执行（指针一致性检测 + core/yaml_edit 定点改写 + 部署事件留痕 source=auto）"
        "由 US3/T1422 落地（T1422）；本批已交付唯一入口的模式分派、证据快照与影子/拦截路径"
    )


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
