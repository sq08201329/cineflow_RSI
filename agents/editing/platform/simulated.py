"""确定性模拟渲染器（功能 007 / C11，开发/CI 默认实现）。

素材帧/音轨由镜头 artifact_hash / 音轨引用哈希确定性派生（程序化渐变帧 +
正弦音轨，同 004/006 程序化口径）→ T710 render_film 定点拼接/叠化/混音 →
mp4（编码单线程确定性档）。同 EDL 两次 render 逐字节一致（SC-002）；
estimate 按 EDL 名义时长（不减叠化重叠），actual 按实际成片时长——
estimated ≥ actual 恒成立。fail_on_shots 注入渲染失败（执行器失败路径测试用）。
"""

import blake3
import numpy as np

from agents.editing.edl import EditDecisionList
from agents.editing.platform.base import RenderedFilm, RenderError
from agents.editing.render import render_film
from agents.editing.shots import ShotEntry, ShotLibrary

_SALT = "simulated-edit-render-v1"


def _procedural_frames(shot: ShotEntry, *, fps: int, width: int, height: int) -> np.ndarray:
    """镜头素材帧：artifact_hash 为种子的确定性渐变帧（同库同镜头逐字节一致）。"""
    count = shot.duration_ms * fps // 1000
    seed = int(blake3.blake3((_SALT + shot.artifact_hash).encode()).hexdigest()[:16], 16)
    brightness = 40 + seed % 120
    speed = 1 + (seed >> 8) % 6
    ys, xs = np.mgrid[0:height, 0:width]
    frames = np.zeros((count, height, width, 3), dtype=np.uint8)
    for t in range(count):
        gray = np.clip(brightness + (xs * 0.25 + t * speed) % 110, 0, 255)
        block_x = (t * speed) % max(1, width - 20)
        block = (xs >= block_x) & (xs < block_x + 20)
        gray = np.where(block & (ys >= height // 3) & (ys < height * 2 // 3), 220, gray)
        frames[t] = np.stack(
            [gray, np.clip(gray + 12, 0, 255), np.clip(gray - 8, 0, 255)], axis=-1
        ).astype(np.uint8)
    return frames


def _procedural_audio(track_ref: str, *, total_ms: int, sample_rate: int) -> np.ndarray:
    """音轨采样：track_ref 哈希派生频率的正弦波（确定性定点）。"""
    n = total_ms * sample_rate // 1000
    seed = int(blake3.blake3((_SALT + track_ref).encode()).hexdigest()[:8], 16)
    freq = 180.0 + seed % 300
    t = np.arange(n, dtype=np.float64) / sample_rate
    return (0.3 * np.sin(2.0 * np.pi * freq * t) * 32767.0).astype(np.int16)


class SimulatedEditRenderer:
    """确定性模拟渲染器：EDL + 镜头库 → RenderedFilm（字节级可复现）。"""

    def __init__(
        self,
        render_cfg: dict,
        *,
        fail_on_shots: tuple[str, ...] | list[str] = (),
        sample_rate: int = 16000,
    ) -> None:
        self._cfg = dict(render_cfg)
        self._fail_on = set(fail_on_shots)
        self._sample_rate = sample_rate
        self.render_calls = 0  # 渲染调用计数（幂等/拒绝路径断言用）
        self._spent = 0.0

    @property
    def total_spent(self) -> float:
        return self._spent

    def estimate(self, edl: EditDecisionList, shots: ShotLibrary) -> float:
        """预估 = EDL 名义时长（不减叠化重叠）× 价目——恒 ≥ 实际扣费。"""
        nominal_ms = sum(c.out_ms - c.in_ms for c in edl.clips)
        return nominal_ms / 1000.0 * float(self._cfg["price_per_second_usd"])

    def render(self, edl: EditDecisionList, shots: ShotLibrary) -> RenderedFilm:
        self.render_calls += 1
        referenced = {c.shot_id for c in edl.clips}
        failed = sorted(referenced & self._fail_on)
        if failed:
            raise RenderError(f"模拟渲染失败（注入）：镜头 {failed} 渲染超时")
        fps = int(self._cfg["fps"])
        frames = {
            shot_id: _procedural_frames(
                shots.get(shot_id),
                fps=fps,
                width=int(self._cfg["width"]),
                height=int(self._cfg["height"]),
            )
            for shot_id in referenced
        }
        nominal_ms = sum(c.out_ms - c.in_ms for c in edl.clips)
        audio = {
            cue.track_ref: _procedural_audio(
                cue.track_ref, total_ms=nominal_ms, sample_rate=self._sample_rate
            )
            for cue in edl.audio
        }
        mp4_bytes, metadata = render_film(
            edl, frames, audio, fps=fps, sample_rate=self._sample_rate
        )
        actual = metadata["duration_ms"] / 1000.0 * float(self._cfg["price_per_second_usd"])
        self._spent += actual
        return RenderedFilm(mp4_bytes=mp4_bytes, metadata=metadata, actual_cost_usd=actual)
