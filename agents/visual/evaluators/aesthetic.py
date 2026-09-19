"""美学统计代理评估器：proxy.aesthetic（research 决策 4，启发式实现）。

纯 numpy 帧统计合成：亮度分布、对比度、色彩丰富度、Laplacian 锐度加权映射
[0,1]，quantize 定点归一。版本号 = 1.0.0+<实现文件与采样参数哈希前12位>。
一期不接真实美学模型（原则六：版本号如实标注启发式实现）。
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

EVALUATOR_ID = "proxy.aesthetic"


class AestheticEvaluator(Evaluator):
    """美学统计代理：确定性、零凭证、零外部模型。"""

    def __init__(self, sampling_spec: dict) -> None:
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(sampling_spec_hash(sampling_spec)),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        rgb = context["samples"].frames_rgb.astype(np.float64)
        gray = context["samples"].frames_gray.astype(np.float64)

        # 分量全部做数值保护（退化输入除零即 0，不产生 NaN）
        brightness = float(gray.mean() / 255.0)
        contrast = float(min(1.0, gray.std() / 64.0))
        rg = rgb[..., 0] - rgb[..., 1]
        yb = 0.5 * (rgb[..., 0] + rgb[..., 1]) - rgb[..., 2]
        colorfulness = float(min(1.0, (rg.std() + yb.std()) / 64.0))
        lap = np.abs(np.diff(gray, axis=2)).mean() if gray.shape[2] > 1 else 0.0
        sharpness = float(min(1.0, lap / 16.0))

        score = (
            0.2 * (1.0 - abs(brightness - 0.5) * 2)  # 亮度适中为佳
            + 0.3 * contrast
            + 0.25 * colorfulness
            + 0.25 * sharpness
        )
        return EvalResult(
            score=quantize_score(score),
            diagnostics={
                "brightness": brightness,
                "contrast": contrast,
                "colorfulness": colorfulness,
                "sharpness": sharpness,
            },
        )
