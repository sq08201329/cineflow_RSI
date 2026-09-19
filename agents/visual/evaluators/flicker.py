"""闪烁/伪影评估器：proxy.flicker（research 决策 4）。

相邻帧亮度差方差 + 高频伪影能量，反向映射（越稳定越高分）；
退化输入（全黑/静止）数值保护，不产生 NaN。
"""

import numpy as np

from agents.visual.evaluators._versioning import implementation_version
from agents.visual.frames import sampling_spec_hash
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.flicker"


class FlickerEvaluator(Evaluator):
    """帧间稳定性代理：确定性纯 numpy。"""

    def __init__(self, sampling_spec: dict) -> None:
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(sampling_spec_hash(sampling_spec)),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        frames = context["samples"].frames_gray.astype(np.float64)

        # 帧间亮度抖动的标准差（闪烁强度：黑白交替/std 大，静止/std 零）
        brightness = frames.mean(axis=(1, 2))
        flicker_var = float(brightness.std()) if len(brightness) else 0.0

        # 高频伪影能量：帧内二阶差分均值（噪声/块效应强度）
        if frames.shape[2] > 2:
            second = np.abs(frames[:, :, 2:] - 2 * frames[:, :, 1:-1] + frames[:, :, :-2])
            artifact_energy = float(second.mean())
        else:
            artifact_energy = 0.0

        # 反向映射：越稳定越高分（分母 +1 保护除零）
        score = 1.0 / (1.0 + flicker_var / 25.0 + artifact_energy / 50.0)
        return EvalResult(
            score=quantize_score(score),
            diagnostics={
                "flicker_var": flicker_var,
                "artifact_energy": artifact_energy,
            },
        )
