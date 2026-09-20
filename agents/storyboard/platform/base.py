"""分镜预演渲染适配器基座：协议、错误分型、RenderedAnimatic。

契约见 contracts/storyboard-platform.md C10。

错误统一映射 RenderError 族，不泄漏实现侧异常类型（004/006/007 同款惯例）；
花费：estimate 预估、render 返回实际扣费，estimated ≥ actual。
配置随调用注入（C10 的 cfg 参数 = StoryboardConfig：渲染参数/规则库/情绪向量表
由配置单一事实源提供，适配器自身无形态硬编码）。
"""

from dataclasses import dataclass, field
from typing import Protocol

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList


class RenderError(Exception):
    """预演渲染平台错误基类。"""


class RateLimitedError(RenderError):
    """渲染服务限流（可重试）。"""


class UnavailableError(RenderError):
    """渲染服务不可用/未配置凭证（可重试）。"""


@dataclass(frozen=True)
class RenderedAnimatic:
    """一次渲染的产出：animatic mp4 字节 + 元数据 + 实际扣费。

    metadata 必含（C10/C11，评估器输入）：duration_ms 总时长、shot_count 镜头数、
    shot_sizes 景别序列、shot_durations_ms 估算时长序列、shot_frame_counts 帧数序列、
    frame_hashes 逐镜分镜卡帧哈希（C7 校验来源一致）、frames_hash 序列哈希、
    emotions 逐镜情绪基调、has_temp_audio 临时音轨标记（带音轨时附 temp_audio_mix_hash）。
    """

    mp4_bytes: bytes
    metadata: dict = field(default_factory=dict)
    actual_cost_usd: float = 0.0


class StoryboardRenderAdapter(Protocol):
    """预演渲染平台适配器协议（模拟/真实双实现同构，C10）。"""

    def estimate(self, shotlist: ShotList, cfg: StoryboardConfig) -> float:
        """预估成本（USD，按镜头数 × 价目；预算门禁申请前校验依据）。"""
        ...

    def render(
        self, shotlist: ShotList, script: ScriptSegment, cfg: StoryboardConfig
    ) -> RenderedAnimatic:
        """渲染 animatic mp4 与元数据（昂贵动作仅经此调用，原则三）。

        script 为情绪基调来源（逐镜情绪取自承接行的情绪标注，澄清 Q2）；
        帧产出走 board_render.storyboard_cards 单一函数（渲染件与评估输入同源）。
        """
        ...
