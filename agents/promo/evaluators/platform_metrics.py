"""平台真值评估器：human.platform_metrics@1.0.0（FR-006）。

平台回流指标（完播率/CTR/转化）按 configs 权重归一化合成；
diagnostics 保留原始指标；越界指标校验拒绝；
human 锚点——产出写入即冻结为常数（deterministic=False 注册例外）。

**读路径 vs 写路径**（功能 020）：本评估器是 010/015 的**读取/重建路径**
（从落盘 payload 重建 `MetricSnapshot`），故只做**值域**校验
（`validate_metric_ranges`）——历史 payload 无 `metric_date` 键时重建得 `None`，
**不抛构造期异常、行仍可计分**；归属日的存在性检查只在**写入路径**强制
（`validate_metrics`，见 `agents/promo/{ingest,daily}.py`）。
"""

from agents.promo.platform.base import MetricSnapshot, validate_metric_ranges
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.errors import ValidationError

EVALUATOR_ID = "human.platform_metrics"
VERSION = "1.0.0"
DEFAULT_CONVERSION_RATE_CAP = 0.05  # 转化率归一化上限（conversions/impressions）


class PlatformMetricsEvaluator(Evaluator):
    """平台真值归一化合成（人类锚点类型）。"""

    def __init__(
        self,
        metric_weights: dict,
        *,
        ctr_cap: float,
        conversion_rate_cap: float = DEFAULT_CONVERSION_RATE_CAP,
    ) -> None:
        self._weights = metric_weights
        self._ctr_cap = ctr_cap
        self._conversion_rate_cap = conversion_rate_cap
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=VERSION,
            kind=EvaluatorKind.HUMAN,
            deterministic=False,  # human 锚点例外（产出落盘即冻结为常数）
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        raw = context["metrics"]
        snapshot = MetricSnapshot(
            ctr=float(raw["ctr"]),
            completion_rate=float(raw["completion_rate"]),
            conversions=int(raw["conversions"]),
            impressions=int(raw["impressions"]),
            clicks=int(raw["clicks"]),
            platform_timestamp=float(raw.get("platform_timestamp", 0.0)),
            data_version=str(raw.get("data_version", "")),
            # 归属日：历史 payload 缺该键 ⇒ None（构造成功，不抛构造期异常）。
            # 存在性检查只落在**写入路径**（`validate_metrics`）；本评估器是读取/重建路径，
            # 故只做值域校验（`validate_metric_ranges`），历史行因此仍可计分。
            metric_date=raw.get("metric_date"),
        )
        try:
            validate_metric_ranges(snapshot)
        except Exception as exc:
            raise ValidationError(f"回流指标越界拒绝：{exc}") from exc

        ctr_n = min(1.0, snapshot.ctr / self._ctr_cap)
        conversion_rate = (
            snapshot.conversions / snapshot.impressions if snapshot.impressions else 0.0
        )
        conv_n = min(1.0, conversion_rate / self._conversion_rate_cap)
        score = (
            self._weights["ctr"] * ctr_n
            + self._weights["completion_rate"] * snapshot.completion_rate
            + self._weights["conversions"] * conv_n
        )
        return EvalResult(
            score=score,
            diagnostics={
                "ctr": snapshot.ctr,
                "completion_rate": snapshot.completion_rate,
                "conversions": snapshot.conversions,
                "impressions": snapshot.impressions,
                "clicks": snapshot.clicks,
                "data_version": snapshot.data_version,
            },
        )
