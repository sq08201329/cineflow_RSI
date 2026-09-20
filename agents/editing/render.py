"""确定性渲染合成（功能 007，research 决策 2）：EDL + 素材帧 → mp4。

numpy 程序化拼接、叠化（定点 alpha 混合，α 为 1/256 精度整数斜坡）、
混音（定点增益叠加，gain 量化到 1/1024）→ imageio-ffmpeg 编码 mp4。
**编码固定单线程确定性档**（ENCODE_FFMPEG_PARAMS = threads=1）——004
x264 多线程编码在负载下非确定的 flake 根因从设计上消除；同 EDL 两次
渲染逐字节一致（SC-002）。

编码路径复用 004 `agents/visual/platform/simulated.py` 的 encode_mp4
（imageio.v3 + libx264 + BytesIO），仅追加单线程参数。
"""

import io

import blake3
import imageio.v3 as iio
import numpy as np

from agents.editing.edl import EditDecisionList
from core.tree.errors import ValidationError

# 编码单线程确定性档（决策 2）：x264 多线程是 004 test_visual_consistency flake 根因
ENCODE_FFMPEG_PARAMS = ["-threads", "1"]

# 定点精度：alpha 混合 1/256（8bit 帧口径）、混音增益 1/1024
_ALPHA_SCALE = 256
_GAIN_SCALE = 1024


def encode_mp4_deterministic(frames: np.ndarray, fps: int) -> bytes:
    """004 encode_mp4 同路径 + 单线程档：逐字节可复现。"""
    buffer = io.BytesIO()
    iio.imwrite(
        buffer,
        frames,
        fps=fps,
        codec="libx264",
        extension=".mp4",
        ffmpeg_params=list(ENCODE_FFMPEG_PARAMS),
    )
    return buffer.getvalue()


def _clip_segment(clip, frames_by_shot: dict[str, np.ndarray], fps: int) -> np.ndarray:
    """取 clip 对应的帧区间 [in, out)：毫秒 → 帧号用整数下取整（帧网格口径）。"""
    if clip.shot_id not in frames_by_shot:
        raise ValidationError(f"素材帧缺失：镜头 {clip.shot_id!r} 无帧序列")
    source = frames_by_shot[clip.shot_id]
    start = clip.in_ms * fps // 1000
    count = (clip.out_ms - clip.in_ms) * fps // 1000
    if count <= 0:
        raise ValidationError(
            f"clip {clip.shot_id!r} 帧区间为空：[{clip.in_ms}, {clip.out_ms})ms @ {fps}fps"
        )
    if start + count > len(source):
        raise ValidationError(
            f"素材帧不足：镜头 {clip.shot_id!r} 需 [{start}, {start + count}) 帧，"
            f"实际仅 {len(source)} 帧"
        )
    return source[start : start + count]


def _dissolve(prev: np.ndarray, nxt: np.ndarray, overlap: int) -> np.ndarray:
    """定点 alpha 混合：α 从 0 斜坡到满幅（第 k 帧 α=(k+1)·256/(overlap+1)）。"""
    overlap = min(overlap, len(prev), len(nxt))
    if overlap <= 0:
        return np.concatenate([prev, nxt], axis=0)
    w = ((np.arange(1, overlap + 1, dtype=np.int32) * _ALPHA_SCALE) // (overlap + 1)).reshape(
        overlap, 1, 1, 1
    )
    head = prev[:-overlap] if overlap < len(prev) else prev[:0]
    tail = nxt[overlap:] if overlap < len(nxt) else nxt[:0]
    blend = (
        prev[-overlap:].astype(np.uint16) * (_ALPHA_SCALE - w) + nxt[:overlap].astype(np.uint16) * w
    ) // _ALPHA_SCALE
    return np.concatenate([head, blend.astype(np.uint8), tail], axis=0)


