"""响度合规评估器：rule.loudness_compliance（硬规则门禁，C4）。

测量 = 纯 numpy 简化 BS.1770（research 决策 3）：积分能量 + 固定 -0.691
K 加权偏移近似，定点 6 位小数输出；分档对照 configs `sound.loudness`
（dialogue/sfx/music，gen_type 映射分档）；静音/极短（<100ms）→ "不适用"
注明（gate 不误杀、不伪造得分，规格边界情况）。
"""

import math

import numpy as np

from agents.sound.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.loudness_compliance"

_K_WEIGHT_OFFSET_DB = -0.691  # BS.1770 K 加权积分的固定偏移（简化近似）
_MIN_DURATION_S = 0.1  # 极短下限：<100ms 不可做积分响度
_SILENCE_ENERGY = 1e-12  # 静音能量下限

_TIER_BY_GEN_TYPE = {"tts": "dialogue", "sfx": "sfx", "music": "music"}


def measure_loudness_lufs(samples: np.ndarray | None, sample_rate: int) -> float | None:
    """简化 BS.1770 积分响度（LUFS，定点 6 位）；静音/极短 → None（不适用）。"""
    if samples is None or len(samples) < int(sample_rate * _MIN_DURATION_S):
        return None
    energy = float(np.mean(samples.astype(np.float64) ** 2))
    if energy < _SILENCE_ENERGY:
        return None
    return round(_K_WEIGHT_OFFSET_DB + 10.0 * math.log10(energy), 6)


class LoudnessComplianceEvaluator(Evaluator):
    """响度分档合规硬规则（确定性、零成本）。"""

    def __init__(self, loudness_tiers: dict) -> None:
        self._tiers = loudness_tiers
        import json

        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(json.dumps(loudness_tiers, sort_keys=True)),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        sample_rate = int(context.get("sample_rate", 16000))
        lufs = measure_loudness_lufs(context.get("samples"), sample_rate)
        if lufs is None:
            return EvalResult(
                score=1.0,  # 不适用不伪造违规：gate 放行并注明（规格边界情况）
                diagnostics={
                    "applicable": False,
                    "note": "静音/极短音频，响度不适用（不伪造得分）",
                },
            )
        gen_type = context.get("gen_type", "tts")
        tier = _TIER_BY_GEN_TYPE.get(gen_type, "dialogue")
        band = self._tiers[tier]
        target = float(band["target_lufs"])
        tolerance = float(band["tolerance"])
        deviation = round(lufs - target, 6)
        violations = []
        if abs(deviation) > tolerance:
            violations.append(
                f"响度 {lufs} LUFS 超出 {tier} 档 {target}±{tolerance}（偏差 {deviation:+.6f}）"
            )
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "tier": tier,
                "measured_lufs": lufs,
                "target_lufs": target,
                "tolerance": tolerance,
                "deviation_lufs": deviation,
                "violations": violations,
            },
        )
