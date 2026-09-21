"""部署自动化领域模型（功能 014 阶段 2 / T1404，data-model 第一道工序）。

frozen dataclass + 枚举，模型层承载**可机检的硬约束**（在数据产生的最早一刻生效）：
- 判定优先级 `forbidden_agent > insufficient_evidence > blocked > eligible`，且判定与
  逐要件状态必须自洽（放行不能有缺口，拦截不能凭空——理由必须可确定性推导）；
- 模式迁移合法性（`manual ⇄ shadow ⇄ auto`；**manual → auto 禁止直连**）与切换留痕
  （人/时间/理由缺一不可，FR-004）；
- 影子计时非负 + 影子期区间累计（切换即暂停/恢复，跨多次切换累加）；
- 误入率分子 ≤ 分母，且比值与所报数值一致（口径必须可被证伪，SC-007）；
- 留痕类模型（部署/抽检/回滚）人/时间/理由齐全；回滚 `mode_after` 恒为 `manual`
  （宪章原则六：抽检否决必须恢复全人工）。

时间一律 ISO 字符串（与 005/010/012 留痕惯例一致）；本模块零文件 I/O（持久化在
`mode.py` / `gate.py`）。
"""

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from core.evaluators.errors import ValidationError

from .errors import ModeTransitionError


class RequirementState(StrEnum):
    """逐要件状态：满足 / 不满足 / 不适用（无 judge 层）/ 缺失（证据不足）。"""

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    NOT_APPLICABLE = "not_applicable"
    MISSING = "missing"


class GateDecision(StrEnum):
    """门槛判定（优先级即拦截理由的确定性，契约 C2）。"""

    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FORBIDDEN_AGENT = "forbidden_agent"


class DeployMode(StrEnum):
    """部署模式：manual（默认，现状不变）→ shadow（判定照跑、指针不动）→ auto。"""

    MANUAL = "manual"
    SHADOW = "shadow"
    AUTO = "auto"


class HumanDecision(StrEnum):
    """同期人工决策（影子对照的分类输入）。"""

    ADOPT = "adopt"
    REJECT = "reject"
    NONE = "none"


class DiffCategory(StrEnum):
    """影子期与人工决策的差异分类（门槛标定的直接信号）。"""

    SYS_PASS_HUMAN_REJECT = "sys_pass_human_reject"
    HUMAN_PASS_SYS_BLOCK = "human_pass_sys_block"
    AGREE = "agree"
    NO_HUMAN_DECISION = "no_human_decision"


class SpotCheckConclusion(StrEnum):
    """人工抽检结论：通过（留痕，模式保持 auto）/ 否决（三件事同时生效）。"""

    PASS = "pass"
    VETO = "veto"


class SpotCheckTrigger(StrEnum):
    """抽检触发方式（渐进策略）：前 N 次全量 / 之后按比例。"""

    FIRST_N = "first_n"
    RATIO = "ratio"


class RollbackTrigger(StrEnum):
    """回滚触发源：抽检否决 / 部署后漂移评估 / 人工。"""

    SPOT_CHECK_VETO = "spot_check_veto"
    DRIFT_ASSESSMENT = "drift_assessment"
    MANUAL = "manual"


# 判定优先级（数值越小越优先）：禁止名单 > 证据不足 > 要件不满足 > 放行
DECISION_PRIORITY = {
    GateDecision.FORBIDDEN_AGENT: 0,
    GateDecision.INSUFFICIENT_EVIDENCE: 1,
    GateDecision.BLOCKED: 2,
    GateDecision.ELIGIBLE: 3,
}

# 要件名（前置 = 无偏性；三要件 = reward 对比 / validation 排名 / 漂移 verdict）
PREREQUISITE_UNBIASEDNESS = "unbiasedness"
REQUIREMENT_REWARD = "reward_compare"
REQUIREMENT_VALIDATION = "validation_rank"
REQUIREMENT_DRIFT = "drift_verdict"
REQUIREMENTS = (REQUIREMENT_REWARD, REQUIREMENT_VALIDATION, REQUIREMENT_DRIFT)


