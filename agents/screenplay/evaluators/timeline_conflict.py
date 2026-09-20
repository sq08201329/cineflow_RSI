"""时间线冲突代理评估器：proxy.timeline_conflict（连续分量，C9）。

场景顺序 vs 剧内时间戳（`scenes[].time_marker`，分钟）的单调性机检：场景序列必须
**非降序**（时间戳相等 = 同时/紧接，合法）；任一回退（`time_marker < 前序`）即一处
时间线冲突，逐条诊断（含前序场景与差值）。得分 = 1 − 冲突场景数 ÷ 场景数（冲突占比，
定点 6 位）——冲突越集中在少数场景，说明剧本脉络越接近可用。

闪回等非线性叙事在本口径下同样计为冲突（如实诊断、不自动豁免）：本代理是确定性
启发式（实现哈希即版本，原则一），非线性叙事的豁免权属人工策略与判据决议，不由
评估器私自放行（原则六）。
"""

from agents.screenplay.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.timeline_conflict"


class TimelineConflictEvaluator(Evaluator):
    """时间线单调性代理（确定性、零成本；无配置参数，版本 = 实现哈希）。"""

    def __init__(self) -> None:
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        script = context["artifact"]
        timeline = [
            {"scene_id": scene.scene_id, "time_marker": scene.time_marker}
            for scene in script.scenes
        ]
        conflicts: list[dict] = []
        previous: dict | None = None
        for entry in timeline:
            if previous is not None and entry["time_marker"] < previous["time_marker"]:
                conflicts.append(
                    {
                        "scene_id": entry["scene_id"],
                        "previous_scene_id": previous["scene_id"],
                        "previous_time_marker": previous["time_marker"],
                        "time_marker": entry["time_marker"],
                        "delta": entry["time_marker"] - previous["time_marker"],
                    }
                )
            previous = entry
        ratio = len(conflicts) / len(timeline) if timeline else 0.0
        return EvalResult(
            score=quantize_score(max(0.0, 1.0 - ratio)),
            diagnostics={
                "applicable": True,
                "scene_count": len(timeline),
                "timeline": timeline,
                "conflicts": conflicts,
                "conflict_ratio": round(ratio, 6),
                "violations": [
                    f"时间线回退：场景 {item['scene_id']} 的 {item['time_marker']} 分钟 < "
                    f"前序 {item['previous_scene_id']} 的 {item['previous_time_marker']} 分钟"
                    for item in conflicts
                ],
            },
        )
