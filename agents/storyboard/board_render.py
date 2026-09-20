"""分镜卡帧生成与预演合成（功能 008，research 决策 4 / C11）。

ShotList → 每镜一张分镜卡（程序化构图表达景别/机位/运动 + 情绪色板注入）→ 拼接
（可选临时音轨）→ mp4。**编码固定单线程确定性档**（同 007 ENCODE_FFMPEG_PARAMS）：
同 ShotList 两次渲染逐字节一致（SC-002）。

构图口径（全部定点整数运算，无随机流）：
- 背景 = 情绪基调色板（storyboard.emotion_vectors 归一化 RGB × 255，未标注情绪 → 中性灰）；
- 主体框边长占比 = 景别档位序号映射（景别越远占比越小）；
- 构图锚点 = 机位档位（含侧别 A|B 的左右镜像）；
- 运动标记条 = 运动档位 + 镜头序号（静止档不画）；
- 顶部索引条 = 镜头序号 4 位二进制编码（decode_index_code 可机检）。
色板只做标量倍数着色（主体/条/索引块都是基调色的定比缩放），因此整卡均值与情绪
基调向量同向——proxy.emotion_alignment 读同一函数产出的帧做余弦即成立（澄清 Q2）。

**帧产出单一来源**：storyboard_cards 是唯一的分镜卡帧产出函数，渲染件与评估输入
共用；元数据带逐帧哈希（frame_hashes）与序列哈希（frames_hash），评估器重算同一函数
即可校验来源一致（禁止两套帧）。

音轨口径（与 007 同）：临时音轨以定点正弦混音产出，其 BLAKE3 进元数据
（temp_audio_mix_hash）作为"带临时音轨"的可审计标记；mp4 只承载画面轨。
"""

import io
from dataclasses import dataclass
from pathlib import Path

import blake3
import imageio.v3 as iio
import numpy as np

from agents.storyboard.shotlist import ShotEntry, ShotList
from core.tree.errors import ValidationError

# 编码单线程确定性档（007 同参数）：x264 多线程编码在负载下非确定，逐字节复现的根因
ENCODE_FFMPEG_PARAMS = ["-threads", "1"]

# 索引条：帧顶两行 4 位二进制编码镜头序号（最多 16 镜/组）
INDEX_BITS = 4
_INDEX_ROWS = 2
# 色板只做标量倍数着色：背景 1.0、主体 0.55、运动标记 0.85、索引亮块 0.75 / 暗块 0.30
_SUBJECT_FACTOR = 0.55
_MARKER_FACTOR = 0.85
_INDEX_ON_FACTOR = 0.75
_INDEX_OFF_FACTOR = 0.30
# 索引解码阈值：以整卡均值为基准（背景占比最大 → 均值 ≈ 基调色亮度的定比）
_INDEX_DECODE_THRESHOLD = 0.55
_NEUTRAL_PALETTE = (128, 128, 128)
# 景别映射：最近景别主体占比 0.90 → 最远景别 0.20（按规则库枚举长度线性插值）
_SUBJECT_MAX = 0.90
_SUBJECT_MIN = 0.20


@dataclass(frozen=True)
class StoryboardCards:
    """分镜卡帧序列（每镜一张）+ 逐帧哈希 + 序列哈希 + 逐镜情绪基调。

    frames/frame_hashes/emotions 与 ShotList.shots 一一对应（评估器按镜取帧）。
    """

    frames: np.ndarray
    frame_hashes: tuple[str, ...]
    frames_hash: str
    emotions: tuple[str | None, ...]


def _as_entry(shot: ShotEntry | dict) -> ShotEntry:
    return shot if isinstance(shot, ShotEntry) else ShotEntry.from_dict(shot)


def _require_render_cfg(render_cfg: dict) -> dict:
    if not isinstance(render_cfg, dict):
        raise ValidationError(f"render_cfg 必须为 dict，实际为 {render_cfg!r}")
    for key in ("fps", "width", "height"):
        if key not in render_cfg:
            raise ValidationError(f"storyboard.render 缺少配置项 {key!r}")
    return render_cfg


def _shade(palette: np.ndarray, factor: float) -> np.ndarray:
    """基调色的标量倍数着色（定点取整）：保持色相 → 余弦对齐口径不受构图影响。"""
    return np.clip(palette.astype(np.int32) * int(round(factor * 100)) // 100, 0, 255).astype(
        np.uint8
    )


