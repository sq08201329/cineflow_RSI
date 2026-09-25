"""校准领域模型（frozen dataclass，data-model.md §领域模型）。

- AnchorScore：DB 行（calibration_anchors）的内存形态，构造即校验；
- CalibrationRound / WeightProposal：状态机迁移产出新实例（frozen 原对象不变），
  终态不可逆；
- 校验风格与 ValidationError 复用 core/evaluators（core 内错误语义一致）。
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

from core.evaluators.errors import ValidationError

_ARTIFACT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_optional_iso_date(name: str, value: str | None) -> None:
    """可空归属日校验：非空时必须是**合法且真实存在**的 ISO `YYYY-MM-DD`。"""
    if value is None:
        return
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise ValidationError(f"{name} 必须为 ISO 日期（YYYY-MM-DD）或 None，实际为 {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{name} 非法（非真实日历日）：{value!r}（{exc}）") from exc


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} 必须为非空字符串")


def _require_optional_non_empty(name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(name, value)


def _require_score(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValidationError(f"{name} 必须 ∈ [0,1]，实际为 {value!r}")


def _optional_correlation(name: str, value: float | None) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not -1.0 <= value <= 1.0:
        raise ValidationError(f"{name} 必须 ∈ [-1,1]，实际为 {value!r}")


def _require_weights(name: str, weights: dict) -> None:
    if not isinstance(weights, dict) or not weights:
        raise ValidationError(f"{name} 必须为非空映射")
    for key, value in weights.items():
        if not isinstance(key, str) or not key:
            raise ValidationError(f"{name} 的键必须为非空字符串，实际为 {key!r}")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValidationError(f"{name} 的权重必须为 ≥ 0 的数值，实际为 {value!r}")


class AnchorSource(StrEnum):
    """锚点来源：人评盲评 / 平台真值回流。"""

    HUMAN_BLIND = "human_blind"
    PLATFORM_TRUTH = "platform_truth"


class RoundStatus(StrEnum):
    """校准轮次状态机：open（清单已发）→ intake（录入中）→ closed（偏差计算完成）。"""

    OPEN = "open"
    INTAKE = "intake"
    CLOSED = "closed"


class ProposalStatus(StrEnum):
    """提案状态机：pending → confirmed | shelved | failed（终态不可逆）。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHELVED = "shelved"
    FAILED = "failed"


@dataclass(frozen=True)
class AnchorScore:
    """锚点评分：人评或平台真值对某节点工件的打分，写入即冻结。

    `metric_date`（功能 020）为**末位可选**归属日（= 平台指标所描述的日期）：
    取可选的理由只有一个——兼容历史 payload 的 dict 重建；存在性检查在**写入路径**
    （`agents/promo/anchors.py` 的采集出口与 `validate_metrics`），不在数据类上。
    `human_blind` 行不填该列（该列只对 `source == platform_truth` 有语义）。
    """

    anchor_id: str
    node_id: str
    artifact_hash: str
    agent_id: str
    source: AnchorSource
    score: float
    reviewer: str
    round_id: str
    created_at: str
    metric_date: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("anchor_id", self.anchor_id)
        _require_non_empty("node_id", self.node_id)
        if not _ARTIFACT_HASH_RE.match(self.artifact_hash):
            raise ValidationError("artifact_hash 必须为 64 位小写十六进制（BLAKE3）")
        _require_non_empty("agent_id", self.agent_id)
        if not isinstance(self.source, AnchorSource):
            try:
                object.__setattr__(self, "source", AnchorSource(self.source))
            except ValueError as exc:
                raise ValidationError(f"source 必须为 {list(AnchorSource)} 之一") from exc
        _require_score("score", self.score)
        _require_non_empty("reviewer", self.reviewer)
        _require_non_empty("round_id", self.round_id)
        _require_non_empty("created_at", self.created_at)
        _require_optional_iso_date("metric_date", self.metric_date)