def most_severe_decision(*decisions: GateDecision) -> GateDecision:
    """取最严重判定（多理由并存时判定取优先级最高者，理由在 GateVerdict.reason 中列全）。"""
    if not decisions:
        raise ValidationError("most_severe_decision 至少需要一个判定")
    return min(
        (_as_enum("decision", decision, GateDecision) for decision in decisions),
        key=lambda decision: DECISION_PRIORITY[decision],
    )


# --- 字段校验助手（与 core/calibration/drift_models.py 同风格） --------------


def _require_non_empty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} 必须为非空字符串，实际为 {value!r}")
    return value


def _require_optional_non_empty(name: str, value: object) -> str:
    """可选字段：None/空串放行，非空但非法形态（如非字符串）即拒。"""
    if value is None or value == "":
        return ""
    return _require_non_empty(name, value)


def _require_bool(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{name} 必须为布尔值，实际为 {value!r}")
    return value


def _require_field_dict(name: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise ValidationError(f"{name} 必须为映射（取值与来源自描述），实际为 {value!r}")
    return dict(value)


def _require_choice(name: str, value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"{name} 必须为 {list(allowed)} 之一，实际为 {value!r}")
    return value


def _as_enum(name: str, value: object, enum_type) -> object:
    """枚举取值归一：接受枚举实例或字符串字面量（非法即拒，不静默兜底）。"""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise ValidationError(
                f"{name} 必须为 {enum_type.__name__} 取值（{list(enum_type)}），实际为 {value!r}"
            ) from exc
    raise ValidationError(f"{name} 必须为 {enum_type.__name__}，实际为 {type(value).__name__}")


# --- 逐要件结果与证据包 -----------------------------------------------------


@dataclass(frozen=True)
class RequirementResult:
    """单个要件的结果：状态 + 取值 + 口径来源引用 + 理由（审计与复算的最小单元）。

    - `satisfied` / `unsatisfied` 必须携带来源引用（有结论必有出处）；
    - `missing` / `not_applicable` 必须说明理由（不静默、不空白留痕）。
    """

    name: str
    state: RequirementState
    reason: str
    value: dict = field(default_factory=dict)
    source: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("name", self.name)
        object.__setattr__(self, "state", _as_enum("state", self.state, RequirementState))
        _require_non_empty("reason", self.reason)
        object.__setattr__(self, "value", _require_field_dict("value", self.value))
        if self.state in (RequirementState.SATISFIED, RequirementState.UNSATISFIED):
            _require_non_empty("source", self.source)
        else:
            _require_optional_non_empty("source", self.source)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "reason": self.reason,
            "value": self.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    """候选策略的证据包（契约 C1）：前置 = 无偏性验收；三要件 = reward/validation/drift。"""

    candidate_version: str
    agent_id: str
    deployed_version: str | None
    unbiasedness: RequirementResult
    reward_compare: RequirementResult
    validation_rank: RequirementResult
    drift_verdict: RequirementResult
    collected_at: str

    def __post_init__(self) -> None:
        for name in ("candidate_version", "agent_id", "collected_at"):
            _require_non_empty(name, getattr(self, name))
        _require_optional_non_empty("deployed_version", self.deployed_version)
        expected = (
            ("unbiasedness", PREREQUISITE_UNBIASEDNESS, self.unbiasedness),
            (REQUIREMENT_REWARD, REQUIREMENT_REWARD, self.reward_compare),
            (REQUIREMENT_VALIDATION, REQUIREMENT_VALIDATION, self.validation_rank),
            (REQUIREMENT_DRIFT, REQUIREMENT_DRIFT, self.drift_verdict),
        )
        for field_name, expected_name, item in expected:
            if not isinstance(item, RequirementResult):
                raise ValidationError(f"{field_name} 必须为 RequirementResult")
            if item.name != expected_name:
                raise ValidationError(
                    f"{field_name} 要件名不匹配：期望 {expected_name!r}，实际 {item.name!r}"
                )

    @property
    def requirements(self) -> tuple[RequirementResult, ...]:
        """三要件（不含前置）。"""
        return (self.reward_compare, self.validation_rank, self.drift_verdict)

    def requirement(self, name: str) -> RequirementResult:
        """按要件名取结果（前置与三要件统一入口）。"""
        for item in (self.unbiasedness, *self.requirements):
            if item.name == name:
                return item
        raise ValidationError(f"证据包中无要件 {name!r}")

    def to_dict(self) -> dict:
        return {
            "candidate_version": self.candidate_version,
            "agent_id": self.agent_id,
            "deployed_version": self.deployed_version,
            "unbiasedness": self.unbiasedness.to_dict(),
            "reward_compare": self.reward_compare.to_dict(),
            "validation_rank": self.validation_rank.to_dict(),
            "drift_verdict": self.drift_verdict.to_dict(),
            "collected_at": self.collected_at,
        }


@dataclass(frozen=True)
class GateVerdict:
    """门槛判定（契约 C2）：判定 + 逐要件结果 + 理由 + 时间。

    判定与要件状态的一致性在构造期强制（放行 = 前置 + 三要件同时满足；缺证据 ≠ 不满足）。
    """

    decision: GateDecision
    agent_id: str
    candidate_version: str
    prerequisite: RequirementResult
    requirements: tuple[RequirementResult, ...]
    reason: str
    decided_at: str
    allow_without_judge: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", _as_enum("decision", self.decision, GateDecision))
        _require_bool("allow_without_judge", self.allow_without_judge)
        for name in ("agent_id", "candidate_version", "reason", "decided_at"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.prerequisite, RequirementResult):
            raise ValidationError("prerequisite 必须为 RequirementResult（前置无偏性）")
        if self.prerequisite.name != PREREQUISITE_UNBIASEDNESS:
            raise ValidationError(
                f"prerequisite 必须为 {PREREQUISITE_UNBIASEDNESS!r}，"
                f"实际为 {self.prerequisite.name!r}"
            )
        object.__setattr__(self, "requirements", tuple(self.requirements))
        names = tuple(item.name for item in self.requirements)
        if names != REQUIREMENTS:
            raise ValidationError(
                f"requirements 必须恰为三要件 {list(REQUIREMENTS)}，实际为 {list(names)}"
            )
        for item in self.requirements:
            if not isinstance(item, RequirementResult):
                raise ValidationError("requirements 元素必须为 RequirementResult")

        satisfied_all = all(item.state is RequirementState.SATISFIED for item in self.requirements)
        unmet = [
            item.name
            for item in self.requirements
            if item.state in (RequirementState.UNSATISFIED, RequirementState.MISSING)
        ]
        inapplicable = [
            item.name for item in self.requirements if item.state is RequirementState.NOT_APPLICABLE
        ]
        prereq_state = self.prerequisite.state
        if self.decision is GateDecision.ELIGIBLE:
            blocked_by_na = bool(inapplicable) and not self.allow_without_judge
            if prereq_state is not RequirementState.SATISFIED or unmet or blocked_by_na:
                raise ValidationError(
                    "eligible 判定必须前置通过且三要件同时满足"
                    f"（前置={prereq_state.value}，未满足={unmet or '无'}，"
                    f"不适用={inapplicable or '无'}，"
                    f"allow_without_judge={self.allow_without_judge}）"
                )
        elif self.decision is GateDecision.INSUFFICIENT_EVIDENCE:
            missing = [
                item.name for item in self.requirements if item.state is RequirementState.MISSING
            ]
            if prereq_state is RequirementState.SATISFIED and not missing:
                raise ValidationError(
                    "insufficient_evidence 必须由前置失败（unsatisfied/missing）或要件 missing 支撑"
                )
        elif self.decision is GateDecision.BLOCKED:
            missing = [
                item.name for item in self.requirements if item.state is RequirementState.MISSING
            ]
            if missing or prereq_state is RequirementState.MISSING:
                raise ValidationError(
                    f"blocked 必须建立在证据齐备之上（缺证据走 insufficient_evidence：{missing}）"
                )
            if satisfied_all:
                raise ValidationError("blocked 判定必须存在未满足要件（全体满足却判 blocked）")

    @property
    def allow(self) -> bool:
        """是否放行自动部署（唯一判据 = 判定为 eligible；其余一律拦截）。"""
        return self.decision is GateDecision.ELIGIBLE

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "agent_id": self.agent_id,
            "candidate_version": self.candidate_version,
            "prerequisite": self.prerequisite.to_dict(),
            "requirements": [item.to_dict() for item in self.requirements],
            "reason": self.reason,
            "decided_at": self.decided_at,
            "allow_without_judge": self.allow_without_judge,
        }


@dataclass(frozen=True)
class EvidenceSnapshot:
    """一次判定的不可改写记录（契约 C3）：证据包 + 判定 + 指纹。

    `fingerprint` 不含时间字段——同证据同判定 → 同指纹（幂等落盘的依据）。
    """

    snapshot_id: str
    agent_id: str
    candidate_version: str
    fingerprint: str
    bundle: EvidenceBundle
    verdict: GateVerdict
    created_at: str

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "agent_id", "candidate_version", "fingerprint", "created_at"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.bundle, EvidenceBundle):
            raise ValidationError("bundle 必须为 EvidenceBundle")
        if not isinstance(self.verdict, GateVerdict):
            raise ValidationError("verdict 必须为 GateVerdict")
        if self.bundle.candidate_version != self.candidate_version:
            raise ValidationError("candidate_version 与证据包不一致（快照不得张冠李戴）")
        if self.bundle.agent_id != self.agent_id:
            raise ValidationError("agent_id 与证据包不一致（快照不得张冠李戴）")

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "agent_id": self.agent_id,
            "candidate_version": self.candidate_version,
            "fingerprint": self.fingerprint,
            "bundle": self.bundle.to_dict(),
            "verdict": self.verdict.to_dict(),
            "created_at": self.created_at,
        }


