"""情绪-配乐匹配代理评估器：proxy.emotion_music_match（连续得分，仅 music，C7）。

确定性启发式代理（research 决策 9，实现哈希即版本）：配乐谐波特征向量
（参数种子决定的调式/速度/音色代理）vs 情绪基调标签向量（工件元数据
emotion_vector 注入）的余弦距离映射得分（score = (1 + cos) / 2）；
距离超校准带（形态配置）→ diagnostics 标低置信（不进盲评 top-k）；
非 music 工件 → "不适用"跳过注明。
"""

import json

import numpy as np

from agents.sound.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "proxy.emotion_music_match"


def emotion_feature_vector(seed: int, dims: int = 4) -> list[float]:
    """参数种子决定的配乐特征向量（确定性单位向量，调式/速度/音色代理）。"""
    rng = np.random.default_rng(int(seed))
    vec = rng.standard_normal(dims)
    return (vec / np.linalg.norm(vec)).tolist()


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    if va.shape != vb.shape or not va.size:
        return 0.0  # 维度不符/空向量：按无相关处理（确定性，不伪造匹配）
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom < 1e-12:
        return 0.0
    return float(np.dot(va, vb) / denom)


class EmotionMusicMatchEvaluator(Evaluator):
    """情绪-配乐匹配代理（确定性、零成本）。"""

    def __init__(self, calibration_band: float) -> None:
        self._band = float(calibration_band)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(json.dumps({"calibration_band": self._band})),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        if context.get("gen_type") != "music":
            return EvalResult(
                score=0.0,
                diagnostics={
                    "applicable": False,
                    "note": "非 music 工件，情绪匹配不适用（分量跳过，合成按适用归一）",
                },
            )
        metadata = context.get("metadata", {})
        target = list(metadata.get("emotion_vector", []))
        seed = int(context.get("gen_params", {}).get("seed", 0))
        feature = emotion_feature_vector(seed, dims=len(target))
        cosine = _cosine(feature, target)
        distance = 1.0 - cosine
        low_confidence = distance > self._band
        score = round((1.0 + cosine) / 2.0, 6)
        return EvalResult(
            score=score,
            diagnostics={
                "applicable": True,
                "cosine": round(cosine, 6),
                "distance": round(distance, 6),
                "calibration_band": self._band,
                "low_confidence": low_confidence,
            },
        )