_ROUND_TRANSITIONS = {
    RoundStatus.OPEN: (RoundStatus.INTAKE,),
    RoundStatus.INTAKE: (RoundStatus.CLOSED,),
    RoundStatus.CLOSED: (),
}


@dataclass(frozen=True)
class CalibrationRound:
    """校准轮次：周期内 top-k 盲评清单与收口状态。"""

    round_id: str
    agent_id: str
    period_start: str
    period_end: str
    top_k: int
    node_ids: tuple = ()
    status: RoundStatus = RoundStatus.OPEN
    note: str = ""  # 样本量备注（"样本不足"等）

    def __post_init__(self) -> None:
        _require_non_empty("round_id", self.round_id)
        _require_non_empty("agent_id", self.agent_id)
        _require_non_empty("period_start", self.period_start)
        _require_non_empty("period_end", self.period_end)
        if not isinstance(self.top_k, int) or isinstance(self.top_k, bool) or self.top_k < 1:
            raise ValidationError(f"top_k 必须为 ≥ 1 的整数，实际为 {self.top_k!r}")
        if not isinstance(self.status, RoundStatus):
            try:
                object.__setattr__(self, "status", RoundStatus(self.status))
            except ValueError as exc:
                raise ValidationError(f"status 必须为 {list(RoundStatus)} 之一") from exc

    def transition(self, to: RoundStatus) -> "CalibrationRound":
        """状态机迁移（open → intake → closed），产出新实例。"""
        if not isinstance(to, RoundStatus):
            to = RoundStatus(to)
        if to not in _ROUND_TRANSITIONS[self.status]:
            raise ValidationError(f"轮次状态不允许 {self.status} → {to}")
        return replace(self, status=to)


@dataclass(frozen=True)
class PairingRecord:
    """锚点 × eval_breakdown 分量配对；自循环剔除时注明剔除分量。"""

    anchor_id: str
    evaluator_key: str  # evaluator_id@version
    anchor_score: float
    auto_score: float | None  # 分量缺失或被剔除时为 None
    excluded: bool = False
    excluded_components: tuple = ()
    note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("anchor_id", self.anchor_id)
        _require_non_empty("evaluator_key", self.evaluator_key)
        _require_score("anchor_score", self.anchor_score)
        if self.auto_score is not None:
            _require_score("auto_score", self.auto_score)


@dataclass(frozen=True)
class BiasRecord:
    """单评估器单周期偏差台账行：连续口径（mean_shift + pearson_r）或
    judge 口径（kendall_tau）；样本不足时不产偏差值并在 note 注明。

    功能 020 新增五个**可空**溯源字段（历史行为 `None`，写入端经
    `core.calibration.ledger.with_provenance` 在 `append_ledger` 之前补全）：
    `period_days` / `window_semantics` / `round_id`（口径与轮次自描述）、
    `anchor_count` / `snapshot_fingerprint`（**快照物化的自描述指针**：该周期该评估器
    计入分布的锚点数 + 所物化快照的指纹，二者配对定位"读的是哪一份"）。
    """

    evaluator_key: str
    period: str
    samples: int
    mean_shift: float | None = None
    pearson_r: float | None = None
    kendall_tau: float | None = None
    note: str = ""
    period_days: int | None = None
    window_semantics: str | None = None
    round_id: str | None = None
    anchor_count: int | None = None
    snapshot_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("evaluator_key", self.evaluator_key)
        _require_non_empty("period", self.period)
        if not isinstance(self.samples, int) or isinstance(self.samples, bool) or self.samples < 0:
            raise ValidationError(f"samples 必须为 ≥ 0 的整数，实际为 {self.samples!r}")
        if self.mean_shift is not None and (
            not isinstance(self.mean_shift, (int, float)) or isinstance(self.mean_shift, bool)
        ):
            raise ValidationError(f"mean_shift 必须为数值，实际为 {self.mean_shift!r}")
        _optional_correlation("pearson_r", self.pearson_r)
        _optional_correlation("kendall_tau", self.kendall_tau)
        if self.period_days is not None and (
            not isinstance(self.period_days, int)
            or isinstance(self.period_days, bool)
            or self.period_days < 1
        ):
            raise ValidationError(
                f"period_days 必须为 ≥ 1 的整数或 None，实际为 {self.period_days!r}"
            )
        _require_optional_non_empty("window_semantics", self.window_semantics)
        _require_optional_non_empty("round_id", self.round_id)
        if self.anchor_count is not None:
            if (
                not isinstance(self.anchor_count, int)
                or isinstance(self.anchor_count, bool)
                or self.anchor_count < 0
            ):
                raise ValidationError(
                    f"anchor_count 必须为 ≥ 0 的整数或 None，实际为 {self.anchor_count!r}"
                )
            if self.anchor_count != self.samples:
                raise ValidationError(
                    "anchor_count 必须等于 samples（该周期快照所依据的锚点数即本行样本量），"
                    f"实际为 anchor_count={self.anchor_count} / samples={self.samples}"
                )
        if self.snapshot_fingerprint is not None and not _ARTIFACT_HASH_RE.match(
            self.snapshot_fingerprint
        ):
            raise ValidationError(
                "snapshot_fingerprint 必须为 64 位小写十六进制（BLAKE3）或 None，"
                f"实际为 {self.snapshot_fingerprint!r}"
            )


