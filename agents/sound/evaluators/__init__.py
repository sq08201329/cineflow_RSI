"""声音四评估器包（US2）：双 gate（loudness/av_sync）+ 双 proxy（asr/emotion）。

build_sound_evaluators 为唯一装配点（loop 接线 / 契约与无偏性测试共用）：
评估器组合与权重键一一对应（evaluator_weights.sound），参数全来自 SoundConfig
（原则五：分档/阈值/cer_cap/校准带零硬编码）。
"""

from agents.sound.config import SoundConfig
from agents.sound.evaluators.asr import AsrTranscriptEvaluator
from agents.sound.evaluators.av_sync import AvSyncEvaluator
from agents.sound.evaluators.emotion import EmotionMusicMatchEvaluator
from agents.sound.evaluators.loudness import LoudnessComplianceEvaluator
from core.evaluators.base import Evaluator

__all__ = [
    "AsrTranscriptEvaluator",
    "AvSyncEvaluator",
    "EmotionMusicMatchEvaluator",
    "LoudnessComplianceEvaluator",
    "build_sound_evaluators",
]


def build_sound_evaluators(config: SoundConfig) -> list[Evaluator]:
    """按 evaluator_weights.sound 装配真实四评估器（双 gate + 双 proxy）。"""
    return [
        LoudnessComplianceEvaluator(config.loudness),
        AvSyncEvaluator(config.av_sync_threshold_ms),
        AsrTranscriptEvaluator(config.asr["cer_cap"]),
        EmotionMusicMatchEvaluator(config.emotion["calibration_band"]),
    ]
