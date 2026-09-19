"""一致性评估器：proxy.identity_consistency（research 决策 4）。

每帧感知哈希（64 位 dhash）作嵌入，相邻帧汉明距离均值映射一致性得分；
单镜头（shots ≤ 1）返回 1.0 并在 diagnostics 注明（规格边界情况）。
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

EVALUATOR_ID = "proxy.identity_consistency"


def _dhash64(gray_frame: np.ndarray) -> np.ndarray:
    """64 位 dhash：9×8 灰度横向差分（纯 numpy，确定性）。"""
    h, w = gray_frame.shape
    yi = (np.arange(8) * h // 8).astype(int)
    xi = (np.arange(9) * w // 9).astype(int)
    small = gray_frame[np.ix_(yi, xi)].astype(np.int16)
    return (np.diff(small, axis=1) > 0).flatten()  # (64,) bool


class IdentityConsistencyEvaluator(Evaluator):
    """跨帧主体一致性代理（单镜头满分注明）。"""

    def __init__(self, sampling_spec: dict) -> None:
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(sampling_spec_hash(sampling_spec)),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        shots = int(context.get("gen_params", {}).get("shots", 1))
        frames = context["samples"].frames_gray
        if shots <= 1:
            return EvalResult(
                score=1.0,
                diagnostics={
                    "note": "single_shot：单镜头一致性恒满分（规格边界）",
                    "mean_distance": 0.0,
                },
            )
        hashes = np.stack([_dhash64(f) for f in frames])
        distances = [
            float(np.count_nonzero(a != b)) / 64.0
            for a, b in zip(hashes, hashes[1:], strict=False)  # 相邻帧对
        ]
        mean_distance = float(np.mean(distances)) if distances else 0.0
        return EvalResult(
            score=quantize_score(1.0 - mean_distance),
            diagnostics={"mean_distance": mean_distance, "shots": shots, "note": "multi_shot"},
        )
