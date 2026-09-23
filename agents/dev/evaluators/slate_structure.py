"""立项组合结构门禁：rule.slate_structure（硬规则门禁，契约 C7）。

工件结构完整性对照形态配置（`dev.slate`）：

- **条目数 ∈ 配置区间**：越界即判 0（实测条目数与区间随诊断点名，不只回一个 0）；
- **方向标识组合内唯一**：重复即判 0（点名重复的方向标识）；
- **必填要点齐备**：每条方向的 genre / constraints / characters 非空（可移交下游的剧本输入
  要点，FR-011）；"字段在但取值为空"同此判 0，并点名缺失字段名。

任一违规 → gate 判 0（不可行解）且诊断逐项点名；失败以
`EvalResult(score=0.0, diagnostics.violations)` 返回、**不抛异常**（先例
`agents/screenplay/evaluators/beat_structure.py:84`）。阈值全配置化，缺项即装配期报错
（不静默取码内默认）。组合条目**唯一性与区间不由工件构造期代判**（越界即门禁判 0）。
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

EVALUATOR_ID = "rule.slate_structure"
# 可移交下游的剧本输入要点（FR-011 判据：三字段非空）
REQUIRED_ENTRY_FIELDS = ("genre", "constraints", "characters")


class SlateStructureEvaluator(Evaluator):
    """立项组合结构完整性门禁（确定性、零成本；条目数区间全配置驱动）。"""

    def __init__(self, *, slate_entries) -> None:
        if (
            not isinstance(slate_entries, (list, tuple))
            or len(slate_entries) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in slate_entries)
            or slate_entries[0] < 1
            or slate_entries[0] > slate_entries[1]
        ):
            raise DevConfigError(
                "rule.slate_structure 缺条目数区间（dev.slate）或区间非法，拒绝启动"
                "——不允许静默放过门禁（FR-012）"
            )
        self._interval = (int(slate_entries[0]), int(slate_entries[1]))
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(json.dumps(self._interval)),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        slate = context["artifact"]
        direction_ids = slate.direction_ids()
        count = len(direction_ids)
        seen: set[str] = set()
        duplicates: list[str] = []
        for direction_id in direction_ids:
            if direction_id in seen and direction_id not in duplicates:
                duplicates.append(direction_id)
            seen.add(direction_id)
        missing_fields: dict[str, list[str]] = {}
        for entry in slate.entries:
            fields = [field for field in REQUIRED_ENTRY_FIELDS if not getattr(entry, field)]
            if fields:
                missing_fields[entry.direction_id] = fields

        lower, upper = self._interval
        violations: list[str] = []
        if not lower <= count <= upper:
            violations.append(f"条目数 {count} 不在形态区间 [{lower}, {upper}]")
        if duplicates:
            violations.append(f"方向标识组合内重复：{duplicates}")
        violations.extend(
            f"方向 {direction_id} 缺必填要点：{fields}"
            for direction_id, fields in missing_fields.items()
        )
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "entry_count": count,
                "slate_interval": [lower, upper],
                "direction_ids": list(direction_ids),
                "duplicate_direction_ids": duplicates,
                "missing_fields": missing_fields,
                "violations": violations,
            },
        )
