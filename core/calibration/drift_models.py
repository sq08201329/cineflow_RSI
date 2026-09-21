"""漂移监控领域模型（frozen dataclass，data-model.md §领域模型；功能 012）。

- DriftBaseline / DriftMetrics：检测口径的输入与产出（口径版本 detector_version 进记录，
  历史判定不被回溯改写——原则一）；
- DriftStatus：状态机 `normal → suspect → confirmed_drift | false_alarm → normal`。
  **模型层强制"系统只写 suspect"**（原则六机检落点 SC-002）：终态（confirmed_drift /
  false_alarm）的构造与迁移必须携带人工处置留痕引用（disposition_ref）+ `by_human=True`，
  系统路径只能写 suspect（且必须携带触发指标引用 trigger_metrics）；
- DriftDisposition：人工处置留痕（人/时间/理由/动作），构造即冻结、只增不改；
- DriftReport / DeployEvidenceVerdict：周期报表形态与 F9 部署证据接口返回形态。

校验风格与 010 `core/calibration/models.py` 一致（ValidationError 复用 core/evaluators）。
"""

import math
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from core.evaluators.errors import ValidationError

# 分位点口径（与 010 快照 quantiles 键一致，p25/p50/p75/p90）
QUANTILE_NAMES = ("p25", "p50", "p75", "p90")
# 口径版本格式：drift_detector@语义版本+{算法与阈值哈希前 12 位}（哈希长度 12~64）
_DETECTOR_VERSION_RE = re.compile(r"^drift_detector@\d+\.\d+\.\d+\+[0-9a-f]{12,64}$")

_TOLERANCE = 1e-6


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} 必须为非空字符串，实际为 {value!r}")


def _require_optional_non_empty(name: str, value: object) -> None:
    if value is None:
        return
    _require_non_empty(name, value)


def _require_detector_version(value: object) -> None:
    if not isinstance(value, str) or not _DETECTOR_VERSION_RE.match(value):
        raise ValidationError(
            f"detector_version 必须为 drift_detector@语义版本+哈希 形态（12~64 位小写十六进制），"
            f"实际为 {value!r}"
        )


