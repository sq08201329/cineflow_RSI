"""声音生成适配器基座：协议、错误类型、GeneratedAudio（contracts/sound-platform.md C9）。

澄清 Q2 落点：适配器按 TTS/音效/音乐三类型拆分（与真实 API 拓扑一致）；
花费：estimate 预估、generate 返回实际扣费，estimated ≥ actual（004 纪律）；
错误统一映射 SoundGenError 族，不泄漏实现侧异常类型。
"""

from dataclasses import dataclass, field
from typing import Protocol


class SoundGenError(Exception):
    """声音生成平台错误基类。"""


class RateLimitedError(SoundGenError):
    """平台限流（可重试）。"""


class UnavailableError(SoundGenError):
    """平台不可用/未配置凭证（可重试）。"""


class InvalidParamsError(SoundGenError):
    """参数非法（不重试）。"""


@dataclass(frozen=True)
class GeneratedAudio:
    """一次生成的产出：wav 字节 + 声学属性元数据 + 实际扣费。

    metadata 必含声学属性注入标记（loudness_gain_db/event_times_ms/cer_injected/
    emotion_vector，按类型适用）——四评估器的确定性输入（C9）。
    """

    wav_bytes: bytes
    metadata: dict = field(default_factory=dict)
    actual_cost_usd: float = 0.0


class SoundGenAdapter(Protocol):
    """声音生成平台适配器协议（三类型各自实现）。"""

    gen_type: str  # "tts" | "sfx" | "music"（成本分账粒度）

    def estimate(self, params: dict) -> float:
        """预估成本（USD，预算门禁申请前校验依据）。"""
        ...

    def generate(self, params: dict) -> GeneratedAudio:
        """生成 wav 工件与元数据（昂贵动作仅经此调用，原则三）。"""
        ...
