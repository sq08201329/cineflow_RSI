"""音画同步评估器：rule.av_sync（硬规则门禁，C5）。

澄清 Q1 落点：仅时序元数据比对——工件元数据 event_times_ms（模拟器注入的
语音/音效事件时间）vs TimingSheet 期望事件（台词 start_ms + 音效 at_ms），
每个事件对最近期望点取偏差，max |偏差| ≤ av_sync_threshold_ms 通过，
否则 gate 判 0；无事件工件（纯音乐垫底）→ "不适用"注明。不做波形-画面对齐。
"""

import json

from agents.sound.evaluators._versioning import implementation_version
from agents.sound.timing import TimingSheet
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.av_sync"


class AvSyncEvaluator(Evaluator):
    """事件时间同步硬规则（确定性、零成本）。"""

    def __init__(self, threshold_ms: int) -> None:
        self._threshold_ms = int(threshold_ms)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(json.dumps({"threshold_ms": self._threshold_ms})),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        events = list(context.get("metadata", {}).get("event_times_ms", []))
        sheet: TimingSheet | None = context.get("timing_sheet")
        expected = []
        if sheet is not None:
            expected = sorted(
                [u.start_ms for u in sheet.utterances] + [e.at_ms for e in sheet.effects]
            )
        if not events or not expected:
            return EvalResult(
                score=1.0,  # 不适用不伪造违规：gate 放行并注明
                diagnostics={
                    "applicable": False,
                    "note": "无事件工件（纯音乐垫底），同步不适用（不伪造得分）",
                },
            )
        deviations = [min(abs(float(ev) - exp) for exp in expected) for ev in events]
        max_deviation = max(deviations)
        violations = []
        if max_deviation > self._threshold_ms:
            violations.append(
                f"事件时间最大偏差 {max_deviation:.1f}ms > 阈值 {self._threshold_ms}ms"
            )
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "threshold_ms": self._threshold_ms,
                "max_deviation_ms": max_deviation,
                "deviations_ms": deviations,
                "event_count": len(events),
                "violations": violations,
            },
        )