# --- 模式状态机 -------------------------------------------------------------


_MODE_TRANSITIONS: dict[DeployMode, tuple[DeployMode, ...]] = {
    DeployMode.MANUAL: (DeployMode.SHADOW,),
    DeployMode.SHADOW: (DeployMode.MANUAL, DeployMode.AUTO),
    DeployMode.AUTO: (DeployMode.MANUAL, DeployMode.SHADOW),
}


def _elapsed_days(since: str, until: str, *, name: str) -> float:
    """两个 ISO 时刻之间的天数（影子期区间累计；负区间即拒绝——时间不可倒流）。"""
    try:
        start = datetime.fromisoformat(since)
        end = datetime.fromisoformat(until)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} 必须为 ISO 时间字符串：{since!r} / {until!r}") from exc
    days = (end - start).total_seconds() / 86400.0
    if days < 0:
        raise ValidationError(f"影子期区间为负（{since} → {until}）：计时不可倒流")
    return days


@dataclass(frozen=True)
class DeployModeState:
    """部署模式状态（契约 C4）：当前模式 + 变更历史 + 影子期累计 + 重标定标记。

    影子期计时 = **模式区间累计**（进入 shadow 开始计时，切出暂停，再入继续累加）；
    变更历史只增不改（末条 = 当前态）；`recalibration_required=true` 时 auto 一律拒绝
    （由 `mode.set_mode` 执行禁止，模型只承载标记与理由）。
    """

    current: DeployMode
    history: tuple = ()
    shadow_days_accumulated: float = 0.0
    shadow_candidate_count: int = 0
    recalibration_required: bool = False
    recalibration_reason: str = ""
    shadow_since: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", _as_enum("mode", self.current, DeployMode))
        if not isinstance(self.history, (tuple, list)) or not self.history:
            raise ValidationError("history 必须为非空的模式变更历史（mode/since/by/reason）")
        entries = []
        for entry in self.history:
            if not isinstance(entry, dict):
                raise ValidationError("history 条目必须为 {mode, since, by, reason} 映射")
            normalized = {
                "mode": _as_enum("mode", entry.get("mode"), DeployMode).value,
                "since": _require_non_empty("since", entry.get("since")),
                "by": _require_non_empty("by", entry.get("by")),
                "reason": _require_non_empty("reason", entry.get("reason")),
            }
            entries.append(normalized)
        if entries[-1]["mode"] != self.current.value:
            raise ValidationError(
                "history 末条必须为当前模式"
                f"（{self.current.value}），实际为 {entries[-1]['mode']!r}"
            )
        object.__setattr__(self, "history", tuple(entries))

        days = self.shadow_days_accumulated
        if (
            isinstance(days, bool)
            or not isinstance(days, (int, float))
            or not math.isfinite(days)
            or days < 0
        ):
            raise ValidationError(
                f"shadow_days_accumulated 必须为 ≥ 0 的有限数值（影子计时非负），实际为 {days!r}"
            )
        object.__setattr__(self, "shadow_days_accumulated", float(days))

        count = self.shadow_candidate_count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValidationError(
                f"shadow_candidate_count 必须为 ≥ 0 的整数（候选计数非负），实际为 {count!r}"
            )
        _require_bool("recalibration_required", self.recalibration_required)
        if self.recalibration_required:
            _require_non_empty("recalibration_reason", self.recalibration_reason)
        else:
            _require_optional_non_empty("recalibration_reason", self.recalibration_reason)

        if self.current is DeployMode.SHADOW:
            _require_non_empty("shadow_since", self.shadow_since)
        elif self.shadow_since is not None:
            raise ValidationError(
                f"shadow_since 仅在 shadow 模式携带（当前为 {self.current.value}：非影子期不计时）"
            )

    @classmethod
    def initial(
        cls,
        mode: DeployMode,
        *,
        at: str,
        by: str = "system",
        reason: str = "初始模式（configs deployment.mode_default）",
    ) -> "DeployModeState":
        """初始态（configs 的 mode_default）；未发生切换时只读不落盘。"""
        mode = _as_enum("mode", mode, DeployMode)
        return cls(
            current=mode,
            history=({"mode": mode.value, "since": at, "by": by, "reason": reason},),
            shadow_since=at if mode is DeployMode.SHADOW else None,
        )

    def ensure_transition(self, to: DeployMode) -> DeployMode:
        """迁移合法性校验（返回归一后的目标态）；非法迁移 → ModeTransitionError。"""
        to = _as_enum("mode", to, DeployMode)
        if to not in _MODE_TRANSITIONS[self.current]:
            detail = (
                "manual → auto 禁止直连（须先经 shadow 并满足影子期下限）"
                if self.current is DeployMode.MANUAL and to is DeployMode.AUTO
                else f"允许的目标态：{[mode.value for mode in _MODE_TRANSITIONS[self.current]]}"
            )
            raise ModeTransitionError(f"非法模式迁移 {self.current.value} → {to.value}：{detail}")
        return to

    def transition(self, to: DeployMode, *, at: str, by: str, reason: str) -> "DeployModeState":
        """执行迁移（frozen：产出新实例，原对象不变）：校验 + 影子期区间累计 + 变更留痕。"""
        to = self.ensure_transition(to)
        _require_non_empty("since", at)
        _require_non_empty("by", by)
        _require_non_empty("reason", reason)
        days = self.shadow_days_accumulated
        if self.current is DeployMode.SHADOW:
            days += _elapsed_days(self.shadow_since, at, name="shadow_since")
        return replace(
            self,
            current=to,
            history=(
                *self.history,
                {"mode": to.value, "since": at, "by": by, "reason": reason},
            ),
            shadow_days_accumulated=days,
            shadow_since=at if to is DeployMode.SHADOW else None,
        )

    def record_shadow_candidate(self) -> "DeployModeState":
        """影子期候选计数 +1（只在 shadow 期计数——非影子期计数会污染影子证据）。"""
        if self.current is not DeployMode.SHADOW:
            raise ValidationError(
                f"当前模式 {self.current.value} 非 shadow：不记录影子候选数"
                "（影子期证据只在 shadow 模式产生）"
            )
        return replace(self, shadow_candidate_count=self.shadow_candidate_count + 1)

    def with_elapsed_shadow(self, at: str) -> "DeployModeState":
        """把当前影子区间的已过时长结算进累计值（门禁判据用；不改原对象）。

        影子期计时是"模式区间累计"，门禁判定发生在**切出之前**——必须在判定时点把正在
        进行的这一区间结算进去，否则永远读不到"已跑够 14 天"。
        """
        if self.current is not DeployMode.SHADOW:
            return self
        days = self.shadow_days_accumulated + _elapsed_days(
            self.shadow_since, at, name="shadow_since"
        )
        return replace(self, shadow_days_accumulated=days)

    def shadow_window_gap(self, *, min_days: float, min_candidates: int) -> str:
        """影子期双下限缺口说明（"" = 满足）；缺口必须可读（FR-006 机检门禁）。"""
        gaps = []
        if self.shadow_days_accumulated < min_days:
            gaps.append(f"累计时长 {self.shadow_days_accumulated:g} 天 < 下限 {min_days:g} 天")
        if self.shadow_candidate_count < min_candidates:
            gaps.append(f"覆盖候选 {self.shadow_candidate_count} 个 < 下限 {min_candidates} 个")
        if not gaps:
            return ""
        return "影子期未满（缺口：" + "；".join(gaps) + "）"

    def set_recalibration(self, required: bool, reason: str = "") -> "DeployModeState":
        """置/清"门槛需重新标定"标记（置时必须有来源理由）。"""
        return replace(
            self,
            recalibration_required=required,
            recalibration_reason=reason if required else "",
        )

    def reset_shadow_window(self, *, at: str | None = None) -> "DeployModeState":
        """影子期重新计时（重标定完成后必须重跑影子验证，FR-009）。"""
        return replace(
            self,
            shadow_days_accumulated=0.0,
            shadow_candidate_count=0,
            shadow_since=at if self.current is DeployMode.SHADOW else None,
        )

    def to_dict(self) -> dict:
        return {
            "current": self.current.value,
            "history": [dict(entry) for entry in self.history],
            "shadow_days_accumulated": self.shadow_days_accumulated,
            "shadow_candidate_count": self.shadow_candidate_count,
            "recalibration_required": self.recalibration_required,
            "recalibration_reason": self.recalibration_reason,
            "shadow_since": self.shadow_since,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "DeployModeState":
        return cls(
            current=payload.get("current"),
            history=tuple(payload.get("history") or ()),
            shadow_days_accumulated=payload.get("shadow_days_accumulated", 0.0),
            shadow_candidate_count=payload.get("shadow_candidate_count", 0),
            recalibration_required=payload.get("recalibration_required", False),
            recalibration_reason=payload.get("recalibration_reason", ""),
            shadow_since=payload.get("shadow_since"),
        )


# --- 影子事件与对照报告 -----------------------------------------------------


@dataclass(frozen=True)
class ShadowEvent:
    """影子期单个候选的判定记录（契约 C5）：若 auto 会放行与否 + 同期人工决策差异分类。

    `unacceptable`：事后人工复核判定"即便自动接班也不可接受"（影子期无真实部署，这是
    误入率分子第二项的唯一来源——不能靠"未部署所以没问题"推断）。
    """

    period: str
    agent_id: str
    candidate_version: str
    verdict: GateDecision
    would_allow: bool
    human_decision: HumanDecision
    diff_category: DiffCategory
    reason: str
    recorded_at: str
    unacceptable: bool = False

    def __post_init__(self) -> None:
        for name in ("period", "agent_id", "candidate_version", "reason", "recorded_at"):
            _require_non_empty(name, getattr(self, name))
        _require_bool("unacceptable", self.unacceptable)
        object.__setattr__(self, "verdict", _as_enum("verdict", self.verdict, GateDecision))
        object.__setattr__(
            self, "human_decision", _as_enum("human_decision", self.human_decision, HumanDecision)
        )
        object.__setattr__(
            self, "diff_category", _as_enum("diff_category", self.diff_category, DiffCategory)
        )
        _require_bool("would_allow", self.would_allow)
        expected_allow = self.verdict is GateDecision.ELIGIBLE
        if self.would_allow is not expected_allow:
            raise ValidationError(
                f"would_allow 必须由判定推导（verdict={self.verdict.value} → "
                f"would_allow={expected_allow}），实际为 {self.would_allow}"
            )
        expected_category = self.classify(self.would_allow, self.human_decision)
        if self.diff_category is not expected_category:
            raise ValidationError(
                "diff_category 必须由（若 auto 放行与否 × 人工决策）推导：期望 "
                f"{expected_category.value}，实际 {self.diff_category.value}"
            )

    @staticmethod
    def classify(would_allow: bool, human_decision: HumanDecision) -> DiffCategory:
        """差异分类（四类）：系统放行人拒 / 人放行系统拦 / 一致 / 无人工决策。"""
        decision = _as_enum("human_decision", human_decision, HumanDecision)
        if decision is HumanDecision.NONE:
            return DiffCategory.NO_HUMAN_DECISION
        if would_allow and decision is HumanDecision.REJECT:
            return DiffCategory.SYS_PASS_HUMAN_REJECT
        if not would_allow and decision is HumanDecision.ADOPT:
            return DiffCategory.HUMAN_PASS_SYS_BLOCK
        return DiffCategory.AGREE

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "agent_id": self.agent_id,
            "candidate_version": self.candidate_version,
            "verdict": self.verdict.value,
            "would_allow": self.would_allow,
            "human_decision": self.human_decision.value,
            "diff_category": self.diff_category.value,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
            "unacceptable": self.unacceptable,
        }


