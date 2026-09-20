"""程序化音频合成测试（功能 006 / T607，先于实现编写）。

research 决策 2 落点：参数种子 → numpy 正弦叠加（基频/谐波/包络由参数决定）→
标准库 wave PCM16；同参数逐字节复现（SC-002）；声学属性注入标记
（loudness_gain/event_times/cer_injected/情绪向量）随元数据字典返回。
"""

import io
import wave
from pathlib import Path

import numpy as np
import pytest
import yaml

from agents.sound.audio import synthesize_wav

REPO_ROOT = Path(__file__).resolve().parents[2]
_SOUND = yaml.safe_load(
    (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8")
)["sound"]
_DIST = _SOUND["simulated_gen"]
_SAMPLE_RATE = int(_SOUND["sample_rate"])


def _pcm_from_wav(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16)


class Test确定性:
    def test_同参数两次逐字节一致(self, make_sound_gen_params):
        params = make_sound_gen_params()
        wav1, meta1 = synthesize_wav(params, _DIST, _SAMPLE_RATE)
        wav2, meta2 = synthesize_wav(params, _DIST, _SAMPLE_RATE)
        assert wav1 == wav2
        assert meta1 == meta2

    def test_不同_seed_产出不同(self, make_sound_gen_params):
        wav1, _ = synthesize_wav(make_sound_gen_params(seed=1), _DIST, _SAMPLE_RATE)
        wav2, _ = synthesize_wav(make_sound_gen_params(seed=2), _DIST, _SAMPLE_RATE)
        assert wav1 != wav2

    def test_同_seed_不同注入属性波形仍一致(self, make_sound_gen_params):
        """声学属性注入是元数据标记（除响度增益），不改波形主体种子。"""
        wav1, _ = synthesize_wav(make_sound_gen_params(seed=7, cer_injected=0.0), _DIST, _SAMPLE_RATE)
        wav2, _ = synthesize_wav(make_sound_gen_params(seed=7, cer_injected=0.3), _DIST, _SAMPLE_RATE)
        assert wav1 == wav2


class Test容器规格:
    def test_采样率声道位深符合配置(self, make_sound_gen_params):
        wav_bytes, _ = synthesize_wav(make_sound_gen_params(), _DIST, _SAMPLE_RATE)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getframerate() == _SAMPLE_RATE
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # PCM16
            assert wf.getnframes() == int(
                round(_DIST["duration_seconds"] * _SAMPLE_RATE)
            )

    def test_时长由参数覆盖(self, make_sound_gen_params):
        wav_bytes, meta = synthesize_wav(
            make_sound_gen_params(duration_s=1.0), _DIST, _SAMPLE_RATE
        )
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnframes() == _SAMPLE_RATE
        assert meta["duration_s"] == pytest.approx(1.0)

    def test_波形非静音且幅值在_PCM16_域内(self, make_sound_gen_params):
        wav_bytes, _ = synthesize_wav(make_sound_gen_params(), _DIST, _SAMPLE_RATE)
        pcm = _pcm_from_wav(wav_bytes)
        assert np.abs(pcm).max() > 1000  # 非静音
        assert np.abs(pcm).max() <= 32767  # 不溢出


class Test声学属性注入:
    def test_元数据键齐全且值透传(self, make_sound_gen_params):
        params = make_sound_gen_params(
            loudness_gain_db=-3.0,
            event_times_ms=[100.0, 900.0],
            cer_injected=0.2,
            emotion_vector=[0.8, -0.2],
        )
        _, meta = synthesize_wav(params, _DIST, _SAMPLE_RATE)
        for key in (
            "loudness_gain_db",
            "event_times_ms",
            "cer_injected",
            "emotion_vector",
            "duration_s",
            "sample_rate",
            "channels",
        ):
            assert key in meta, f"元数据缺少 {key}"
        assert meta["loudness_gain_db"] == -3.0
        assert meta["event_times_ms"] == [100.0, 900.0]
        assert meta["cer_injected"] == 0.2
        assert meta["emotion_vector"] == [0.8, -0.2]
        assert meta["sample_rate"] == _SAMPLE_RATE
        assert meta["channels"] == 1

    def test_响度增益可测(self, make_sound_gen_params):
        """+6dB 增益 → RMS 比值 ≈ 10^(6/20)（rule.loudness_compliance 夹具可标定）。"""
        wav0, _ = synthesize_wav(
            make_sound_gen_params(seed=7, loudness_gain_db=0.0), _DIST, _SAMPLE_RATE
        )
        wav6, _ = synthesize_wav(
            make_sound_gen_params(seed=7, loudness_gain_db=6.0), _DIST, _SAMPLE_RATE
        )
        rms0 = np.sqrt(np.mean(_pcm_from_wav(wav0).astype(np.float64) ** 2))
        rms6 = np.sqrt(np.mean(_pcm_from_wav(wav6).astype(np.float64) ** 2))
        assert rms6 / rms0 == pytest.approx(10 ** (6.0 / 20.0), rel=1e-2)

    def test_元数据随工件落盘(self, make_sound_gen_params, sound_data_dir):
        """两段式落盘的工件侧：wav 字节与元数据可成对写入数据目录。"""
        import json

        wav_bytes, meta = synthesize_wav(make_sound_gen_params(), _DIST, _SAMPLE_RATE)
        wav_path = sound_data_dir / "artifacts" / "clip.wav"
        meta_path = sound_data_dir / "artifacts" / "clip.meta.json"
        wav_path.write_bytes(wav_bytes)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        with wave.open(str(wav_path), "rb") as wf:
            assert wf.getframerate() == _SAMPLE_RATE
        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        assert loaded == meta
