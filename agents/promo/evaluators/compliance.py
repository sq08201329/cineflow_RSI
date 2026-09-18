"""合规评估器：rule.material_compliance@1.0.0（FR-003，硬规则门禁）。

平台物料规格（尺寸/时长/文案长度）+ 敏感词库检查；任一不过 → score 0.0。
缺物料规格或敏感词库配置：构造即 PromoConfigError——缺配置拒投不放行。
"""

from agents.promo.config import PromoConfig, PromoConfigError
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.material_compliance"
VERSION = "1.0.0"


class MaterialComplianceEvaluator(Evaluator):
    """物料合规硬规则（确定性、零成本）。"""

    def __init__(self, config: PromoConfig) -> None:
        if not config.material_spec:
            raise PromoConfigError("缺物料规格配置（material_spec）——拒投不放行")
        if not config.sensitive_words:
            raise PromoConfigError("缺敏感词库配置（sensitive_words）——拒投不放行")
        self._spec = config.material_spec
        self._sensitive_words = list(config.sensitive_words)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=VERSION,
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        material = context["material"]
        content = material["content"]
        violations: list[str] = []

        copy = str(content.get("copy", ""))
        max_chars = int(self._spec["max_copy_chars"])
        if len(copy) > max_chars:
            violations.append(f"文案超长：{len(copy)} > {max_chars}")
        for word in self._sensitive_words:
            if word in copy:
                violations.append(f"敏感词命中：{word!r}")

        poster_size = content.get("poster_size")
        if poster_size is not None and poster_size != self._spec["poster_size"]:
            violations.append(f"海报尺寸不符：{poster_size} ≠ {self._spec['poster_size']}")
        duration = content.get("duration_seconds")
        max_duration = float(self._spec["max_duration_seconds"])
        if duration is not None and float(duration) > max_duration:
            violations.append(f"时长超限：{duration}s > {max_duration}s")

        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={"violations": violations, "checks": ["copy", "poster", "duration"]},
        )
