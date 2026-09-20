"""确定性模拟视频生成器（research 决策 2，开发/CI 默认实现）。

blake3(规范化 gen_params) 为种子 → numpy 程序化帧（渐变 + 运动色块 +
种子派生亮度/色彩/运动幅度/噪声）→ imageio-ffmpeg 编码 mp4。
同参数产出逐字节相同（工件哈希稳定，内容寻址与重算一致性可断言）；
内部账本记录扣费供对账；花费实际 ≤ 预估。
"""

import io
import json

import blake3
import imageio.v3 as iio
import numpy as np

from agents.visual.platform.base import (
    ArtifactNotReadyError,
    GenJob,
    GenJobStatus,
    InvalidParamsError,
)
from core.tree.models import new_id

_SALT = "simulated-video-gen-v1"


def render_frames(gen_params: dict, distribution: dict) -> np.ndarray:
    """程序化确定性帧渲染：同 gen_params 逐字节相同。"""
    canonical = json.dumps(gen_params, sort_keys=True, ensure_ascii=False)
    seed = int(blake3.blake3((_SALT + canonical).encode()).hexdigest()[:16], 16)
    rng = np.random.default_rng(seed)

    count = int(distribution["frames"])
    # 分辨率可由 gen_params 覆盖（合规门禁对照 clip_spec 的违规样例由此产生）
    width = int(gen_params.get("width", 320))
    height = int(gen_params.get("height", 240))
    tier = int(gen_params.get("seed_tier", 1))
    shots = int(gen_params.get("shots", 1))
    brightness = 40 + (seed % 120)
    speed = 1 + (seed >> 8) % 6
    noise_level = tier * 2  # 种子档控制噪声幅度（闪烁维度区分度）
    hue_shift = (seed >> 16) % 60  # 色彩区分度（美学维度）

    frames = np.zeros((count, height, width, 3), dtype=np.uint8)
    ys, xs = np.mgrid[0:height, 0:width]
    for t in range(count):
        shot = min(shots - 1, t * shots // count)  # 镜头切换点
        base = brightness + shot * 40 + (xs * 0.15 + t * 2) % 100
        block_x = (t * speed + shot * 60) % (width - 40)
        block = (xs >= block_x) & (xs < block_x + 40) & (ys >= 90) & (ys < 150)
        frame = np.where(block, base + 80, base)
        if noise_level:
            frame = frame + rng.integers(-noise_level, noise_level + 1, frame.shape)
        gray = np.clip(frame, 0, 255)
        frames[t] = np.stack(
            [
                np.clip(gray + hue_shift, 0, 255),
                gray,
                np.clip(gray - hue_shift // 2, 0, 255),
            ],
            axis=-1,
        ).astype(np.uint8)
    return frames


def encode_mp4(frames: np.ndarray, fps: int) -> bytes:
    """imageio-ffmpeg 编码 mp4（参数固定：逐字节可复现）。

    编码固定单线程档（threads=1）：x264 多线程编码在负载下的非确定性是
    一期 test_visual_consistency 偶发 flake 的根因（010/006 运维记录）——
    007 决策 2 在剪辑渲染器先行消除，此处为安全回移：工件/锚点哈希均为
    运行时重算（无持久化钉死值），字节变化不影响任何冻结断言。
    """
    buffer = io.BytesIO()
    iio.imwrite(
        buffer,
        frames,
        fps=fps,
        codec="libx264",
        extension=".mp4",
        ffmpeg_params=["-threads", "1"],
    )
    return buffer.getvalue()


class SimulatedVideoGen:
    """确定性模拟生成平台：幂等键去重、状态机逐次推进、账本对账。"""

    def __init__(self, distribution: dict) -> None:
        self._dist = distribution
        self._jobs: dict[str, dict] = {}  # external_id → 内部任务态
        self._by_idempotency: dict[str, str] = {}
        self._ledger: list[dict] = []

    @property
    def job_count(self) -> int:
        return len(self._jobs)

    @property
    def total_spent(self) -> float:
        return sum(entry["cost_usd"] for entry in self._ledger)

    def job_actual_cost(self, external_id: str) -> float:
        return self._require(external_id)["actual_cost_usd"]

    def _require(self, external_id: str) -> dict:
        if external_id not in self._jobs:
            raise InvalidParamsError(f"未知任务：{external_id}")
        return self._jobs[external_id]

    def submit(self, gen_params: dict, *, idempotency_key: str) -> GenJob:
        if idempotency_key in self._by_idempotency:  # 幂等：重复提交返回同一任务
            state = self._jobs[self._by_idempotency[idempotency_key]]
            return state["job"]
        canonical = json.dumps(gen_params, sort_keys=True, ensure_ascii=False)
        params_hash = blake3.blake3(canonical.encode()).hexdigest()
        external_id = f"vg-{blake3.blake3(idempotency_key.encode()).hexdigest()[:16]}"
        job = GenJob(
            job_id=new_id(),
            external_id=external_id,
            params_hash=params_hash,
            estimated_cost_usd=float(self._dist["estimated_cost_usd"]),
            status=GenJobStatus.SUBMITTED,
        )
        # 提交即渲染缓存（确定性工件）；状态机由 get_status 推进
        self._jobs[external_id] = {
            "job": job,
            "gen_params": gen_params,
            "ticks": 0,
            "actual_cost_usd": 0.0,
            "artifact": encode_mp4(render_frames(gen_params, self._dist), fps=8),
        }
        self._by_idempotency[idempotency_key] = external_id
        return job

    def get_status(self, external_id: str) -> GenJobStatus:
        state = self._require(external_id)
        status = state["job"].status
        if status in (GenJobStatus.COMPLETED, GenJobStatus.FAILED):
            return status
        # submitted → generating → completed：每次查询推进一格（确定性）
        state["ticks"] += 1
        nxt = GenJobStatus.GENERATING if state["ticks"] == 1 else GenJobStatus.COMPLETED
        state["job"] = GenJob(
            job_id=state["job"].job_id,
            external_id=external_id,
            params_hash=state["job"].params_hash,
            estimated_cost_usd=state["job"].estimated_cost_usd,
            status=nxt,
        )
        return nxt

    def fetch_artifact(self, external_id: str) -> bytes:
        state = self._require(external_id)
        if state["job"].status is not GenJobStatus.COMPLETED:
            raise ArtifactNotReadyError(f"任务 {external_id} 未 completed，工件未就绪")
        if not state["actual_cost_usd"]:  # 首次取件时实际扣费入账（实际 ≤ 预估）
            actual = min(float(self._dist["cost_per_clip_usd"]), state["job"].estimated_cost_usd)
            state["actual_cost_usd"] = actual
            self._ledger.append({"external_id": external_id, "cost_usd": actual})
        return state["artifact"]

    def cancel(self, external_id: str) -> None:
        state = self._require(external_id)
        if state["job"].status in (GenJobStatus.COMPLETED, GenJobStatus.FAILED):
            return  # 幂等：已完成/已失败为无操作
        state["job"] = GenJob(
            job_id=state["job"].job_id,
            external_id=external_id,
            params_hash=state["job"].params_hash,
            estimated_cost_usd=state["job"].estimated_cost_usd,
            status=GenJobStatus.FAILED,
        )
