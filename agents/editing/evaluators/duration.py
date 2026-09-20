"""时长合规评估器：rule.duration_compliance（硬规则门禁，C4）。

成片总时长 ∈ target_duration_s ± duration_tolerance_s（含端点）通过，
否则 gate 判 0。输入 = 渲染元数据 duration_ms（帧数口径，与 EDL 名义时长
的叠化差异已由渲染层如实承担）。
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

EVALUATOR_ID = "rule.duration_compliance"


class DurationComplianceEvaluator(Evaluator):
    """总时长门禁（确定性、零成本）。"""

    def __init__(self, target_duration_s: float, duration_tolerance_s: float) -> None:
        self._lower_ms = int((target_duration_s - duration_tolerance_s) * 1000)
        self._upper_ms = int((target_duration_s + duration_tolerance_s) * 1000)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(
                    {
                        "target_duration_s": float(target_duration_s),
                        "duration_tolerance_s": float(duration_tolerance_s),
                    }
                )
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        duration_ms = int(artifact.metadata["duration_ms"])
        violations = []
        if not self._lower_ms <= duration_ms <= self._upper_ms:
            violations.append(
                f"成片总时长 {duration_ms}ms 越界：[{self._lower_ms}, {self._upper_ms}]ms"
            )
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "duration_ms": duration_ms,
                "lower_ms": self._lower_ms,
                "upper_ms": self._upper_ms,
                "violations": violations,
            },
        )