@dataclass(frozen=True)
class ShadowReport:
    """影子期对照报告（契约 C5）：放行/拦截数 + 理由分布 + 差异分类 + 误入率分子分母。

    数字必须自洽：放行 + 拦截 = 候选数；差异分类计数合计 = 候选数；误入率 = 分子/分母
    （分母为 0 时如实标注 `None`——不伪造 0）。
    """

    period: str
    agent_id: str
    shadow_days: float
    candidate_count: int
    passes: int
    blocks: int
    reason_distribution: dict
    diff_counts: dict
    misadmission_numerator: int
    misadmission_denominator: int
    misadmission_rate: float | None
    met: bool
    note: str
    generated_at: str

    def __post_init__(self) -> None:
        for name in ("period", "agent_id", "generated_at"):
            _require_non_empty(name, getattr(self, name))
        for name in ("candidate_count", "passes", "blocks"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"{name} 必须为 ≥ 0 的整数，实际为 {value!r}")
        if self.passes + self.blocks != self.candidate_count:
            raise ValidationError(
                f"passes + blocks 必须等于 candidate_count（{self.passes}+{self.blocks} "
                f"!= {self.candidate_count}）"
            )
        days = self.shadow_days
        if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 0:
            raise ValidationError(f"shadow_days 必须为 ≥ 0 的数值，实际为 {days!r}")
        diff = _require_field_dict("diff_counts", self.diff_counts)
        for category in DiffCategory:
            diff.setdefault(category.value, 0)
        for key, value in diff.items():
            _require_choice("diff_counts 分类", key, tuple(item.value for item in DiffCategory))
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"diff_counts[{key}] 必须为 ≥ 0 的整数，实际为 {value!r}")
        if sum(diff.values()) != self.candidate_count:
            raise ValidationError(
                f"diff_counts 合计必须等于 candidate_count（{sum(diff.values())} != "
                f"{self.candidate_count}）"
            )
        object.__setattr__(self, "diff_counts", diff)
        object.__setattr__(
            self,
            "reason_distribution",
            _require_field_dict("reason_distribution", self.reason_distribution),
        )
        for name in ("misadmission_numerator", "misadmission_denominator"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"{name} 必须为 ≥ 0 的整数，实际为 {value!r}")
        if self.misadmission_numerator > self.misadmission_denominator:
            raise ValidationError(
                "misadmission_numerator 不得大于 misadmission_denominator"
                "（分母 = 放行/自动部署总数，分子 = 其中被否定的数）："
                f"{self.misadmission_numerator} > {self.misadmission_denominator}"
            )
        expected_rate = (
            None
            if self.misadmission_denominator == 0
            else self.misadmission_numerator / self.misadmission_denominator
        )
        if expected_rate is None:
            if self.misadmission_rate is not None:
                raise ValidationError(
                    "misadmission_denominator=0 时 misadmission_rate 必须为 None"
                    "（无样本不伪造比率）"
                )
        elif self.misadmission_rate is None or abs(self.misadmission_rate - expected_rate) > 1e-9:
            raise ValidationError(
                "misadmission_rate 必须等于分子/分母（口径可被证伪）：期望 "
                f"{expected_rate}，实际 {self.misadmission_rate!r}"
            )
        _require_bool("met", self.met)
        _require_optional_non_empty("note", self.note)

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "agent_id": self.agent_id,
            "shadow_days": self.shadow_days,
            "candidate_count": self.candidate_count,
            "passes": self.passes,
            "blocks": self.blocks,
            "reason_distribution": self.reason_distribution,
            "diff_counts": self.diff_counts,
            "misadmission_numerator": self.misadmission_numerator,
            "misadmission_denominator": self.misadmission_denominator,
            "misadmission_rate": self.misadmission_rate,
            "met": self.met,
            "note": self.note,
            "generated_at": self.generated_at,
        }


