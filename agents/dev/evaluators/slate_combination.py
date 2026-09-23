"""立项组合层门禁：rule.slate_combination（硬规则门禁，契约 C8——组合层核心门禁）。

组合层约束对照形态配置：

- **方向多样性/去重率**：组合内方向重复率 ≤ `dev.combination.max_direction_repeat_rate`；
- **条目数上限**：条目数 ≤ `dev.slate.max`；
- **进入生产标记数量 ∈ `dev.production_marks` 区间**，且**每个标记指向组合内已存在的条目**
  （悬空标记即违规）。

0 标记与区间下界冲突时**以区间为准**：判 0 并如实记录（契约 C5/C8——"无达标条目"如实产出
0 标记是合法状态，但配置区间下界 > 0 时该轮确实未落区间，由门禁点名而非掩盖；不得降格硬凑
一个标记，SC-010）。违规 → gate 判 0 且诊断点名违规项，**不抛异常**；缺项即装配期报错。
"""

import json

from agents.dev.config import DevConfigError
from agents.dev.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.slate_combination"
# 0 标记（区间允许时）的如实记录：原因随诊断落盘，不得留空、不得降格硬凑
ZERO_MARKS_REASON = (
    "本轮如实产出 0 个进入生产标记：组合内无达标条目（门禁违规项或分数未达阈值），"
    "不降格硬凑（SC-010）"
)


def _require_interval(value, where: str, *, minimum_lower: int = 0) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or value[0] < minimum_lower
        or value[0] > value[1]
    ):
        raise DevConfigError(f"{where} 缺失或区间非法（{value!r}），拒绝启动（FR-012）")
    return int(value[0]), int(value[1])


class SlateCombinationEvaluator(Evaluator):
    """组合层门禁（确定性、零成本）：重复率、条目数上限、进入生产标记数量与指向。"""

    def __init__(self, *, slate_entries, production_marks, max_direction_repeat_rate) -> None:
        self._slate_interval = _require_interval(slate_entries, "dev.slate", minimum_lower=1)
        self._marks_interval = _require_interval(production_marks, "dev.production_marks")
        if (
            isinstance(max_direction_repeat_rate, bool)
            or not isinstance(max_direction_repeat_rate, (int, float))
            or not 0.0 <= float(max_direction_repeat_rate) <= 1.0
        ):
            raise DevConfigError(
                "dev.combination.max_direction_repeat_rate 缺失或非法"
                f"（{max_direction_repeat_rate!r}），拒绝启动（FR-012）"
            )
        self._max_repeat_rate = float(max_direction_repeat_rate)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(
                    {
                        "slate_entries": list(self._slate_interval),
                        "production_marks": list(self._marks_interval),
                        "max_direction_repeat_rate": self._max_repeat_rate,
                    }
                )
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        slate = context["artifact"]
        direction_ids = slate.direction_ids()
        count = len(direction_ids)
        distinct = len(set(direction_ids))
        repeat_rate = 0.0 if count == 0 else (count - distinct) / count

        marks = list(slate.produce_ids())
        known = set(direction_ids)
        dangling_marks = [mark for mark in marks if mark not in known]

        lower, upper = self._marks_interval
        violations: list[str] = []
        if repeat_rate > self._max_repeat_rate:
            violations.append(
                f"方向重复率 {repeat_rate:.4f} 超过形态上限 {self._max_repeat_rate}"
                f"（条目 {count} 条、去重 {distinct} 条）"
            )
        if count > self._slate_interval[1]:
            violations.append(f"条目数 {count} 超出形态上限 {self._slate_interval[1]}")
        if not lower <= len(marks) <= upper:
            violations.append(f"进入生产标记数 {len(marks)} 不在形态区间 [{lower}, {upper}]")
        violations.extend(f"标记指向组合内不存在的条目：{mark}" for mark in dangling_marks)
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "entry_count": count,
                "direction_count": distinct,
                "direction_repeat_rate": repeat_rate,
                "max_direction_repeat_rate": self._max_repeat_rate,
                "production_marks": marks,
                "production_marks_interval": [lower, upper],
                "marks_reason": "" if marks else ZERO_MARKS_REASON,
                "dangling_marks": dangling_marks,
                "violations": violations,
            },
        )
