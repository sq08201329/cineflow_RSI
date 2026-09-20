"""剪辑渲染适配器基座：协议、错误分型、RenderedFilm（contracts/editing-platform.md C10）。

错误统一映射 RenderError 族，不泄漏实现侧异常类型（004/006 同款惯例）；
花费：estimate 预估、render 返回实际扣费，estimated ≥ actual。
"""

from dataclasses import dataclass, field
from typing import Protocol

from agents.editing.edl import EditDecisionList
from agents.editing.shots import ShotLibrary


class RenderError(Exception):
    """剪辑渲染平台错误基类。"""


class RateLimitedError(RenderError):
    """渲染服务限流（可重试）。"""


class UnavailableError(RenderError):
    """渲染服务不可用/未配置凭证（可重试）。"""


@dataclass(frozen=True)
class RenderedFilm:
    """一次渲染的产出：mp4 字节 + 元数据 + 实际扣费。

    metadata 必含四键（C10，评估器输入）：duration_ms 总时长、
    shot_durations_ms 镜头时长序列、transitions 转场序列、has_audio 音轨标记。
    """

    mp4_bytes: bytes
    metadata: dict = field(default_factory=dict)
    actual_cost_usd: float = 0.0


class EditRenderAdapter(Protocol):
    """剪辑渲染平台适配器协议（模拟/真实双实现同构，C10）。"""

    def estimate(self, edl: EditDecisionList, shots: ShotLibrary) -> float:
        """预估成本（USD，按成片名义时长 × 价目；预算门禁申请前校验依据）。"""
        ...

    def render(self, edl: EditDecisionList, shots: ShotLibrary) -> RenderedFilm:
        """渲染合成 mp4 与元数据（昂贵动作仅经此调用，原则三）。"""
        ...
