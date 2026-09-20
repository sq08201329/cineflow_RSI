"""节拍结构门禁评估器：rule.beat_structure（硬规则门禁，C4）。

工件结构化标记的节拍清单对照配置节拍表（`screenplay.beat_sheet`，与执行前校验共用
单一事实源）：
- **关键节拍存在性**：required 节拍逐条存在（缺一即判 0，逐条列出缺口）；
- **结构可解析**：工件节拍必须全部 ∈ 配置节拍表（表外节拍无从对照判定）；
- **三幕/序列结构**：工件必须覆盖配置涉幕（三幕齐备），且节拍所属幕按配置幕序
  非回退（幕序回退 = 序列结构不可解析）。
任一违规 → gate 判 0（要件缺失即不可行解）。可选节拍（required=False）不参与
存在性要求——**不臆造**超出配置的结构约束（原则六）。
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

EVALUATOR_ID = "rule.beat_structure"


class BeatStructureEvaluator(Evaluator):
    """节拍表结构完整性门禁（确定性、零成本；节拍表全配置驱动）。"""

    def __init__(self, beat_sheet) -> None:
        if not isinstance(beat_sheet, (list, tuple)) or not beat_sheet:
            raise ScreenplayConfigError(
                "rule.beat_structure 缺节拍表（beat_sheet），拒绝启动——不允许静默放过门禁（原则五）"
            )
        self._sheet = [dict(beat) for beat in beat_sheet]
        self._known = frozenset(beat["beat_id"] for beat in self._sheet)
        self._required = tuple(beat["beat_id"] for beat in self._sheet if beat["required"])
        # 幕序与涉幕取自配置节拍表（单一事实源；顺序即判定的"序列"依据）
        self._acts = tuple(dict.fromkeys(beat["act"] for beat in self._sheet))
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(self._sheet, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def _act_rank(self, act: str) -> int | None:
        return self._acts.index(act) if act in self._acts else None

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        script = context["artifact"]
        beat_ids = script.beat_ids()
        beat_acts = [beat.act for beat in script.beats]
        missing_required = [beat_id for beat_id in self._required if beat_id not in beat_ids]
        unknown_beats = [beat_id for beat_id in beat_ids if beat_id not in self._known]
        observed_acts = {beat.act for beat in script.beats}
        missing_acts = [act for act in self._acts if act not in observed_acts]

        regressions: list[str] = []
        rank = -1
        for index, beat in enumerate(script.beats, start=1):
            current = self._act_rank(beat.act)
            if current is None:
                continue  # 表外节拍已按 unknown_beats 违规（不重复计）
            if current < rank:
                regressions.append(
                    f"第 {index} 个节拍 {beat.beat_id} 的幕 {beat.act} 相对前序回退"
                    f"（配置幕序 {list(self._acts)}）"
                )
            rank = max(rank, current)

        violations: list[str] = []
        if missing_required:
            violations.append(f"缺关键节拍（required）：{missing_required}")
        if unknown_beats:
            violations.append(f"节拍不在配置节拍表内：{unknown_beats}（结构不可解析）")
        if missing_acts:
            violations.append(f"幕缺失：{missing_acts}（三幕/序列结构不完整）")
        violations.extend(f"幕序回退：{item}" for item in regressions)
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "beat_count": len(script.beats),
                "required_beat_count": len(self._required),
                "required_beats": list(self._required),
                "beat_ids": list(beat_ids),
                "act_sequence": beat_acts,
                "missing_required": missing_required,
                "unknown_beats": unknown_beats,
                "missing_acts": missing_acts,
                "regressions": regressions,
                "violations": violations,
            },
        )
