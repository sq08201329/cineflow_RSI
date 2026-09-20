"""真实声音生成适配器骨架（契约同构，C11，T614）。

凭证经环境变量注入（SOUND_TTS_* / SOUND_SFX_* / SOUND_MUSIC_*）；
无凭证构造即 UnavailableError——不假装生成（宪章原则六）。
契约语义（预估/实际花费、错误映射）与模拟器完全一致，受
tests/contract/test_sound_platform_contract.py 同一套件约束。
"""

import os

from agents.sound.platform.base import GeneratedAudio, UnavailableError


class _HttpRealSoundGenBase:
    """三类型真实适配器共用骨架：ENV_PREFIX/gen_type 由子类声明。"""

    ENV_PREFIX: str = ""
    gen_type: str = ""

    def __init__(self, base_url: str, api_key: str) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                f"真实声音平台缺凭证：需要 {self.ENV_PREFIX}_BASE_URL / "
                f"{self.ENV_PREFIX}_API_KEY"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get(f"{cls.ENV_PREFIX}_BASE_URL", ""),
            os.environ.get(f"{cls.ENV_PREFIX}_API_KEY", ""),
        )

    def estimate(self, params: dict) -> float:
        raise UnavailableError("真实声音平台接入是凭证配置的运维动作，本期未接入")

    def generate(self, params: dict) -> GeneratedAudio:
        raise UnavailableError("真实声音平台未接入")


class HttpRealTTSGen(_HttpRealSoundGenBase):
    """真实 TTS 适配器骨架。"""

    ENV_PREFIX = "SOUND_TTS"
    gen_type = "tts"


class HttpRealSFXGen(_HttpRealSoundGenBase):
    """真实音效适配器骨架。"""

    ENV_PREFIX = "SOUND_SFX"
    gen_type = "sfx"


class HttpRealMusicGen(_HttpRealSoundGenBase):
    """真实配乐适配器骨架。"""

    ENV_PREFIX = "SOUND_MUSIC"
    gen_type = "music"