def _require_int(name: str, value: object, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValidationError(f"{name} 必须为 ≥ {minimum} 的整数，实际为 {value!r}")


def _require_number(
    name: str, value: object, *, minimum: float | None = None, maximum: float | None = None
) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValidationError(f"{name} 必须为有限数值，实际为 {value!r}")
    if minimum is not None and value < minimum:
        raise ValidationError(f"{name} 必须 ≥ {minimum}，实际为 {value!r}")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{name} 必须 ≤ {maximum}，实际为 {value!r}")


def _as_enum(name: str, value: object, enum_cls: type[StrEnum]) -> StrEnum:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ValidationError(f"{name} 必须为 {[item.value for item in enum_cls]} 之一") from exc


class DriftVerdict(StrEnum):
    """一次检测的判定：判定类（normal/drift）与如实标注类（insufficient/no_baseline/no_data）。

    判定类必须携带两维指标（psi + 分位数偏移）；标注类不得携带指标（不硬判、不伪造结论）。
    """

    NORMAL = "normal"
    DRIFT = "drift"
    INSUFFICIENT = "insufficient"
    NO_BASELINE = "no_baseline"
    NO_DATA = "no_data"


JUDGED_VERDICTS = (DriftVerdict.NORMAL, DriftVerdict.DRIFT)


class DriftState(StrEnum):
    """评估器版本的漂移状态；系统自动写入仅限 suspect（终态由人工处置写入）。"""

    NORMAL = "normal"
    SUSPECT = "suspect"
    CONFIRMED_DRIFT = "confirmed_drift"
    FALSE_ALARM = "false_alarm"


class DriftConclusion(StrEnum):
    """人工处置结论。"""

    CONFIRMED_DRIFT = "confirmed_drift"
    FALSE_ALARM = "false_alarm"


class DriftAction(StrEnum):
    """人工处置动作：停用该版本 / 换锚点升版（新版本独立基线）/ 误报恢复。"""

    DEACTIVATE = "deactivate"
    REANCHOR = "reanchor"
    RESTORE = "restore"


@dataclass(frozen=True)
class DriftBaseline:
    """滑动窗口基线：最近 N 周期（不含当前）的分桶合并分布 + 分位数 + 样本量。

    - periods：窗口内周期标签（升序，全部有快照；缺口周期跳过不插值）；
    - buckets：窗口内分桶合并分布（占比 ∈ [0,1]，非空窗口占比和 = 1）；
    - quantiles：p25/p50/p75/p90（[0,1]）；
    - detector_version：构成该基线的口径版本（口径升级即新基线）；
    - dropped_periods：窗口内**因评估器升版被切分**的周期（台账记录版本与当前版本不同，
      不计入合并——版本冻结；仅作如实标注，不影响合并口径）。
    """

    evaluator_key: str
    agent_id: str
    periods: tuple = ()
    buckets: tuple = ()
    quantiles: dict = field(default_factory=dict)
    samples: int = 0
    detector_version: str = ""
    dropped_periods: tuple = ()

    def __post_init__(self) -> None:
        _require_non_empty("evaluator_key", self.evaluator_key)
        _require_non_empty("agent_id", self.agent_id)
        _require_detector_version(self.detector_version)

        if not isinstance(self.periods, (tuple, list)) or not self.periods:
            raise ValidationError("periods 必须为非空周期序列（滑动窗口内的周期标签）")
        for period in self.periods:
            _require_non_empty("periods", period)
        object.__setattr__(self, "periods", tuple(self.periods))

        if not isinstance(self.dropped_periods, (tuple, list)):
            raise ValidationError("dropped_periods 必须为周期标签元组（可为空）")
        for period in self.dropped_periods:
            _require_non_empty("dropped_periods", period)
        object.__setattr__(self, "dropped_periods", tuple(self.dropped_periods))

        if not isinstance(self.buckets, (tuple, list)) or not self.buckets:
            raise ValidationError("buckets 必须为非空分桶占比序列")
        for share in self.buckets:
            _require_number("buckets", share, minimum=0.0, maximum=1.0)
        total = sum(self.buckets)
        # 非空窗口占比和为 1；全空分布（样本量为 0 的退化窗口）允许全 0
        if not (abs(total) <= _TOLERANCE or abs(total - 1.0) <= _TOLERANCE):
            raise ValidationError(f"buckets 占比和必须为 1（或全 0），实际为 {total!r}")
        object.__setattr__(self, "buckets", tuple(float(share) for share in self.buckets))

        if not isinstance(self.quantiles, dict) or set(self.quantiles) != set(QUANTILE_NAMES):
            actual = sorted(self.quantiles) if isinstance(self.quantiles, dict) else self.quantiles
            raise ValidationError(
                f"quantiles 必须恰好包含 {list(QUANTILE_NAMES)} 四个分位点，实际为 {actual!r}"
            )
        for name, value in self.quantiles.items():
            _require_number(f"quantiles.{name}", value, minimum=0.0, maximum=1.0)

        _require_int("samples", self.samples, minimum=0)

    @property
    def period_range(self) -> str:
        """窗口周期区间标注（首..末），供基线引用（baseline_ref）使用。"""
        return f"{self.periods[0]}..{self.periods[-1]}"


@dataclass(frozen=True)
class DriftMetrics:
    """一次漂移检测的记录（一份 JSON 落 calibration/drift/metrics/...）。

    - verdict ∈ DriftVerdict；判定类必须携带 psi + quantile_shifts + baseline_ref，
      标注类（insufficient/no_baseline/no_data）不得携带指标——"不伪造结论"的模型层约束；
    - thresholds：本次判定所用阈值快照（口径自描述：psi/quantile > 0，min_samples/window ≥ 1）；
    - note：如实标注（首周期无基线 / 样本不足 / 序列缺口 / 触发指标）——只陈述不判断。
    """

    evaluator_key: str
    agent_id: str
    period: str
    verdict: DriftVerdict
    samples: int
    detector_version: str
    psi: float | None = None
    quantile_shifts: dict = field(default_factory=dict)
    baseline_ref: str | None = None
    thresholds: dict = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("evaluator_key", self.evaluator_key)
        _require_non_empty("agent_id", self.agent_id)
        _require_non_empty("period", self.period)
        object.__setattr__(self, "verdict", _as_enum("verdict", self.verdict, DriftVerdict))
        _require_int("samples", self.samples, minimum=0)
        _require_detector_version(self.detector_version)
        _require_thresholds(self.thresholds)

        judged = self.verdict in JUDGED_VERDICTS
        if judged:
            if self.psi is None:
                raise ValidationError(
                    f"verdict={self.verdict} 为判定类，必须携带 psi（禁止无指标判定）"
                )
            if not isinstance(self.quantile_shifts, dict) or not self.quantile_shifts:
                raise ValidationError(
                    f"verdict={self.verdict} 为判定类，quantile_shifts 必须为非空位移向量"
                )
        else:
            if self.psi is not None:
                raise ValidationError(
                    f"verdict={self.verdict} 不判定，不得携带 psi（禁止伪造指标）"
                )
            if self.quantile_shifts:
                raise ValidationError(
                    f"verdict={self.verdict} 不判定，quantile_shifts 必须为空（禁止伪造指标）"
                )
        if self.psi is not None:
            _require_number("psi", self.psi, minimum=0.0)
        for name, value in self.quantile_shifts.items():
            _require_non_empty("quantile_shifts", name)
            _require_number(f"quantile_shifts.{name}", value)

        if self.verdict is DriftVerdict.NO_BASELINE:
            if self.baseline_ref is not None:
                raise ValidationError("verdict=no_baseline 不得携带 baseline_ref（首周期无基线）")
        elif judged or self.verdict is DriftVerdict.INSUFFICIENT:
            _require_non_empty("baseline_ref", self.baseline_ref)
        else:
            _require_optional_non_empty("baseline_ref", self.baseline_ref)


def _require_thresholds(thresholds: object) -> None:
    if not isinstance(thresholds, dict) or not thresholds:
        raise ValidationError("thresholds 必须为非空映射（本次判定的阈值快照）")
    for key in ("psi", "quantile"):
        if key not in thresholds:
            raise ValidationError(f"thresholds 缺少配置项 {key!r}")
        value = thresholds[key]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValidationError(f"thresholds.{key} 必须为 > 0 的有限数值，实际为 {value!r}")
    for key in ("min_samples", "window"):
        if key not in thresholds:
            raise ValidationError(f"thresholds 缺少配置项 {key!r}")
        _require_int(f"thresholds.{key}", thresholds[key], minimum=1)


# 状态机合法迁移：系统可写 normal → suspect；suspect → 终态与 false_alarm → normal
# 均需人工处置留痕引用（by_human=True + disposition_ref）；confirmed_drift 为终点（无出边）。
_STATE_TRANSITIONS = {
    DriftState.NORMAL: (DriftState.SUSPECT,),
    DriftState.SUSPECT: (DriftState.CONFIRMED_DRIFT, DriftState.FALSE_ALARM),
    DriftState.FALSE_ALARM: (DriftState.NORMAL,),
    DriftState.CONFIRMED_DRIFT: (),
}


@dataclass(frozen=True)
class DriftStatus:
    """评估器版本的漂移状态记录（registry 当前态；历史只增不改）。

    - status=normal（初始或误报恢复）/ suspect（系统判定超阈写入，需 trigger_metrics）/
      confirmed_drift / false_alarm（终态，需 disposition_ref 人工留痕引用）；
    - 模型层拒绝"系统写终态"：无 disposition_ref 的终态构造被拒（SC-002 机检落点）。
    """

    evaluator_key: str
    status: DriftState = DriftState.NORMAL
    since: str = ""
    trigger_metrics: str | None = None
    disposition_ref: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("evaluator_key", self.evaluator_key)
        object.__setattr__(self, "status", _as_enum("status", self.status, DriftState))
        if self.status is DriftState.SUSPECT:
            _require_non_empty("trigger_metrics", self.trigger_metrics)
        if self.status in (DriftState.CONFIRMED_DRIFT, DriftState.FALSE_ALARM):
            # 终态必须来自人工处置留痕（系统只写 suspect）
            _require_non_empty("disposition_ref", self.disposition_ref)
        _require_optional_non_empty("trigger_metrics", self.trigger_metrics)
        _require_optional_non_empty("disposition_ref", self.disposition_ref)
        _require_non_empty("since", self.since)

    @property
    def human_written(self) -> bool:
        """当前态是否由人工处置写入（终态 True；suspect/normal 由系统判定写入）。"""
        return self.status in (DriftState.CONFIRMED_DRIFT, DriftState.FALSE_ALARM)

    def transition(
        self,
        to: DriftState,
        *,
        since: str,
        trigger_metrics: str | None = None,
        by_human: bool = False,
        disposition_ref: str | None = None,
    ) -> "DriftStatus":
        """状态机迁移（frozen：产出新实例，原对象不变）。

        - `normal → suspect`：检测判定超阈写入（必须携带 trigger_metrics 引用）；
        - `suspect → confirmed_drift | false_alarm` 与 `false_alarm → normal`：仅人工处置
          （by_human=True + disposition_ref），系统调用即拒绝（原则六：处置权在人）。
        """
        to = _as_enum("status", to, DriftState)
        if to not in _STATE_TRANSITIONS[self.status]:
            raise ValidationError(f"漂移状态不允许 {self.status} → {to}")

        if self.status is DriftState.NORMAL and to is DriftState.SUSPECT:
            if not isinstance(trigger_metrics, str) or not trigger_metrics:
                raise ValidationError("登记 suspect 必须携带触发指标引用（trigger_metrics）")
        else:
            if not by_human or not isinstance(disposition_ref, str) or not disposition_ref:
                raise ValidationError(
                    f"漂移状态 {self.status} → {to} 仅人工处置写入（系统只写 suspect；"
                    "需 by_human=True + disposition_ref 留痕引用）"
                )

        return replace(
            self,
            status=to,
            since=since,
            trigger_metrics=self.trigger_metrics if trigger_metrics is None else trigger_metrics,
            disposition_ref=self.disposition_ref if disposition_ref is None else disposition_ref,
        )


@dataclass(frozen=True)
class DriftDisposition:
    """人工处置留痕：结论（确认漂移 / 误报）+ 人 + 时间 + 理由 + 动作；只增不改。

    理由必填——无理由不落痕（原则六：处置必须留痕，且留痕不得被改写）。
    """

    evaluator_key: str
    conclusion: DriftConclusion
    by: str
    at: str
    reason: str
    action: DriftAction

    def __post_init__(self) -> None:
        _require_non_empty("evaluator_key", self.evaluator_key)
        object.__setattr__(
            self, "conclusion", _as_enum("conclusion", self.conclusion, DriftConclusion)
        )
        object.__setattr__(self, "action", _as_enum("action", self.action, DriftAction))
        _require_non_empty("by", self.by)
        _require_non_empty("at", self.at)
        _require_non_empty("reason", self.reason)


@dataclass(frozen=True)
class DriftReport:
    """周期漂移监控报表：per (agent, evaluator) 的指标序列/基线/阈值/状态/处置 + 告警。

    items 为不可变元组（每项含 agent_id / evaluator_key / status 等，US3 落地字段），
    double_signal_rules 记录本次报表使用的双信号联动规则（配置快照，报表自描述）；
    alerts 为报表级告警清单（强化告警含 `double_signal: true` 与级别升级）；
    residual_signals 为**残余信号附注**（F6 ScoreConflict 等）——**不参与阈值判定**，
    无持久化来源时字段为空并如实注明（不伪造冲突数据）。
    """

    period: str
    detector_version: str
    generated_at: str
    items: tuple = ()
    double_signal_rules: dict = field(default_factory=dict)
    alerts: tuple = ()
    residual_signals: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("period", self.period)
        _require_detector_version(self.detector_version)
        _require_non_empty("generated_at", self.generated_at)
        if not isinstance(self.items, tuple):
            raise ValidationError("items 必须为元组（immutable；每项为映射）")
        for item in self.items:
            if not isinstance(item, dict) or not item:
                raise ValidationError("items 的每项必须为非空映射")
        if not isinstance(self.double_signal_rules, dict):
            raise ValidationError("double_signal_rules 必须为映射（双信号联动规则快照）")
        if not isinstance(self.alerts, tuple):
            raise ValidationError("alerts 必须为元组（报表级告警清单）")
        for index, alert in enumerate(self.alerts):
            if not isinstance(alert, dict) or not alert:
                raise ValidationError(f"alerts[{index}] 必须为非空映射")
            for key in ("agent_id", "evaluator_key", "level"):
                if not isinstance(alert.get(key), str) or not alert[key]:
                    raise ValidationError(f"alerts[{index}].{key} 必须为非空字符串（告警字段齐全）")
            if not isinstance(alert.get("double_signal"), bool):
                raise ValidationError(f"alerts[{index}].double_signal 必须为布尔值")
        if not isinstance(self.residual_signals, dict):
            raise ValidationError("residual_signals 必须为映射（残余信号附注，不参与阈值判定）")


@dataclass(frozen=True)
class DeployEvidenceVerdict:
    """F9 部署证据接口返回形态：允许/拒绝 + 理由（理由必填——拒绝尤其不得沉默）。"""

    evaluator_key: str
    allow: bool
    reason: str

    def __post_init__(self) -> None:
        _require_non_empty("evaluator_key", self.evaluator_key)
        if not isinstance(self.allow, bool):
            raise ValidationError(f"allow 必须为布尔值，实际为 {self.allow!r}")
        _require_non_empty("reason", self.reason)
