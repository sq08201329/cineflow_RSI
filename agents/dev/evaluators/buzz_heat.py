"""舆情热度代理：proxy.buzz_heat（契约 C9）。

确定性函数（零 LLM、零网络）：逐方向按**模拟数据源**给出舆情检索热度
（基线 × 灵敏度 × 同类型夹具系数，∈ [0,1]，见 `agents/dev/signals.py`），组合分值 = 各方向
热度均值。驱动数据为**模拟数据源 + 夹具，非真实商业数据**——来源标注随诊断入
`eval_breakdown`（SC-009，真实舆情接口接入属 G3 渠道范围）。

版本号 = 实现文件哈希 + 模拟数据源参数（数据源即行为口径，原则一）：`dev.signals` 任一参数
变更 ⇒ 新 `evaluator_id@version`，历史节点的分量不重算。
"""

from agents.dev.config import DevConfigError
from agents.dev.evaluators._versioning import implementation_version
from agents.dev.signals import SOURCE_BUZZ, SimulatedSignalSource
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.buzz_heat"


class BuzzHeatEvaluator(Evaluator):
    """舆情检索热度（确定性、零成本；驱动源为参数化模拟数据源）。"""

    def __init__(self, source: SimulatedSignalSource) -> None:
        if not isinstance(source, SimulatedSignalSource) or source.source_id != SOURCE_BUZZ:
            raise DevConfigError(
                "proxy.buzz_heat 缺模拟数据源（dev.signals，来源 "
                f"{SOURCE_BUZZ}），拒绝启动——不允许静默放过代理分量（FR-012）"
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
            entry.direction_id: self._source.buzz_heat(genre=entry.genre) for entry in slate.entries
        }
        heat = sum(per_direction.values()) / len(per_direction)
        return EvalResult(
            score=quantize_score(min(1.0, max(0.0, heat))),
            diagnostics={
                **self._source.provenance(),
                "retrieval": "舆情检索热度（模拟数据源 + 夹具）",
                "entry_count": len(per_direction),
                "per_direction_heat": per_direction,
                "mean_heat": heat,
            },
        )
