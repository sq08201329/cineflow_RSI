"""确定性模拟渲染器（功能 008 / C11，开发/CI 默认实现）。

ShotList + 剧本 → T810 `render_animatic`（分镜卡帧：程序化构图 + 情绪色板注入 →
拼接 → mp4 单线程编码确定性档）→ RenderedAnimatic。同 ShotList 两次 render 逐字节
一致（SC-002）；estimate = 镜头数 × `render.price_per_shot_usd`，actual 按实际渲染
的镜头数计价——恒 estimated ≥ actual。

`fail_on_shots` 注入渲染失败（执行器失败路径测试用，Normalize 为 RenderError 族）；
`with_temp_audio` 产临时音轨（定点混音哈希进元数据；mp4 只承载画面轨，与 007 同口径）。
"""

from agents.storyboard.board_render import render_animatic
from agents.storyboard.config import StoryboardConfig
from agents.storyboard.platform.base import RenderedAnimatic, RenderError
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList


class SimulatedStoryboardRenderer:
    """确定性模拟渲染器：ShotList + 剧本 → RenderedAnimatic（字节级可复现）。"""

    def __init__(
        self,
        *,
        with_temp_audio: bool = False,
        fail_on_shots: tuple[str, ...] | list[str] | set[str] = (),
        sample_rate: int = 16000,
    ) -> None:
        self._with_temp_audio = with_temp_audio
        self._fail_on = set(fail_on_shots)
        self._sample_rate = sample_rate
        self.render_calls = 0  # 渲染调用计数（幂等/拒绝路径断言用）
        self._spent = 0.0

    @property
    def total_spent(self) -> float:
        return self._spent

    def estimate(self, shotlist: ShotList, cfg: StoryboardConfig) -> float:
        """预估 = 镜头数 × 价目（名义口径；实际按渲染镜头数，恒 ≤ 预估）。"""
        return len(shotlist.shots) * float(cfg.render["price_per_shot_usd"])

    def render(
        self, shotlist: ShotList, script: ScriptSegment, cfg: StoryboardConfig
    ) -> RenderedAnimatic:
        self.render_calls += 1
        failed = sorted({shot.shot_id for shot in shotlist.shots} & self._fail_on)
        if failed:
            raise RenderError(f"模拟渲染失败（注入）：镜头 {failed} 渲染超时")
        mp4_bytes, metadata = render_animatic(
            shotlist,
            script,
            render_cfg=cfg.render,
            grammar_rules=cfg.shot_grammar,
            emotion_vectors=cfg.emotion_vectors,
            with_temp_audio=self._with_temp_audio,
            sample_rate=self._sample_rate,
        )
        actual = metadata["shot_count"] * float(cfg.render["price_per_shot_usd"])
        self._spent += actual
        return RenderedAnimatic(
            mp4_bytes=mp4_bytes,
            metadata=metadata,
            actual_cost_usd=actual,
        )
