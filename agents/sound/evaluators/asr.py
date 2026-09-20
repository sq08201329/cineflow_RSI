"""ASR 转写代理评估器：proxy.asr_transcript（连续得分，仅 TTS，C6）。

确定性启发式代理（research 决策 4，诚实边界：实现哈希即版本，真实 ASR
服务替换 = 升版本）：模拟路径从工件元数据 cer_injected（种子决定）按
gen_params.seed 确定性"转写"复现（对参考台词做种子驱动的确定性错字替换），
score = 1 − min(1, CER / cer_cap)（cer_cap 形态配置）；
静音/过短能量 → diagnostics 标低置信；非 TTS 工件 → "不适用"跳过注明。
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

EVALUATOR_ID = "proxy.asr_transcript"

_SILENCE_ENERGY = 1e-12  # 低置信能量下限（静音/过短）
_MIN_SAMPLES = 1600  # 低置信长度下限（16kHz × 100ms）


def deterministic_transcript(text: str, cer: float, seed: int) -> str:
    """确定性"转写"复现：按 seed 对参考文本做 ⌈cer×len⌉ 处确定性错字替换。"""
    if not text or cer <= 0.0:
        return text
    rng = np.random.default_rng(int(seed))
    chars = list(text)
    n_errors = min(len(chars), int(round(cer * len(chars))))
    if n_errors == 0:
        return text
    positions = sorted(rng.choice(len(chars), size=n_errors, replace=False).tolist())
    for pos in positions:
        chars[pos] = "□"  # 错字占位（确定性，不引入随机字符表）
    return "".join(chars)


class AsrTranscriptEvaluator(Evaluator):
    """ASR 转写代理（确定性、零成本）。"""

    def __init__(self, cer_cap: float) -> None:
        self._cer_cap = float(cer_cap)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(json.dumps({"cer_cap": self._cer_cap})),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        if context.get("gen_type") != "tts":
            return EvalResult(
                score=0.0,
                diagnostics={
                    "applicable": False,
                    "note": "非 TTS 工件，ASR 不适用（分量跳过，合成按适用归一）",
                },
            )
        metadata = context.get("metadata", {})
        cer = float(metadata.get("cer_injected", 0.0))
        gen_params = context.get("gen_params", {})
        reference = str(gen_params.get("text", ""))
        transcript = deterministic_transcript(reference, cer, int(gen_params.get("seed", 0)))

        samples = context.get("samples")
        low_confidence = (
            samples is None
            or len(samples) < _MIN_SAMPLES
            or float(np.mean(samples.astype(np.float64) ** 2)) < _SILENCE_ENERGY
        )
        score = 1.0 - min(1.0, cer / self._cer_cap)
        return EvalResult(
            score=score,
            diagnostics={
                "applicable": True,
                "cer": cer,
                "cer_cap": self._cer_cap,
                "reference": reference,
                "transcript": transcript,
                "low_confidence": low_confidence,
            },
        )