@dataclass(frozen=True)
class WeightProposal:
    """权重再拟合提案：候选权重由约束岭回归自动拟合，人工仅确认/搁置（禁止编辑）。"""

    proposal_id: str
    agent_id: str
    based_version: str
    bias_evidence: tuple = ()  # BiasRecord 引用（偏差证据）
    current_weights: dict = field(default_factory=dict)
    candidate_weights: dict = field(default_factory=dict)
    fit_objective: str = ""
    ridge_lambda: float = 1.0
    status: ProposalStatus = ProposalStatus.PENDING
    confirmed_by: str | None = None
    confirmed_at: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("proposal_id", self.proposal_id)
        _require_non_empty("agent_id", self.agent_id)
        _require_non_empty("based_version", self.based_version)
        _require_weights("current_weights", self.current_weights)
        _require_weights("candidate_weights", self.candidate_weights)
        if (
            not isinstance(self.ridge_lambda, (int, float))
            or isinstance(self.ridge_lambda, bool)
            or self.ridge_lambda < 0
        ):
            raise ValidationError(f"ridge_lambda 必须为 ≥ 0 的数值，实际为 {self.ridge_lambda!r}")
        if not isinstance(self.status, ProposalStatus):
            try:
                object.__setattr__(self, "status", ProposalStatus(self.status))
            except ValueError as exc:
                raise ValidationError(f"status 必须为 {list(ProposalStatus)} 之一") from exc

    def _require_pending(self, action: str) -> None:
        if self.status is not ProposalStatus.PENDING:
            raise ValidationError(f"提案终态不可逆：{self.status} 不允许 {action}")

    def confirm(self, *, by: str, at: str) -> "WeightProposal":
        """确认生效（pending → confirmed），记录确认人与时间。"""
        self._require_pending("confirm")
        _require_non_empty("by", by)
        _require_non_empty("at", at)
        return replace(self, status=ProposalStatus.CONFIRMED, confirmed_by=by, confirmed_at=at)

    def shelve(self) -> "WeightProposal":
        """人工搁置（pending → shelved），零变更。"""
        self._require_pending("shelve")
        return replace(self, status=ProposalStatus.SHELVED)

    def fail(self) -> "WeightProposal":
        """生效中途失败（pending → failed），回滚后落此终态。"""
        self._require_pending("fail")
        return replace(self, status=ProposalStatus.FAILED)
