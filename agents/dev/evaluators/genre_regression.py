"""历史同类型票房回归代理：proxy.genre_regression（契约 C9）。

确定性函数（零 LLM、零网络）：逐方向按**模拟数据源**给出同类型票房回归预测
（基线 × 灵敏度 × 同类型夹具系数，见 `agents/dev/signals.py`），组合分值 = 各方向预测均值
相对基线两倍的定点归一（达到基线两倍即满分）。驱动数据为**模拟数据源 + 夹具，非真实商业
数据**——来源标注随诊断入 `eval_breakdown`（SC-009，真实票房库接入属 G3）。

版本号 = 实现文件哈希 + 模拟数据源参数（数据源即行为口径，原则一）：`dev.signals` 任一参数
变更 ⇒ 新 `evaluator_id@version`，历史节点的分量不重算。
"""

from agents.dev.config import DevConfigError
from agents.dev.evaluators._versioning import implementation_version
from agents.dev.signals import SOURCE_BOX_OFFICE, SimulatedSignalSource
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.genre_regression"


class GenreRegressionEvaluator(Evaluator):
    """历史同类型票房回归预测（确定性、零成本；驱动源为参数化模拟数据源）。"""

    def __init__(self, source: SimulatedSignalSource) -> None:
        if not isinstance(source, SimulatedSignalSource) or source.source_id != SOURCE_BOX_OFFICE:
            raise DevConfigError(
                "proxy.genre_regression 缺模拟数据源（dev.signals，来源 "
                f"{SOURCE_BOX_OFFICE}），拒绝启动——不允许静默放过代理分量（FR-012）"
            )
        self._source = source
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            # 数据源参数进版本号：改参数即新版本（原则一）
            version=implementation_version(source.version_part()),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        slate = context["artifact"]
        per_direction = {
            entry.direction_id: self._source.box_office_usd_million(genre=entry.genre)
            for entry in slate.entries
        }
        predicted = sum(per_direction.values()) / len(per_direction)
        normalizer = self._source.box_office_normalizer_usd_million()
        score = quantize_score(min(1.0, max(0.0, predicted / normalizer)))
        return EvalResult(
            score=score,
            diagnostics={
                **self._source.provenance(),
                "regression": "同类型票房回归（模拟数据源 + 夹具）",
                "entry_count": len(per_direction),
                "per_direction_usd_million": per_direction,
                "predicted_usd_million": predicted,
                "normalizer_usd_million": normalizer,
            },
        )
