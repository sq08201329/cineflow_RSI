"""镜头时长分布评估器：rule.shot_distribution（硬规则门禁，C5）。

逐镜头时长 ∈ [min_shot_ms, max_shot_ms]（含端点，配置 shot_limits）；
任一越界 → gate 判 0（防碎片化与拖沓）。输入 = 渲染元数据
shot_durations_ms（与 EDL clip 时长序列一致，C10 元数据契约）。
"""

import json

from agents.editing.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.shot_distribution"


class ShotDistributionEvaluator(Evaluator):
    """镜头时长分布门禁（确定性、零成本）。"""

    def __init__(self, shot_limits: dict) -> None:
        self._min_ms = int(shot_limits["min_shot_ms"])
        self._max_ms = int(shot_limits["max_shot_ms"])
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(json.dumps(shot_limits, sort_keys=True)),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        durations = [int(d) for d in artifact.metadata["shot_durations_ms"]]
        violations = [
            f"镜头 {i + 1} 时长 {d}ms 越界：[{self._min_ms}, {self._max_ms}]ms"
            for i, d in enumerate(durations)
            if not self._min_ms <= d <= self._max_ms
        ]
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "shot_durations_ms": durations,
                "min_shot_ms": self._min_ms,
                "max_shot_ms": self._max_ms,
                "violations": violations,
            },
        )