def _fade_out(segment: np.ndarray, fade: int) -> np.ndarray:
    """淡出黑场：末 fade 帧按整数斜坡衰减到 0（不压缩时长）。"""
    fade = min(fade, len(segment))
    if fade <= 0:
        return segment
    factors = ((np.arange(fade, 0, -1, dtype=np.int32) * _ALPHA_SCALE) // (fade + 1)).reshape(
        fade, 1, 1, 1
    )
    out = segment.copy()
    out[-fade:] = (segment[-fade:].astype(np.uint16) * factors // _ALPHA_SCALE).astype(np.uint8)
    return out


def compose_frames(
    edl: EditDecisionList, frames_by_shot: dict[str, np.ndarray], fps: int
) -> np.ndarray:
    """EDL → 拼接后帧序列：cut 直接拼接、dissolve 定点混合重叠、fade 淡出黑场。

    clip 上的 transition 描述该 clip 到下一 clip 的衔接（与 edl.py 校验口径一致）；
    末 clip 的 transition 不渲染。
    """
    if not isinstance(edl, EditDecisionList):
        raise ValidationError(f"edl 必须为 EditDecisionList，实际为 {edl!r}")
    segments = [_clip_segment(clip, frames_by_shot, fps) for clip in edl.clips]
    composed = segments[0]
    for i, clip in enumerate(edl.clips[:-1]):
        nxt = segments[i + 1]
        overlap = clip.transition.duration_ms * fps // 1000
        if clip.transition.type == "dissolve":
            composed = _dissolve(composed, nxt, overlap)
        elif clip.transition.type == "fade":
            composed = np.concatenate([_fade_out(composed, overlap), nxt], axis=0)
        else:  # cut（及规则库未来扩展类型的默认直拼）
            composed = np.concatenate([composed, nxt], axis=0)
    return composed


def mix_audio(
    edl: EditDecisionList,
    audio_by_track: dict[str, np.ndarray],
    *,
    total_ms: int,
    sample_rate: int,
) -> np.ndarray:
    """定点混音（决策 9）：音轨按 at_ms 时间戳摆放，gain 量化 1/1024 叠加，int32 累积
    后裁剪回 PCM16；同输入重算逐位一致。"""
    total_samples = total_ms * sample_rate // 1000
    acc = np.zeros(total_samples, dtype=np.int32)
    for cue in edl.audio:
        if cue.track_ref not in audio_by_track:
            raise ValidationError(f"音轨素材缺失：{cue.track_ref!r} 无采样序列")
        track = np.asarray(audio_by_track[cue.track_ref], dtype=np.int32)
        gain_q = round(cue.gain * _GAIN_SCALE)  # 定点增益
        start = cue.at_ms * sample_rate // 1000
        if start >= total_samples:
            continue  # 摆放点超出成片时长：截断（如实无声）
        end = min(total_samples, start + len(track))
        acc[start:end] += (track[: end - start] * gain_q) // _GAIN_SCALE
    return np.clip(acc, -32768, 32767).astype(np.int16)


def render_film(
    edl: EditDecisionList,
    frames_by_shot: dict[str, np.ndarray],
    audio_by_track: dict[str, np.ndarray] | None = None,
    *,
    fps: int,
    sample_rate: int = 16000,
) -> tuple[bytes, dict]:
    """EDL + 素材帧 → (mp4 字节, 元数据)。

    元数据（C10：评估器输入）：duration_ms 总时长（帧数口径）、
    shot_durations_ms 镜头时长序列（与 EDL 一致）、transitions 转场序列、
    has_audio 音轨标记；带音轨时附 audio_mix_hash（定点混音的内容寻址摘要）。
    """
    frames = compose_frames(edl, frames_by_shot, fps)
    duration_ms = len(frames) * 1000 // fps
    metadata = {
        "duration_ms": duration_ms,
        "shot_durations_ms": [c.out_ms - c.in_ms for c in edl.clips],
        "transitions": [c.transition.to_dict() for c in edl.clips],
        "has_audio": bool(edl.audio),
        "fps": fps,
    }
    if edl.audio:
        mix = mix_audio(edl, audio_by_track or {}, total_ms=duration_ms, sample_rate=sample_rate)
        metadata["audio_mix_hash"] = blake3.blake3(mix.tobytes()).hexdigest()
    return encode_mp4_deterministic(frames, fps), metadata
