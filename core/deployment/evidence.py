"""证据包收集（功能 014 US1 / T1411，契约 C1 + FR-001）。

候选策略的**证据包**：前置 = 无偏性验收结论；三要件 = ①回放 reward 对比（011 池化口径
vs 现部署版本）②validation 排名（005 防过拟合口径）③judge 漂移 verdict（012
`deploy_evidence_verdict` 对全部相关 judge 版本）。口径**全部复用既有实现**，本模块不新造
对比/排名/漂移逻辑——只做读取、投影与"就绪度"翻译（satisfied/unsatisfied/not_applicable/missing）。

诚实边界（原则六）：
- 缺结论/缺产物 = `missing`（证据不足，绝不推测为通过）；非法 payload = 报错
  （`missing` 与"证据坏了"必须可区分——后者静默降级会掩盖取证流程故障）；
- 无 judge 层的 Agent → 漂移要件 `not_applicable`，**默认不放宽整体门槛**（是否放宽由
  `deployment.gate.allow_without_judge` 在判定层决定，默认 false）；
- 判定只读既有产物（不重跑回放、不触网关），单次判定 < 1 秒（原则三）。
"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from core.calibration.drift_gate import RegistryLike, deploy_evidence_verdict
from core.deployment.config import DeploymentConfig
from core.deployment.errors import DeploymentEvidenceError
from core.deployment.models import (
    PREREQUISITE_UNBIASEDNESS,
    REQUIREMENT_DRIFT,
    REQUIREMENT_REWARD,
    REQUIREMENT_VALIDATION,
    EvidenceBundle,
    RequirementResult,
    RequirementState,
)

# 口径来源引用（证据包自描述：来源不是"编号"，而是实际被复用的接口/产物）
VALIDATION_SOURCE = (
    "005 口径：validation 池逐版本 reward 排名（dreaming/overfit.evaluate_overfit 同款 cutoff）"
)
DRIFT_SOURCE = (
    "012 接口：core.calibration.drift_gate.deploy_evidence_verdict（drift/status 状态登记）"
)
UNBIASEDNESS_SOURCE = "002 无偏性验收结论（verify_unbiasedness 产物）"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def validation_cutoff(total: int, top_ratio: float) -> int:
    """validation 名次上限（005 逐字同款：`max(1, int(n * top_ratio))`）。"""
    if total < 0:
        raise DeploymentEvidenceError(f"validation 样本数不得为负，实际为 {total}")
    return max(1, int(total * top_ratio))


def validation_rank(
    candidate_version: str, rewards: Mapping[str, float], *, top_ratio: float
) -> tuple[int, int]:
    """候选在 validation 池的名次与总数（005 口径：降序；**并列取最劣名次**；未上榜 = 最末）。"""
    values = []
    for version, value in rewards.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise DeploymentEvidenceError(
                f"validation reward 必须为有限数值：{version!r} → {value!r}"
            )
        values.append(float(value))
    if not values:
        raise DeploymentEvidenceError("validation 池为空：无排名可言（应由调用方记为 missing）")
    ranked = sorted(values, reverse=True)
    score = rewards.get(candidate_version)
    if score is None:
        rank = len(ranked)  # 未上榜 = 最末（保守，不给"未知"让路）
    else:
        rank = ranked.index(float(score)) + 1 + (ranked.count(float(score)) - 1)
    return rank, len(ranked)


def reward_compare_payload(
    candidate: float, deployed: float, *, source: str, note: str = ""
) -> dict:
    """011 池化回放对比结果的规范 payload（生产者与消费方同口径，夹具不另造 schema）。

    字段：候选 reward / 现部署 reward / 对比产物来源引用（`replay/pools/...`）。
    """
    return {"candidate": candidate, "deployed": deployed, "source": source, "note": note}


def _unbiasedness_result(cfg: DeploymentConfig, payload: object) -> RequirementResult:
    if not cfg.gate.require_unbiasedness:
        return RequirementResult(
            name=PREREQUISITE_UNBIASEDNESS,
            state=RequirementState.SATISFIED,
            reason="配置显式声明不要求无偏性前置（deployment.gate.require_unbiasedness=false）",
            value={"required": False},
            source="configs/movie.yaml deployment.gate.require_unbiasedness=false",
        )
    verdict = _field(payload, "verdict")
    if payload is None:
        return RequirementResult(
            name=PREREQUISITE_UNBIASEDNESS,
            state=RequirementState.MISSING,
            reason="无偏性验收结论缺失（前置：未通过或未验收即整体证据不足）",
            value={},
        )
    if verdict is None:
        return RequirementResult(
            name=PREREQUISITE_UNBIASEDNESS,
            state=RequirementState.MISSING,
            reason="无偏性验收结论缺少 verdict 字段（无结论 = 证据不足）",
            value={},
        )
    tau = _field(payload, "tau")
    threshold = _field(payload, "threshold")
    value = {"verdict": str(verdict), "tau": tau, "threshold": threshold}
    passed = str(verdict) == "pass"
    return RequirementResult(
        name=PREREQUISITE_UNBIASEDNESS,
        state=RequirementState.SATISFIED if passed else RequirementState.UNSATISFIED,
        reason=(
            f"无偏性验收通过（τ={tau!r}，阈值 {threshold!r}）"
            if passed
            else f"无偏性验收未通过（verdict={verdict!r}，τ={tau!r}）：回放口径不可信，整体证据不足"
        ),
        value=value,
        source=_source_of(payload, UNBIASEDNESS_SOURCE),
    )


def _reward_result(
    cfg: DeploymentConfig, payload: object, deployed_version: str | None
) -> RequirementResult:
    if deployed_version is None:
        return RequirementResult(
            name=REQUIREMENT_REWARD,
            state=RequirementState.MISSING,
            reason="无现部署版本（deployment.{agent}.current_policy_version 未配置）：无对比对象",
            value={},
        )
    if payload is None:
        return RequirementResult(
            name=REQUIREMENT_REWARD,
            state=RequirementState.MISSING,
            reason="无池化回放对比结果（011 池化口径产物缺失）",
            value={},
        )
    candidate = _field(payload, "candidate")
    deployed = _field(payload, "deployed")
    source = _field(payload, "source")
    for name, value in (("candidate", candidate), ("deployed", deployed)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise DeploymentEvidenceError(f"reward_compare.{name} 必须为有限数值，实际为 {value!r}")
    if not isinstance(source, str) or not source:
        raise DeploymentEvidenceError("reward_compare 必须携带对比产物来源引用（source）")
    value = {"candidate": float(candidate), "deployed": float(deployed)}
    better = float(candidate) > float(deployed)
    return RequirementResult(
        name=REQUIREMENT_REWARD,
        state=RequirementState.SATISFIED if better else RequirementState.UNSATISFIED,
        reason=(
            f"池化回放 reward 对比（011 口径）：候选 {float(candidate):g} "
            f"vs 现部署 {float(deployed):g}"
            + ("，候选更高" if better else "，候选未超过现部署（不臆造优势）")
        ),
        value=value,
        source=source,
    )


def _validation_result(
    cfg: DeploymentConfig,
    rewards: Mapping[str, float] | None,
    candidate_version: str,
    source: str,
) -> RequirementResult:
    if not rewards:
        return RequirementResult(
            name=REQUIREMENT_VALIDATION,
            state=RequirementState.MISSING,
            reason="无 validation 集（005 防过拟合口径的留出树缺失）：无排名可言",
            value={},
        )
    top_ratio = cfg.gate.validation_top_ratio
    rank, total = validation_rank(candidate_version, rewards, top_ratio=top_ratio)
    cutoff = validation_cutoff(total, top_ratio)
    inside = rank <= cutoff
    return RequirementResult(
        name=REQUIREMENT_VALIDATION,
        state=RequirementState.SATISFIED if inside else RequirementState.UNSATISFIED,
        reason=(
            f"validation 排名 {rank}/{total}（前 {top_ratio:.0%}，cutoff={cutoff}）"
            + ("，未跌出线" if inside else "，跌出前 20%：疑似过拟合")
        ),
        value={"rank": rank, "total": total, "top_ratio": top_ratio, "cutoff": cutoff},
        source=source or VALIDATION_SOURCE,
    )


def _drift_result(judge_keys: Sequence[str], registry: RegistryLike) -> RequirementResult:
    if not judge_keys:
        return RequirementResult(
            name=REQUIREMENT_DRIFT,
            state=RequirementState.NOT_APPLICABLE,
            reason=(
                "该 Agent 无 judge 层（漂移要件不适用，口径不适用不等于放宽其他要件；"
                "是否放行由 deployment.gate.allow_without_judge 决定，默认保守）"
            ),
            value={"verdicts": {}},
        )
    if registry is None:
        return RequirementResult(
            name=REQUIREMENT_DRIFT,
            state=RequirementState.MISSING,
            reason="无漂移状态登记（drift/status 缺失）：漂移证据不足",
            value={"judge_keys": list(judge_keys)},
        )
    verdicts = {}
    denied = []
    for key in judge_keys:
        verdict = deploy_evidence_verdict(key, registry)
        verdicts[key] = {"allow": bool(verdict.allow), "reason": verdict.reason}
        if not verdict.allow:
            denied.append(f"{key}：{verdict.reason}")
    return RequirementResult(
        name=REQUIREMENT_DRIFT,
        state=RequirementState.UNSATISFIED if denied else RequirementState.SATISFIED,
        reason=(
            "；".join(denied)
            if denied
            else f"全部相关 judge 版本（{len(judge_keys)} 个）漂移状态允许进入自动部署证据"
        ),
        value={"verdicts": verdicts},
        source=DRIFT_SOURCE,
    )


def collect_evidence(
    agent_id: str,
    candidate_version: str,
    *,
    cfg: DeploymentConfig,
    deployed_version: str | None = None,
    unbiasedness: object = None,
    reward_compare: Mapping | None = None,
    validation_rewards: Mapping[str, float] | None = None,
    validation_source: str = "",
    judge_keys: Sequence[str] = (),
    drift_registry: RegistryLike = None,
    collected_at: str | None = None,
) -> EvidenceBundle:
    """收集候选证据包（契约 C1）。

    - `unbiasedness`：无偏性验收结论（含 `verdict` 的映射/对象；`pass` 才通过）；
    - `reward_compare`：011 池化回放对比结果（`reward_compare_payload` 形态）；
    - `validation_rewards`：validation 池逐版本 reward（排名按 005 口径现场推导）；
    - `judge_keys` / `drift_registry`：相关 judge 版本与 012 状态登记
      （空 `judge_keys` = 该 Agent 无 judge 层）。
    """
    if not agent_id or not candidate_version:
        raise DeploymentEvidenceError("agent_id 与 candidate_version 均必填（证据包归属不可缺）")
    return EvidenceBundle(
        candidate_version=candidate_version,
        agent_id=agent_id,
        deployed_version=deployed_version,
        unbiasedness=_unbiasedness_result(cfg, unbiasedness),
        reward_compare=_reward_result(cfg, reward_compare, deployed_version),
        validation_rank=_validation_result(
            cfg, validation_rewards, candidate_version, validation_source
        ),
        drift_verdict=_drift_result(judge_keys, drift_registry),
        collected_at=collected_at or _now(),
    )


def _field(payload: object, name: str) -> object:
    """从映射或对象取字段（无则该字段为 None——缺失与非法由各自要件规则区分）。"""
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        return payload.get(name)
    return getattr(payload, name, None)


def _source_of(payload: object, default: str) -> str:
    source = _field(payload, "source")
    return source if isinstance(source, str) and source else default
