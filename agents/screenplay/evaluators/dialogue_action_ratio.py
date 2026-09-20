"""对白/动作行比例门禁评估器：rule.dialogue_action_ratio（硬规则门禁，C7）。

对白行占比 = 对白行数 ÷ 总行数；占比 ∈ 配置区间
（`screenplay.dialogue_action_ratio` 的 min/max，闭区间）通过，越界 → gate 判 0
（对白压倒动作 = 剧本形态失衡，画面语言无从落地）。区间缺项或次序倒置拒绝启动
——不允许静默放过门禁（原则五）。
"""

import json

from agents.screenplay.config import ScreenplayConfigError
from agents.screenplay.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.dialogue_action_ratio"

_EPSILON = 1e-9  # 闭区间比较容差（占比由除法得出，避免边界浮点抖动）


def _require_number(slice_dict, key: str) -> float:
    value = slice_dict.get(key) if isinstance(slice_dict, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ScreenplayConfigError(
            f"rule.dialogue_action_ratio 缺配置项 {key!r}（须为数值），拒绝启动"
            f"——不允许静默放过门禁（原则五）"
        )
    return float(value)


class DialogueActionRatioEvaluator(Evaluator):
    """对白行占比门禁（确定性、零成本；区间全配置驱动）。"""

    def __init__(self, dialogue_action_ratio: dict) -> None:
        self._low = _require_number(dialogue_action_ratio, "min")
        self._high = _require_number(dialogue_action_ratio, "max")
        if not 0.0 <= self._low < self._high <= 1.0:
            raise ScreenplayConfigError(
                "screenplay.dialogue_action_ratio 必须满足 0 ≤ min < max ≤ 1，实际为 "
                f"{dialogue_action_ratio!r}（区间非法拒绝启动）"
            )
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(dialogue_action_ratio, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        script = context["artifact"]
        dialogue = script.count_kind("dialogue")
        action = script.count_kind("action")
        total = script.total_lines()  # 工件构造保证 ≥ 1（缺行即拒绝，见 artifact.py）
        ratio = dialogue / total
        passed = self._low - _EPSILON <= ratio <= self._high + _EPSILON
        violations = (
            []
            if passed
            else [
                f"对白行占比越界：{round(ratio, 6)} ∉ [{self._low}, {self._high}]"
                f"（对白 {dialogue} / 动作 {action} / 总 {total} 行）"
            ]
        )
        return EvalResult(
            score=1.0 if passed else 0.0,
            diagnostics={
                "applicable": True,
                "dialogue_ratio": round(ratio, 6),
                "dialogue_lines": dialogue,
                "action_lines": action,
                "line_count": total,
                "range": [self._low, self._high],
                "violations": violations,
            },
        )
