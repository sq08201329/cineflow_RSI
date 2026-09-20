"""声音规则评估器测试（功能 006 / T616，先于实现编写）。

C4 响度合规（gate）：纯 numpy 简化 BS.1770（积分能量 + K 加权偏移，定点 6 位）；
分档 dialogue -27±2 / sfx -30±3 / music -25±3；-27.5 LUFS 对白通过、-20 LUFS 判 0；
静音/极短"不适用"注明不伪造得分。
C5 音画同步（gate）：event_times vs TimingSheet，max |偏差| ≤ av_sync_threshold_ms；
80ms 过、200ms 判 0；纯音乐无事件"不适用"注明。
"""

import io
import wave
from pathlib import Path

import numpy as np
import pytest
import yaml

from agents.sound.audio import decode_wav_samples, synthesize_wav
from agents.sound.evaluators.av_sync import AvSyncEvaluator
from agents.sound.evaluators.loudness import LoudnessComplianceEvaluator, measure_loudness_lufs
from core.evaluators.base import ArtifactRef

REPO_ROOT = Path(__file__).resolve().parents[2]
_SOUND = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))["sound"]
_DIST = _SOUND["simulated_gen"]
_SR = int(_SOUND["sample_rate"])
_ARTIFACT = ArtifactRef(artifact_hash="ab" * 32)


def _wav_at_lufs(make_sound_gen_params, gen_type: str, target_lufs: float, seed: int = 7):
    """注入响度增益，把合成波形标定到目标 LUFS（先测零点再补偿）。"""
    wav0, _ = synthesize_wav(
        make_sound_gen_params(gen_type=gen_type, seed=seed, loudness_gain_db=0.0), _DIST, _SR
    )
    lufs0 = measure_loudness_lufs(decode_wav_samples(wav0), _SR)
    gain = target_lufs - lufs0
    params = make_sound_gen_params(gen_type=gen_type, seed=seed, loudness_gain_db=gain)
    wav_bytes, metadata = synthesize_wav(params, _DIST, _SR)
    return params, wav_bytes, metadata


def _loudness_ctx(gen_type, wav_bytes, metadata, params):
    return {
        "samples": decode_wav_samples(wav_bytes),
        "sample_rate": _SR,
        "gen_type": gen_type,
        "metadata": metadata,
        "gen_params": params,
    }


@pytest.fixture()
def loudness_evaluator(sound_config):
    return LoudnessComplianceEvaluator(sound_config.loudness)


@pytest.fixture()
def av_sync_evaluator(sound_config):
    return AvSyncEvaluator(sound_config.av_sync_threshold_ms)


