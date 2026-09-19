"""帧采样管线（research 决策 1/3）：ffprobe 探测 + 等间隔 N=8 采样 64×64。

解码后端为 imageio-ffmpeg（wheel 自带 ffmpeg 二进制，版本锁定可复现）；
采样规则（帧数/尺寸）序列化进评估器版本元信息——采样变则版本变（原则一）。
损坏文件/不可解码一律受控 FrameDecodeError，不崩溃闭环。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import blake3
import imageio_ffmpeg
import numpy as np


class FrameDecodeError(Exception):
    """片段无法探测/解码（损坏文件、ffmpeg 不可用等）：受控报错。"""


@dataclass(frozen=True)
class FrameSamples:
    """帧采样序列（评估器输入边界；同一片段同一规则逐字节一致）。"""

    frames_gray: np.ndarray  # (N, 64, 64) uint8
    frames_rgb: np.ndarray  # (N, 64, 64, 3) uint8
    frame_indices: list[int]
    sampling_spec: dict


def probe_clip(path: str | Path) -> dict:
    """ffprobe 元数据探测：编码/分辨率/帧率/时长。"""
    try:
        meta = next(imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24"))
    except Exception as exc:  # noqa: BLE001 - 解码/探测失败统一受控
        raise FrameDecodeError(f"片段无法探测：{path}（{exc}）") from exc
    return {
        "width": int(meta["size"][0]),
        "height": int(meta["size"][1]),
        "fps": float(meta["fps"]),
        "duration_seconds": float(meta["duration"]),
        "codec": str(meta["codec"]),
    }


def _resize_nearest(frame: np.ndarray, size: int) -> np.ndarray:
    """最近邻缩放（纯 numpy，确定性；不引入 cv2）。"""
    h, w = frame.shape[:2]
    yi = (np.arange(size) * h // size).astype(int)
    xi = (np.arange(size) * w // size).astype(int)
    return frame[np.ix_(yi, xi)]


def sample_frames(path: str | Path, sampling_spec: dict) -> FrameSamples:
    """等间隔取 N 帧（含首尾），缩放至 size×size，灰度 + RGB 双形态。"""
    count = int(sampling_spec["count"])
    size = int(sampling_spec["size"])
    try:
        stream = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24")
        meta = next(stream)  # 首帧之前先产出元数据
        frames = np.stack(
            [
                np.frombuffer(b, np.uint8).reshape(meta["size"][1], meta["size"][0], 3)
                for b in stream
            ]
        )
    except Exception as exc:  # noqa: BLE001
        raise FrameDecodeError(f"片段无法解码：{path}（{exc}）") from exc
    total = len(frames)
    if total == 0:
        raise FrameDecodeError(f"片段零帧：{path}")
    if total < count:
        raise FrameDecodeError(f"片段帧数 {total} 不足采样 {count} 帧")

    indices = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    picked = frames[indices]
    rgb = np.stack([_resize_nearest(f, size) for f in picked])
    gray = np.clip(rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114, 0, 255).astype(
        np.uint8
    )
    return FrameSamples(
        frames_gray=gray,
        frames_rgb=rgb,
        frame_indices=indices,
        sampling_spec=dict(sampling_spec),
    )


def sampling_spec_hash(sampling_spec: dict) -> str:
    """采样规格哈希（进评估器版本元信息）：规范化 JSON 的 BLAKE3 前 12 位。"""
    canonical = json.dumps(sampling_spec, sort_keys=True).encode()
    return blake3.blake3(canonical).hexdigest()[:12]
