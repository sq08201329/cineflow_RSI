"""声音代理评估器测试（功能 006 / T617，先于实现编写）。

C6 proxy.asr_transcript（仅 TTS）：cer_injected → 确定性"转写"复现 →
score = 1 − min(1, CER / cer_cap)，映射误差 < 1e-6；同工件重评估逐位一致；
低置信（静音/过短）diagnostics 标注；非 TTS 跳过注明。
C7 proxy.emotion_music_match（仅 music）：种子决定的谐波特征向量 vs 情绪基调
向量余弦距离映射得分；匹配 vs 背离分差显著；距离超校准带标低置信。
"""

import numpy as np
import pytest

from agents.sound.evaluators.asr import AsrTranscriptEvaluator
from agents.sound.evaluators.emotion import EmotionMusicMatchEvaluator, emotion_feature_vector
from core.evaluators.base import ArtifactRef

_ARTIFACT = ArtifactRef(artifact_hash="cd" * 32)
_TEXT = "你终于来了，我等了很久。"


@pytest.fixture()
def asr_evaluator(sound_config):
    return AsrTranscriptEvaluator(sound_config.asr["cer_cap"])


@pytest.fixture()
def emotion_evaluator(sound_config):
    return EmotionMusicMatchEvaluator(sound_config.emotion["calibration_band"])


def _samples(energy: float = 0.1, n: int = 32000) -> np.ndarray:
    return np.full(n, energy**0.5, dtype=np.float64)


def _asr_ctx(cer, gen_type="tts", seed=7, samples=None):
    return {
        "gen_type": gen_type,
        "metadata": {"cer_injected": cer},
        "gen_params": {"seed": seed, "text": _TEXT},
        "samples": _samples() if samples is None else samples,
        "sample_rate": 16000,
    }


class TestAsr转写代理:
    def test_映射口径误差小于1e_6(self, asr_evaluator, sound_config):
        cer_cap = sound_config.asr["cer_cap"]
        for cer in (0.0, 0.05, 0.1, 0.19):
            result = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(cer))
            expected = 1.0 - min(1.0, cer / cer_cap)
            assert result.score == pytest.approx(expected, abs=1e-6)

    def test_cer_超_cap_归零(self, asr_evaluator):
        result = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.4))  # cap 0.2
        assert result.score == 0.0

    def test_确定性转写复现且逐位一致(self, asr_evaluator):
        """同工件（同 seed/cer/text）重评估：转写与得分逐位一致（SC-004）。"""
        first = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.15))
        second = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.15))
        assert first.score == second.score
        assert first.diagnostics == second.diagnostics
        assert first.diagnostics["reference"] == _TEXT
        assert first.diagnostics["transcript"] != _TEXT  # cer>0 → 转写有确定性错字
        assert first.diagnostics["cer"] == pytest.approx(0.15)

    def test_不同_cer_转写错字数不同(self, asr_evaluator):
        clean = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.0))
        noisy = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.3))
        assert clean.diagnostics["transcript"] == _TEXT
        assert noisy.diagnostics["transcript"] != clean.diagnostics["transcript"]

    @pytest.mark.parametrize("gen_type", ["sfx", "music"])
    def test_非_TTS_跳过注明(self, asr_evaluator, gen_type):
        result = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.1, gen_type=gen_type))
        assert result.diagnostics["applicable"] is False
        assert "不适用" in result.diagnostics["note"]

    def test_静音低置信标注(self, asr_evaluator):
        silent = np.zeros(32000, dtype=np.float64)
        result = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.1, samples=silent))
        assert result.diagnostics["low_confidence"] is True

    def test_正常能量不低置信(self, asr_evaluator):
        result = asr_evaluator.evaluate(_ARTIFACT, _asr_ctx(0.1))
        assert result.diagnostics.get("low_confidence", False) is False


class Test情绪配乐匹配代理:
    def _ctx(self, emotion_vector, gen_type="music", seed=7):
        return {
            "gen_type": gen_type,
            "metadata": {"emotion_vector": list(emotion_vector)},
            "gen_params": {"seed": seed},
        }

    def test_匹配_vs_背离分差显著(self, emotion_evaluator):
        feature = emotion_feature_vector(7, dims=2)
        matched = emotion_evaluator.evaluate(_ARTIFACT, self._ctx(feature))
        divergent = emotion_evaluator.evaluate(_ARTIFACT, self._ctx([-f for f in feature]))
        assert matched.score == pytest.approx(1.0, abs=1e-6)  # 余弦 1 → 满分
        assert divergent.score == pytest.approx(0.0, abs=1e-6)  # 余弦 -1 → 零分
        assert matched.score - divergent.score > 0.5

    def test_距离超校准带标低置信(self, emotion_evaluator, sound_config):
        feature = emotion_feature_vector(7, dims=2)
        divergent = emotion_evaluator.evaluate(_ARTIFACT, self._ctx([-f for f in feature]))
        assert divergent.diagnostics["distance"] > sound_config.emotion["calibration_band"]
        assert divergent.diagnostics["low_confidence"] is True
        matched = emotion_evaluator.evaluate(_ARTIFACT, self._ctx(feature))
        assert matched.diagnostics["low_confidence"] is False

    def test_同工件重评估逐位一致(self, emotion_evaluator):
        ctx = self._ctx([0.6, 0.8])
        first = emotion_evaluator.evaluate(_ARTIFACT, ctx)
        second = emotion_evaluator.evaluate(_ARTIFACT, ctx)
        assert first.score == second.score
        assert first.diagnostics == second.diagnostics

    @pytest.mark.parametrize("gen_type", ["tts", "sfx"])
    def test_非_music_跳过注明(self, emotion_evaluator, gen_type):
        result = emotion_evaluator.evaluate(_ARTIFACT, self._ctx([0.5, 0.5], gen_type=gen_type))
        assert result.diagnostics["applicable"] is False
        assert "不适用" in result.diagnostics["note"]

    def test_特征向量确定性(self):
        assert emotion_feature_vector(42, dims=3) == emotion_feature_vector(42, dims=3)
        assert emotion_feature_vector(42, dims=3) != emotion_feature_vector(43, dims=3)