def emotion_palette(emotion: str | None, emotion_vectors: dict) -> np.ndarray:
    """情绪基调 → 分镜卡色板（归一化 RGB × 255）；未标注情绪 → 中性灰。

    未登记情绪即报错（不允许静默用默认色板冒充——诚实边界，原则六）。
    """
    if emotion is None:
        return np.array(_NEUTRAL_PALETTE, dtype=np.uint8)
    if not isinstance(emotion_vectors, dict) or emotion not in emotion_vectors:
        raise ValidationError(
            f"情绪基调 {emotion!r} 不在情绪向量表 {sorted(emotion_vectors or {})} 内"
        )
    vector = emotion_vectors[emotion]
    if not isinstance(vector, (list, tuple)) or len(vector) != 3:
        raise ValidationError(f"情绪向量 {emotion!r} 必须为 3 维 RGB，实际为 {vector!r}")
    return np.array([int(round(float(c) * 255)) for c in vector], dtype=np.uint8)


def shot_size_scale(shot_size: str, grammar_rules: dict) -> float:
    """景别档位 → 主体占比（规则库枚举序号线性映射：越远占比越小）。"""
    sizes = grammar_rules.get("shot_sizes")
    if not isinstance(sizes, (list, tuple)) or not sizes:
        raise ValidationError("规则库 shot_sizes 必须为非空列表（镜头语法规则库缺失）")
    if shot_size not in sizes:
        raise ValidationError(f"景别档位 {shot_size!r} 不在规则库 {list(sizes)} 内")
    rank = list(sizes).index(shot_size)
    if len(sizes) == 1:
        return _SUBJECT_MAX
    span = _SUBJECT_MAX - _SUBJECT_MIN
    return _SUBJECT_MIN + span * (len(sizes) - 1 - rank) / (len(sizes) - 1)


