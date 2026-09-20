"""转场规则评估器：rule.transition_rules（硬规则门禁，C6）。

EDL 转场序列经规则库复核——直接调用 edl.validate_edl（与执行前校验同一
配置规则库，单一事实源，research 决策 3：门禁 = 执行前校验的提前计算）。
违规 → gate 判 0；违规明细（含分区/越界等校验层信息）如实入 diagnostics。
"""

import json

from agents.editing.edl import validate_edl
from agents.editing.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.tree.errors import ValidationError

EVALUATOR_ID = "rule.transition_rules"


class TransitionRulesEvaluator(Evaluator):
    """转场规则库门禁（确定性、零成本；规则库全配置驱动）。"""

    def __init__(self, transition_rules: dict) -> None:
        self._rules = dict(transition_rules)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(transition_rules, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        edl = context["edl"]  # EditDecisionList（执行器 ctx 必带）
        violations = []
        try:
            validate_edl(
                edl,
                context["shot_library"],
                context["scene_structure"],
                self._rules,
            )
        except ValidationError as exc:
            violations.append(str(exc))
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "transition_count": len(edl.clips),
                "rules": {"allowed": list(self._rules["allowed"])},
                "violations": violations,
            },
        )
