"""确定性模拟声音生成器（research 决策 2，开发/CI 默认实现，C10）。

三类型模拟器共用同一条合成路径（T608 synthesize_wav）：参数种子 → numpy
波形 → PCM16 wav；同参数两次 generate 逐字节一致（SC-002）；
estimated ≥ actual（estimated_cost_usd / cost_per_clip_usd 同 004 纪律）。
"""

from agents.sound.audio import synthesize_wav
from agents.sound.platform.base import GeneratedAudio


class _SimulatedSoundGenBase:
    """三类型模拟器共用实现：gen_type 由子类声明（成本分账粒度）。"""

    gen_type: str = ""

    def __init__(self, distribution: dict, sample_rate: int) -> None:
        self._dist = distribution
        self._sample_rate = sample_rate

    def estimate(self, params: dict) -> float:
        return float(self._dist["estimated_cost_usd"])

    def generate(self, params: dict) -> GeneratedAudio:
        wav_bytes, metadata = synthesize_wav(params, self._dist, self._sample_rate)
        actual = min(float(self._dist["cost_per_clip_usd"]), self.estimate(params))
        return GeneratedAudio(
            wav_bytes=wav_bytes,
            metadata=metadata,
            actual_cost_usd=actual,
        )


class SimulatedTTSGen(_SimulatedSoundGenBase):
    """TTS 模拟器：cer_injected 注入标记为该类型的评估信号。"""

    gen_type = "tts"


class SimulatedSFXGen(_SimulatedSoundGenBase):
    """音效模拟器：event_times_ms 注入标记为该类型的评估信号。"""

    gen_type = "sfx"


class SimulatedMusicGen(_SimulatedSoundGenBase):
    """配乐模拟器：emotion_vector 注入标记为该类型的评估信号。"""

    gen_type = "music"