class Test响度合规:
    def test_对白_负27点5_LUFS_通过(self, loudness_evaluator, make_sound_gen_params):
        params, wav_bytes, metadata = _wav_at_lufs(make_sound_gen_params, "tts", -27.5)
        result = loudness_evaluator.evaluate(
            _ARTIFACT, _loudness_ctx("tts", wav_bytes, metadata, params)
        )
        assert result.score == 1.0
        assert result.diagnostics["measured_lufs"] == pytest.approx(-27.5, abs=0.01)
        assert result.diagnostics["tier"] == "dialogue"
        assert result.diagnostics["violations"] == []

    def test_对白_负20_LUFS_判0(self, loudness_evaluator, make_sound_gen_params):
        """-20 LUFS 距 -27 目标 7 > 容差 2 → gate 判 0（总分短路见 T618）。"""
        params, wav_bytes, metadata = _wav_at_lufs(make_sound_gen_params, "tts", -20.0)
        result = loudness_evaluator.evaluate(
            _ARTIFACT, _loudness_ctx("tts", wav_bytes, metadata, params)
        )
        assert result.score == 0.0
        assert result.diagnostics["violations"]

    @pytest.mark.parametrize(
        ("gen_type", "tier", "target"),
        [("tts", "dialogue", -27.0), ("sfx", "sfx", -30.0), ("music", "music", -25.0)],
    )
    def test_三类型分档(self, loudness_evaluator, make_sound_gen_params, gen_type, tier, target):
        """分档对照：目标值通过；目标 ±（容差+1）判 0。"""
        params, wav_bytes, metadata = _wav_at_lufs(make_sound_gen_params, gen_type, target)
        result = loudness_evaluator.evaluate(
            _ARTIFACT, _loudness_ctx(gen_type, wav_bytes, metadata, params)
        )
        assert result.score == 1.0
        assert result.diagnostics["tier"] == tier

        tolerance = float(_SOUND["loudness"][tier]["tolerance"])
        params2, wav2, meta2 = _wav_at_lufs(
            make_sound_gen_params, gen_type, target + tolerance + 1.0
        )
        result2 = loudness_evaluator.evaluate(
            _ARTIFACT, _loudness_ctx(gen_type, wav2, meta2, params2)
        )
        assert result2.score == 0.0

    def test_静音不适用注明(self, loudness_evaluator):
        silent = np.zeros(_SR, dtype=np.float64)
        result = loudness_evaluator.evaluate(
            _ARTIFACT, {"samples": silent, "sample_rate": _SR, "gen_type": "tts", "metadata": {}}
        )
        assert result.score == 1.0  # gate 不误杀
        assert result.diagnostics["applicable"] is False
        assert "不适用" in result.diagnostics["note"]

    def test_极短音频不适用注明(self, loudness_evaluator):
        short = np.ones(_SR // 100, dtype=np.float64) * 0.1  # 10ms < 100ms 下限
        result = loudness_evaluator.evaluate(
            _ARTIFACT, {"samples": short, "sample_rate": _SR, "gen_type": "tts", "metadata": {}}
        )
        assert result.diagnostics["applicable"] is False

    def test_测量定点6位且重测一致(self, loudness_evaluator, make_sound_gen_params):
        params, wav_bytes, metadata = _wav_at_lufs(make_sound_gen_params, "tts", -27.0)
        ctx = _loudness_ctx("tts", wav_bytes, metadata, params)
        first = loudness_evaluator.evaluate(_ARTIFACT, ctx)
        second = loudness_evaluator.evaluate(_ARTIFACT, ctx)
        assert first.score == second.score
        assert first.diagnostics == second.diagnostics  # SC-004 重评估逐位一致
        assert first.diagnostics["measured_lufs"] == round(
            first.diagnostics["measured_lufs"], 6
        )


class Test音画同步:
    def _ctx(self, event_times, make_timing_sheet):
        sheet = make_timing_sheet(
            utterances=[{"text": "台词", "start_ms": 0, "end_ms": 1000}], effects=[]
        )
        return {
            "metadata": {"event_times_ms": event_times},
            "timing_sheet": sheet,
            "gen_type": "tts",
        }

    def test_偏差80ms通过(self, av_sync_evaluator, make_timing_sheet):
        result = av_sync_evaluator.evaluate(_ARTIFACT, self._ctx([80.0], make_timing_sheet))
        assert result.score == 1.0
        assert result.diagnostics["max_deviation_ms"] == pytest.approx(80.0)
        assert result.diagnostics["threshold_ms"] == 120

    def test_偏差200ms判0(self, av_sync_evaluator, make_timing_sheet):
        result = av_sync_evaluator.evaluate(_ARTIFACT, self._ctx([200.0], make_timing_sheet))
        assert result.score == 0.0
        assert result.diagnostics["max_deviation_ms"] == pytest.approx(200.0)
        assert result.diagnostics["violations"]

    def test_多事件取最大偏差(self, av_sync_evaluator, make_timing_sheet):
        result = av_sync_evaluator.evaluate(
            _ARTIFACT, self._ctx([0.0, 100.0], make_timing_sheet)
        )
        assert result.score == 1.0
        assert result.diagnostics["max_deviation_ms"] == pytest.approx(100.0)

    def test_纯音乐无事件不适用(self, av_sync_evaluator, make_timing_sheet):
        result = av_sync_evaluator.evaluate(_ARTIFACT, self._ctx([], make_timing_sheet))
        assert result.score == 1.0  # gate 不误杀
        assert result.diagnostics["applicable"] is False
        assert "不适用" in result.diagnostics["note"]

    def test_事件对音效起止同样比对(self, av_sync_evaluator, make_timing_sheet):
        """expected = 台词 start + 音效 at_ms；事件对最近期望点取偏差。"""
        sheet = make_timing_sheet(
            utterances=[{"text": "台词", "start_ms": 0, "end_ms": 1000}],
            effects=[{"kind": "door", "at_ms": 1100}],
        )
        ctx = {
            "metadata": {"event_times_ms": [0.0, 1210.0]},
            "timing_sheet": sheet,
            "gen_type": "sfx",
        }
        result = av_sync_evaluator.evaluate(_ARTIFACT, ctx)
        assert result.diagnostics["max_deviation_ms"] == pytest.approx(110.0)
        assert result.score == 1.0
