"""页数-时长换算门禁评估器：rule.page_minutes（硬规则门禁，C5）。

总行数 ÷ `lines_per_page`（配置）→ 页数 ≈ 目标成片时长（分钟，1 页 ≈ 1 分钟）：
页数 ∈ [目标 − 容差, 目标 + 容差] 通过，越界 → gate 判 0（体量与目标时长不匹配 =
策划不可行解）。口径全配置驱动（target_duration_min / page_tolerance / lines_per_page），
缺任一项拒绝启动——不允许静默放过门禁（原则五）。
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

EVALUATOR_ID = "rule.page_minutes"

_EPSILON = 1e-9  # 闭区间比较容差（页数由除法得出，避免边界浮点抖动）


def _require_int(slice_dict, key: str, *, minimum: int):
    value = slice_dict.get(key) if isinstance(slice_dict, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ScreenplayConfigError(
            f"rule.page_minutes 缺配置项 {key!r}（须为 ≥{minimum} 的整数），拒绝启动"
            f"——不允许静默放过门禁（原则五）"
        )
    return value


class PageMinutesEvaluator(Evaluator):
    """页数-时长换算门禁（确定性、零成本）。"""

    def __init__(self, page_minutes: dict) -> None:
        self._target = _require_int(page_minutes, "target_duration_min", minimum=1)
        self._tolerance = _require_int(page_minutes, "page_tolerance", minimum=0)
        self._lines_per_page = _require_int(page_minutes, "lines_per_page", minimum=1)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(page_minutes, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        script = context["artifact"]
        line_count = script.total_lines()
        pages = script.page_count(self._lines_per_page)
        low = self._target - self._tolerance
        high = self._target + self._tolerance
        passed = low - _EPSILON <= pages <= high + _EPSILON
        violations = (
            []
            if passed
            else [
                f"页数越界：{round(pages, 6)} 页 ∉ [{low}, {high}] 页"
                f"（{line_count} 行 ÷ {self._lines_per_page} 行/页；目标 {self._target} 分钟"
                f" ± {self._tolerance} 页）"
            ]
        )
        return EvalResult(
            score=1.0 if passed else 0.0,
            diagnostics={
                "applicable": True,
                "pages": round(pages, 6),
                "line_count": line_count,
                "lines_per_page": self._lines_per_page,
                "target_pages": self._target,
                "page_tolerance": self._tolerance,
                "window": [low, high],
                "violations": violations,
            },
        )