def camera_anchor(camera: str, side: str, width: int, height: int) -> tuple[int, int]:
    """机位档位 + 侧别 → 主体框中心锚点（A|B 左右镜像，越轴差异可机检）。"""
    anchors = {
        "eye_level": (width // 2, height // 2),
        "low_angle": (width // 2, height * 3 // 4),
        "high_angle": (width // 2, height // 4),
        "over_shoulder": (width // 3, height // 2),
        "side": (width * 3 // 4, height // 2),
    }
    if camera not in anchors:
        raise ValidationError(f"机位档位 {camera!r} 不在构图锚点表 {sorted(anchors)} 内")
    cx, cy = anchors[camera]
    if side == "B":
        cx = width - 1 - cx  # 反打侧：构图镜像（180° 线两侧的机位差异）
    elif side != "A":
        raise ValidationError(f"side 必须为 'A' 或 'B'，实际为 {side!r}")
    return cx, cy


def encode_index_bits(index: int) -> tuple[bool, ...]:
    """镜头序号 → 索引条 4 位（高位在左，与 decode_index_code 对称）。"""
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 2**INDEX_BITS:
        raise ValidationError(f"镜头序号必须 ∈ [0, {2**INDEX_BITS})，实际为 {index!r}")
    return tuple(bool((index >> (INDEX_BITS - 1 - bit)) & 1) for bit in range(INDEX_BITS))


def decode_index_code(frame: np.ndarray) -> int:
    """索引条解码：返回镜头序号（分镜卡身份可机检，不依赖像素全等比对）。"""
    if frame.ndim != 3 or frame.shape[0] < _INDEX_ROWS:
        raise ValidationError(f"分镜卡帧形状非法：{frame.shape!r}")
    block_width = max(1, frame.shape[1] // (2**INDEX_BITS))
    luma = frame.mean(axis=2)
    threshold = _INDEX_DECODE_THRESHOLD * float(luma.mean())
    value = 0
    for bit in range(INDEX_BITS):
        block = luma[0:_INDEX_ROWS, bit * block_width : (bit + 1) * block_width]
        value = (value << 1) | (1 if float(block.mean()) >= threshold else 0)
    return value


def shot_emotion(shot: ShotEntry | dict, script) -> str | None:
    """镜头情绪基调：covers 顺序上首个带标注的行；全部未标注 → None（不适用）。

    剧本承接关系已由 validate_shotlist 第①层前置校验；此处引用不到行即报错。
    """
    entry = _as_entry(shot)
    for line_id in entry.covers:
        emotion = script.emotion_of(line_id)
        if emotion is not None:
            return emotion
    return None


def _draw_subject(frame: np.ndarray, palette: np.ndarray, shot: ShotEntry, rules: dict) -> None:
    """主体框：景别档位决定占比、机位档位决定锚点（程序化构图表达镜头语言）。"""
    height, width = frame.shape[:2]
    scale = shot_size_scale(shot.shot_size, rules)
    box_w = max(2, int(width * scale))
    box_h = max(2, int(height * scale))
    cx, cy = camera_anchor(shot.camera, shot.side, width, height)
    x0 = min(max(0, cx - box_w // 2), width - box_w)
    y0 = min(max(_INDEX_ROWS, cy - box_h // 2), height - box_h)
    frame[y0 : y0 + box_h, x0 : x0 + box_w] = _shade(palette, _SUBJECT_FACTOR)


def _draw_movement_marker(
    frame: np.ndarray, palette: np.ndarray, shot: ShotEntry, index: int, rules: dict
) -> None:
    """运动标记条：运动档位决定纵向带位、镜头序号决定水平推移；静止档不画。"""
    movements = rules.get("movements")
    if not isinstance(movements, (list, tuple)) or not movements:
        raise ValidationError("规则库 movements 必须为非空列表（镜头语法规则库缺失）")
    if shot.movement not in movements:
        raise ValidationError(f"运动档位 {shot.movement!r} 不在规则库 {list(movements)} 内")
    ordinal = list(movements).index(shot.movement)
    if ordinal == 0:  # 静止档：无运动标记
        return
    height, width = frame.shape[:2]
    length = max(2, width // 4)
    span = max(1, width - length)
    x0 = (index * ordinal * 3) % span
    y0 = min(height - 1, (ordinal * height) // (len(movements) + 1))
    frame[y0 : y0 + 1, x0 : x0 + length] = _shade(palette, _MARKER_FACTOR)


def _draw_index_code(frame: np.ndarray, palette: np.ndarray, index: int) -> None:
    """索引条：帧顶两行 4 位二进制（亮块=1 / 暗块=0），镜头身份可机检。"""
    block_width = max(1, frame.shape[1] // (2**INDEX_BITS))
    for bit, on in enumerate(encode_index_bits(index)):
        factor = _INDEX_ON_FACTOR if on else _INDEX_OFF_FACTOR
        frame[0:_INDEX_ROWS, bit * block_width : (bit + 1) * block_width] = _shade(palette, factor)


def render_shot_card(
    shot: ShotEntry | dict,
    *,
    index: int,
    emotion: str | None,
    render_cfg: dict,
    grammar_rules: dict,
    emotion_vectors: dict,
) -> np.ndarray:
    """单镜分镜卡帧：情绪色板背景 + 主体框（景别/机位）+ 运动标记 + 索引条。"""
    cfg = _require_render_cfg(render_cfg)
    entry = _as_entry(shot)
    height, width = int(cfg["height"]), int(cfg["width"])
    palette = emotion_palette(emotion, emotion_vectors)
    frame = np.empty((height, width, 3), dtype=np.uint8)
    frame[...] = palette  # 背景 = 情绪基调色板（色板注入）
    _draw_subject(frame, palette, entry, grammar_rules)
    _draw_movement_marker(frame, palette, entry, index, grammar_rules)
    _draw_index_code(frame, palette, index)
    return frame


def frame_function_hash() -> str:
    """帧产出函数哈希 = 本实现文件 BLAKE3 前 8 位（judge/对齐代理版本号的一段）。

    分镜卡帧是对齐代理（proxy.emotion_alignment）的输入形成环节：实现文件任一变更
    （含构图/色板/索引条口径）即哈希变更 → 对齐代理版本变更（宪章原则一）。
    """
    return blake3.blake3(Path(__file__).read_bytes()).hexdigest()[:8]


def storyboard_cards(
    shotlist: ShotList,
    script,
    *,
    render_cfg: dict,
    grammar_rules: dict,
    emotion_vectors: dict,
) -> StoryboardCards:
    """ShotList + 剧本 → 分镜卡帧序列（**唯一分镜卡帧产出函数**：渲染件与评估输入共用）。

    逐镜情绪取自承接行的情绪标注（未标注 → 不适用，中性色板）。
    """
    if not isinstance(shotlist, ShotList):
        raise ValidationError(f"shotlist 必须为 ShotList，实际为 {shotlist!r}")
    _require_render_cfg(render_cfg)
    frames = []
    emotions: list[str | None] = []
    for index, shot in enumerate(shotlist.shots):
        emotion = shot_emotion(shot, script)
        emotions.append(emotion)
        frames.append(
            render_shot_card(
                shot,
                index=index,
                emotion=emotion,
                render_cfg=render_cfg,
                grammar_rules=grammar_rules,
                emotion_vectors=emotion_vectors,
            )
        )
    stack = np.stack(frames, axis=0)
    hashes = tuple(blake3.blake3(frame.tobytes()).hexdigest() for frame in frames)
    return StoryboardCards(
        frames=stack,
        frame_hashes=hashes,
        frames_hash=blake3.blake3(stack.tobytes()).hexdigest(),
        emotions=tuple(emotions),
    )


def shot_frame_count(shot: ShotEntry | dict, render_cfg: dict) -> int:
    """逐镜帧数 = est_duration_ms × fps // 1000（帧网格口径）；不足一帧即拒绝。"""
    cfg = _require_render_cfg(render_cfg)
    entry = _as_entry(shot)
    count = entry.est_duration_ms * int(cfg["fps"]) // 1000
    if count < 1:
        raise ValidationError(
            f"镜头 {entry.shot_id!r} 估算时长 {entry.est_duration_ms}ms 不足一帧"
            f"（{cfg['fps']}fps），无法生成分镜卡帧"
        )
    return count


def compose_frames(shotlist: ShotList, cards: StoryboardCards, render_cfg: dict) -> np.ndarray:
    """分镜卡 → 逐镜按时长网格重复拼接（定点整数帧数，无插值/无随机）。"""
    if not isinstance(shotlist, ShotList):
        raise ValidationError(f"shotlist 必须为 ShotList，实际为 {shotlist!r}")
    if len(cards.frames) != len(shotlist.shots):
        raise ValidationError(
            f"分镜卡帧数 {len(cards.frames)} 与镜头数 {len(shotlist.shots)} 不一致"
        )
    segments = [
        np.repeat(cards.frames[index][None], shot_frame_count(shot, render_cfg), axis=0)
        for index, shot in enumerate(shotlist.shots)
    ]
    return np.concatenate(segments, axis=0)


def encode_mp4_deterministic(frames: np.ndarray, fps: int) -> bytes:
    """007/004 同路径编码 + 单线程档：逐字节可复现。"""
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


def _temp_audio_mix(shotlist: ShotList, *, total_ms: int, sample_rate: int) -> np.ndarray:
    """临时音轨：ShotList 哈希派生频率的定点正弦（确定性；mp4 只承载画面轨）。"""
    n = total_ms * sample_rate // 1000
    seed = int(blake3.blake3(shotlist.canonical_json().encode()).hexdigest()[:8], 16)
    freq = 180.0 + seed % 300
    t = np.arange(n, dtype=np.float64) / sample_rate
    return (0.25 * np.sin(2.0 * np.pi * freq * t) * 32767.0).astype(np.int16)


def render_animatic(
    shotlist: ShotList,
    script,
    *,
    render_cfg: dict,
    grammar_rules: dict,
    emotion_vectors: dict,
    with_temp_audio: bool = False,
    sample_rate: int = 16000,
) -> tuple[bytes, dict]:
    """ShotList + 剧本 → (animatic mp4 字节, 元数据)。

    元数据（C10/C11，评估器输入）：duration_ms 总时长、shot_count 镜头数、
    shot_sizes 景别序列、shot_durations_ms 估算时长序列、shot_frame_counts 帧数序列、
    frame_hashes 逐镜分镜卡帧哈希（评估器来源一致性校验）、frames_hash 序列哈希、
    emotions 逐镜情绪基调、has_temp_audio 临时音轨标记（带音轨时附 temp_audio_mix_hash）。
    """
    cfg = _require_render_cfg(render_cfg)
    cards = storyboard_cards(
        shotlist,
        script,
        render_cfg=cfg,
        grammar_rules=grammar_rules,
        emotion_vectors=emotion_vectors,
    )
    frames = compose_frames(shotlist, cards, cfg)
    fps = int(cfg["fps"])
    duration_ms = len(frames) * 1000 // fps
    metadata = {
        "duration_ms": duration_ms,
        "shot_count": len(shotlist.shots),
        "shot_sizes": [shot.shot_size for shot in shotlist.shots],
        "shot_durations_ms": [shot.est_duration_ms for shot in shotlist.shots],
        "shot_frame_counts": [shot_frame_count(shot, cfg) for shot in shotlist.shots],
        "frame_hashes": list(cards.frame_hashes),
        "frames_hash": cards.frames_hash,
        "emotions": list(cards.emotions),
        "has_temp_audio": bool(with_temp_audio),
        "fps": fps,
    }
    if with_temp_audio:
        mix = _temp_audio_mix(shotlist, total_ms=duration_ms, sample_rate=sample_rate)
        metadata["temp_audio_mix_hash"] = blake3.blake3(mix.tobytes()).hexdigest()
    return encode_mp4_deterministic(frames, fps), metadata
