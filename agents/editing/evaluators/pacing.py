"""节奏曲线代理评估器：proxy.pacing_curve（连续分量，C7 / research 决策 4）。

成片镜头时长序列按相对位置分段（镜头 i 的相对位置 = (i+0.5)/N 落入
PacingBaseline.segments 的 span [lo, hi)）统计均值/总体方差，与基准段做
加权欧氏距离 d → score = 1 − min(1, d / d_cap)，quantize 6 位定点收口。
空段（无镜头落入）跳过不臆造统计。缺基准拒绝启动（配置纪律，原则五）——
短剧形态切换 = 段权重调整零代码（附录 A"前 3 秒留存权重上调"= 前段 weight）。
"""

import json
import math

from agents.editing.config import EditingConfigError
from agents.editing.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.pacing_curve"


class PacingCurveEvaluator(Evaluator):
    """分段节奏基准距离代理（确定性、零成本）。"""

    def __init__(self, pacing_baseline: dict | None) -> None:
        if not isinstance(pacing_baseline, dict) or not pacing_baseline.get("segments"):
            raise EditingConfigError(
                "proxy.pacing_curve 缺基准曲线（pacing_baseline.segments），拒绝启动"
                "——不允许静默无基准打分（原则五）"
            )
        self._d_cap = float(pacing_baseline["d_cap"])
        self._segments = [
            {
                "span": (float(s["span"][0]), float(s["span"][1])),
                "mean_ms": float(s["mean_ms"]),
                "var_ms": float(s["var_ms"]),
                "weight": float(s["weight"]),
            }
            for s in pacing_baseline["segments"]
        ]
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(pacing_baseline, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        durations = [float(d) for d in artifact.metadata["shot_durations_ms"]]
        n = len(durations)
        distance_sq = 0.0
        segment_stats = []
        for segment in self._segments:
            lo, hi = segment["span"]
            shots = [d for i, d in enumerate(durations) if lo <= (i + 0.5) / n < hi]
            if not shots:
                # 空段跳过（不臆造统计）；shot_count=0 如实注明
                segment_stats.append(
                    {"span": [lo, hi], "shot_count": 0, "weight": segment["weight"]}
                )
                continue
            mean = sum(shots) / len(shots)
            var = sum((d - mean) ** 2 for d in shots) / len(shots)  # 总体方差（定点口径）
            distance_sq += segment["weight"] * (
                (mean - segment["mean_ms"]) ** 2 + (var - segment["var_ms"]) ** 2
            )
            segment_stats.append(
                {
                    "span": [lo, hi],
                    "shot_count": len(shots),
                    "mean_ms": mean,
                    "var_ms": var,
                    "weight": segment["weight"],
                }
            )
        distance = math.sqrt(distance_sq)
        return EvalResult(
            score=quantize_score(1.0 - min(1.0, distance / self._d_cap)),
            diagnostics={
                "applicable": True,
                "shot_count": n,
                "segments": segment_stats,
                "distance": distance,
                "d_cap": self._d_cap,
            },
        )