# --- 部署 / 抽检 / 回滚留痕 -------------------------------------------------


@dataclass(frozen=True)
class AutoDeployEvent:
    """自动部署留痕（契约 C6~C7）：指针改写前后值必须与候选/现部署版本一致。

    谱系 `source=auto`（原则二）；本模型不含历史节点的任何写入——部署只是指针切换。
    """

    agent_id: str
    candidate_version: str
    from_version: str
    evidence_snapshot: str
    deployed_at: str
    pointer_before: str
    pointer_after: str
    reason: str
    source: str = "auto"

    def __post_init__(self) -> None:
        for name in (
            "agent_id",
            "candidate_version",
            "from_version",
            "evidence_snapshot",
            "deployed_at",
            "pointer_before",
            "pointer_after",
            "reason",
        ):
            _require_non_empty(name, getattr(self, name))
        _require_choice("source", self.source, ("auto",))
        if self.pointer_after != self.candidate_version:
            raise ValidationError(
                f"pointer_after 必须等于候选版本（{self.pointer_after!r} != "
                f"{self.candidate_version!r}）：部署留痕不得与指针去向不符"
            )
        if self.from_version != self.pointer_before:
            raise ValidationError(
                f"pointer_before 必须等于现部署版本（{self.pointer_before!r} != "
                f"{self.from_version!r}）：留痕与指针不符"
            )

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "candidate_version": self.candidate_version,
            "from_version": self.from_version,
            "evidence_snapshot": self.evidence_snapshot,
            "deployed_at": self.deployed_at,
            "pointer_before": self.pointer_before,
            "pointer_after": self.pointer_after,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True)
