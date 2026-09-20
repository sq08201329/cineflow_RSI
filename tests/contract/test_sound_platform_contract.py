"""声音生成适配器契约套件（功能 006 / T611，先于实现编写）。

C9~C12 同构执行：三类型（tts/sfx/music）× 双实现（模拟/真实骨架）——
estimate ≥ actual 语义、产出 wav 可解析（采样率/声道/位深符合 SoundConfig）、
元数据键齐全（声学属性注入标记按类型适用）、错误分型正确。
模拟实现必须全过；真实实现无凭证按用例 skip（不报错不假装，003/004 同款）。
"""

import io
import os
import wave

import pytest

from agents.sound.platform.base import (
    InvalidParamsError,
    RateLimitedError,
    SoundGenError,
    UnavailableError,
)
from agents.sound.platform.simulated import (
    SimulatedMusicGen,
    SimulatedSFXGen,
    SimulatedTTSGen,
)

_SIMULATED = {"tts": SimulatedTTSGen, "sfx": SimulatedSFXGen, "music": SimulatedMusicGen}
# 类型适用的注入标记（评估器的确定性输入，C9）：TTS→错字率、SFX→事件时间、music→情绪向量
_TYPE_MARKERS = {"tts": "cer_injected", "sfx": "event_times_ms", "music": "emotion_vector"}


def _make_real_adapter(gen_type: str):
    from agents.sound.platform.http_real import (
        HttpRealMusicGen,
        HttpRealSFXGen,
        HttpRealTTSGen,
    )

    cls = {"tts": HttpRealTTSGen, "sfx": HttpRealSFXGen, "music": HttpRealMusicGen}[gen_type]
    prefix = f"SOUND_{gen_type.upper()}"
    if not os.environ.get(f"{prefix}_BASE_URL"):
        pytest.skip(f"真实声音平台无凭证（{prefix}_BASE_URL 未配置），跳过其契约用例")
    return cls.from_env()


@pytest.fixture()
def adapter(request, sound_config):
    impl, gen_type = request.param
    if impl == "simulated":
        return _SIMULATED[gen_type](sound_config.simulated_gen, sound_config.sample_rate)
    return _make_real_adapter(gen_type)


def _params(make_sound_gen_params, gen_type: str) -> dict:
    return make_sound_gen_params(gen_type=gen_type, seed=42)


@pytest.mark.parametrize(
    "adapter",
    [(impl, gen_type) for impl in ("simulated", "http_real") for gen_type in _SIMULATED],
    indirect=True,
    ids=[f"{impl}-{gt}" for impl in ("simulated", "http_real") for gt in _SIMULATED],
)
class Test适配器契约:
    """同一套用例对三类型 × 双实现同构执行（C12）。"""

    def test_协议携带_gen_type(self, adapter, request):
        _, gen_type = request.node.callspec.params["adapter"]
        assert adapter.gen_type == gen_type

    def test_预估花费为正(self, adapter, make_sound_gen_params):
        assert adapter.estimate(_params(make_sound_gen_params, adapter.gen_type)) > 0

    def test_实际扣费不超预估(self, adapter, make_sound_gen_params):
        """estimate ≥ actual 纪律（C12 场景 4，004 同款）。"""
        params = _params(make_sound_gen_params, adapter.gen_type)
        estimated = adapter.estimate(params)
        produced = adapter.generate(params)
        assert produced.actual_cost_usd <= estimated

    def test_工件_wav_可解析且规格符合配置(self, adapter, make_sound_gen_params, sound_config):
        produced = adapter.generate(_params(make_sound_gen_params, adapter.gen_type))
        with wave.open(io.BytesIO(produced.wav_bytes), "rb") as wf:
            assert wf.getframerate() == sound_config.sample_rate
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # PCM16

    def test_元数据键齐全且类型适用(self, adapter, make_sound_gen_params):
        """注入标记按类型适用：TTS→cer_injected、SFX→event_times、music→情绪向量。"""
        produced = adapter.generate(_params(make_sound_gen_params, adapter.gen_type))
        metadata = produced.metadata
        assert "loudness_gain_db" in metadata  # 三类型共用（响度分档合规输入）
        marker = _TYPE_MARKERS[adapter.gen_type]
        assert marker in metadata, f"{adapter.gen_type} 元数据缺少 {marker}"

    def test_同参数两次生成逐字节一致(self, adapter, make_sound_gen_params):
        """SC-002 确定性：同参数两次 generate 字节完全一致。"""
        params = _params(make_sound_gen_params, adapter.gen_type)
        first = adapter.generate(params)
        second = adapter.generate(params)
        assert first.wav_bytes == second.wav_bytes
        assert first.metadata == second.metadata


class Test错误分型:
    """错误类型层级（C9）：全部归一 SoundGenError 族，不泄漏实现侧异常。"""

    def test_错误层级(self):
        assert issubclass(RateLimitedError, SoundGenError)
        assert issubclass(UnavailableError, SoundGenError)
        assert issubclass(InvalidParamsError, SoundGenError)

    @pytest.mark.parametrize("gen_type", ["tts", "sfx", "music"])
    def test_真实适配器无凭证构造即报未配置(self, gen_type, monkeypatch):
        """C11/C12 场景 3：无凭证 → UnavailableError（不假装接入）。"""
        from agents.sound.platform.http_real import (
            HttpRealMusicGen,
            HttpRealSFXGen,
            HttpRealTTSGen,
        )

        cls = {"tts": HttpRealTTSGen, "sfx": HttpRealSFXGen, "music": HttpRealMusicGen}[gen_type]
        prefix = f"SOUND_{gen_type.upper()}"
        monkeypatch.delenv(f"{prefix}_BASE_URL", raising=False)
        monkeypatch.delenv(f"{prefix}_API_KEY", raising=False)
        with pytest.raises(UnavailableError, match=prefix):
            cls.from_env()