class SpotCheckRecord:
    """人工事后抽检留痕（契约 C8）：覆盖的部署事件 + 触发方式 + 结论 + 人/时间/理由。"""

    agent_id: str
    deploy_event: str
    seq: int
    trigger: SpotCheckTrigger
    conclusion: SpotCheckConclusion
    by: str
    at: str
    reason: str

    def __post_init__(self) -> None:
        for name in ("agent_id", "deploy_event", "by", "at", "reason"):
            _require_non_empty(name, getattr(self, name))
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise ValidationError(f"seq 必须为 ≥ 1 的整数（部署序号），实际为 {self.seq!r}")
        object.__setattr__(self, "trigger", _as_enum("trigger", self.trigger, SpotCheckTrigger))
        object.__setattr__(
            self, "conclusion", _as_enum("conclusion", self.conclusion, SpotCheckConclusion)
        )

    @property
    def vetoed(self) -> bool:
        """是否否决（误入率分子的取数口径：生产期 = 抽检否决数 + 回滚评估判定的误放行数）。"""
        return self.conclusion is SpotCheckConclusion.VETO

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "deploy_event": self.deploy_event,
            "seq": self.seq,
            "trigger": self.trigger.value,
            "conclusion": self.conclusion.value,
            "by": self.by,
            "at": self.at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RollbackEvent:
    """回滚留痕（契约 C9/C10）：回滚后模式恒为 manual（抽检否决三件事之一）。"""

    agent_id: str
    from_version: str
    to_version: str
    trigger: RollbackTrigger
    at: str
    mode_after: DeployMode
    recalibration_required: bool
    by: str
    reason: str
    note: str = ""

    def __post_init__(self) -> None:
        for name in ("agent_id", "from_version", "to_version", "at", "by", "reason"):
            _require_non_empty(name, getattr(self, name))
        object.__setattr__(self, "trigger", _as_enum("trigger", self.trigger, RollbackTrigger))
        object.__setattr__(self, "mode_after", _as_enum("mode_after", self.mode_after, DeployMode))
        if self.mode_after is not DeployMode.MANUAL:
            raise ValidationError(
                "mode_after 必须为 manual（回滚即恢复全人工审批），实际为"
                f" {self.mode_after.value!r}"
            )
        if self.to_version == self.from_version:
            raise ValidationError(
                f"回滚必须换版本：from_version 与 to_version 均为 {self.from_version!r}"
            )
        _require_bool("recalibration_required", self.recalibration_required)
        _require_optional_non_empty("note", self.note)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "trigger": self.trigger.value,
            "at": self.at,
            "mode_after": self.mode_after.value,
            "recalibration_required": self.recalibration_required,
            "by": self.by,
            "reason": self.reason,
            "note": self.note,
        }
