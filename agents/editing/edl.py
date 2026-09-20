"""剪辑决策列表（EDL）模型与执行前四层合法性校验（功能 007，research 决策 3）。

EditDecisionList：clips [{shot_id, in_ms, out_ms, transition:{type, duration_ms}}] 有序
+ audio [{track_ref, at_ms, gain}] 可选；规范化 JSON（键排序）即回放匹配键（C14 的
精确匹配依据），BLAKE3 哈希为运营表唯一键分量。

validate_edl 四层校验全部在执行前（违规 0 渲染 0 成本，FR-002）：
① 引用存在（镜头在库、音轨引用在库）；
② 0 ≤ in_ms < out_ms ≤ 镜头时长；
③ 场景分区（镜头归属分区正确 + 分区序号不降）；
④ 转场规则库（类型 ∈ allowed、叠化 ≤ dissolve_max_ms、同区跳切检测——配置驱动，
   与门禁 rule.transition_rules 共用同一规则库，配置单一事实源）。
"""

import json
from dataclasses import dataclass

import blake3

from agents.editing.shots import SceneStructure, ShotLibrary
from core.tree.errors import ValidationError


def _require_int_ms(value, field_name: str) -> int:
    """毫秒时间戳必须为非负整数（同 006 timing.py 口径）。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} 必须为整数毫秒时间戳，实际为 {value!r}")
    if value < 0:
        raise ValidationError(f"{field_name} 必须非负，实际为 {value!r}")
    return value


@dataclass(frozen=True)
class Transition:
    """转场：挂在 clip 上，描述该 clip 到下一 clip 的衔接方式。"""

    type: str
    duration_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type:
            raise ValidationError(f"transition.type 必须为非空字符串，实际为 {self.type!r}")
        _require_int_ms(self.duration_ms, "transition.duration_ms")

    def to_dict(self) -> dict:
        return {"type": self.type, "duration_ms": self.duration_ms}


@dataclass(frozen=True)
class Clip:
    """剪辑片段：镜头区间语义 [in_ms, out_ms)（毫秒）。"""

    shot_id: str
    in_ms: int
    out_ms: int
    transition: Transition | dict

    def __post_init__(self) -> None:
        if not isinstance(self.shot_id, str) or not self.shot_id:
            raise ValidationError(f"clip.shot_id 必须为非空字符串，实际为 {self.shot_id!r}")
        _require_int_ms(self.in_ms, "clip.in_ms")
        _require_int_ms(self.out_ms, "clip.out_ms")
        if isinstance(self.transition, dict):
            object.__setattr__(
                self,
                "transition",
                Transition(
                    type=self.transition.get("type"),
                    duration_ms=self.transition.get("duration_ms"),
                ),
            )
        elif not isinstance(self.transition, Transition):
            raise ValidationError(
                f"clip.transition 必须为 dict 或 Transition，实际为 {self.transition!r}"
            )

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "in_ms": self.in_ms,
            "out_ms": self.out_ms,
            "transition": self.transition.to_dict(),
        }


@dataclass(frozen=True)
class AudioCue:
    """音轨事件：track_ref 按 at_ms 时间戳摆放，gain 定点增益叠加（决策 9）。"""

    track_ref: str
    at_ms: int
    gain: float

    def __post_init__(self) -> None:
        if not isinstance(self.track_ref, str) or not self.track_ref:
            raise ValidationError(f"audio.track_ref 必须为非空字符串，实际为 {self.track_ref!r}")
        _require_int_ms(self.at_ms, "audio.at_ms")
        if not isinstance(self.gain, (int, float)) or isinstance(self.gain, bool):
            raise ValidationError(f"audio.gain 必须为数值，实际为 {self.gain!r}")
        if not 0.0 < float(self.gain) <= 1.0:
            raise ValidationError(f"audio.gain 必须 ∈ (0, 1]，实际为 {self.gain!r}")
        object.__setattr__(self, "gain", float(self.gain))

    def to_dict(self) -> dict:
        return {"track_ref": self.track_ref, "at_ms": self.at_ms, "gain": self.gain}


def _normalize_clip(item: Clip | dict) -> Clip:
    if isinstance(item, Clip):
        return item
    if isinstance(item, dict):
        return Clip(
            shot_id=item.get("shot_id"),
            in_ms=item.get("in_ms"),
            out_ms=item.get("out_ms"),
            transition=item.get("transition"),
        )
    raise ValidationError(f"clip 必须为 dict 或 Clip，实际为 {item!r}")


def _normalize_audio(item: AudioCue | dict) -> AudioCue:
    if isinstance(item, AudioCue):
        return item
    if isinstance(item, dict):
        return AudioCue(
            track_ref=item.get("track_ref"),
            at_ms=item.get("at_ms"),
            gain=item.get("gain"),
        )
    raise ValidationError(f"audio 必须为 dict 或 AudioCue，实际为 {item!r}")


@dataclass(frozen=True)
class EditDecisionList:
    """剪辑决策列表：clips 有序 + audio 可选；构造做形状校验，语义校验走 validate_edl。"""

    clips: tuple[Clip | dict, ...] | list[Clip | dict]
    audio: tuple[AudioCue | dict, ...] | list[AudioCue | dict] = ()

    def __post_init__(self) -> None:
        clips = tuple(_normalize_clip(c) for c in self.clips)
        if not clips:
            raise ValidationError("EDL clips 不能为空")
        object.__setattr__(self, "clips", clips)
        object.__setattr__(self, "audio", tuple(_normalize_audio(a) for a in self.audio))

    @classmethod
    def from_dict(cls, data: dict) -> "EditDecisionList":
        if not isinstance(data, dict):
            raise ValidationError(f"EDL 必须为 dict，实际为 {data!r}")
        return cls(clips=data.get("clips"), audio=data.get("audio", ()))

    def to_dict(self) -> dict:
        return {
            "clips": [c.to_dict() for c in self.clips],
            "audio": [a.to_dict() for a in self.audio],
        }

    def canonical_json(self) -> str:
        """规范化 JSON（键排序）：回放精确匹配键（同 004/006 canonical 口径）。"""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def edl_hash(self) -> str:
        """规范化 BLAKE3（运营表唯一键 (round_id, edl_hash) 分量）。"""
        return blake3.blake3(self.canonical_json().encode()).hexdigest()

    def total_duration_ms(self) -> int:
        """成片名义时长：末 clip 出点口径不适用拼接——逐段求和减叠化重叠。

        叠化时长在两段间重叠一次；cut/fade 不重叠（fade 为淡出黑场不压缩时长）。
        """
        total = 0
        for i, clip in enumerate(self.clips):
            total += clip.out_ms - clip.in_ms
            if i + 1 < len(self.clips) and clip.transition.type == "dissolve":
                total -= min(clip.transition.duration_ms, clip.out_ms - clip.in_ms)
        return total


def validate_edl(
    edl: EditDecisionList,
    shot_library: ShotLibrary,
    scenes: SceneStructure,
    transition_rules: dict,
) -> None:
    """执行前四层合法性校验：违规即 ValidationError（0 渲染 0 成本，C1）。

    transition_rules 为配置规则库（editing.transition_rules 段），
    与门禁 rule.transition_rules 共用——执行前校验 = 门禁的提前计算（决策 3）。
    """
    if not isinstance(edl, EditDecisionList):
        raise ValidationError(f"edl 必须为 EditDecisionList，实际为 {edl!r}")
    allowed = transition_rules.get("allowed")
    if not isinstance(allowed, (list, tuple)) or not allowed:
        raise ValidationError("transition_rules.allowed 必须为非空列表（规则库配置缺失）")
    dissolve_max_ms = transition_rules.get("dissolve_max_ms")
    if not isinstance(dissolve_max_ms, int) or isinstance(dissolve_max_ms, bool):
        raise ValidationError("transition_rules.dissolve_max_ms 必须为整数（规则库配置缺失）")
    forbid_jump_cut = bool(transition_rules.get("forbid_jump_cut_within_scene", False))

    # 第①层：引用存在（镜头在库、音轨引用在库）
    for clip in edl.clips:
        if not shot_library.has_shot(clip.shot_id):
            raise ValidationError(f"EDL 引用不存在镜头 {clip.shot_id!r}（第①层：引用存在）")
    for cue in edl.audio:
        if not shot_library.has_audio(cue.track_ref):
            raise ValidationError(f"EDL 引用不存在音轨 {cue.track_ref!r}（第①层：引用存在）")

    # 第②层：0 ≤ in_ms < out_ms ≤ 镜头时长（非负已由构造保证）
    for clip in edl.clips:
        duration_ms = shot_library.get(clip.shot_id).duration_ms
        if not clip.in_ms < clip.out_ms:
            raise ValidationError(
                f"clip {clip.shot_id!r} 要求 in_ms < out_ms，"
                f"实际为 {clip.in_ms} >= {clip.out_ms}（第②层：出入点）"
            )
        if clip.out_ms > duration_ms:
            raise ValidationError(
                f"clip {clip.shot_id!r} 出点越界：out_ms={clip.out_ms} > "
                f"镜头时长 {duration_ms}（第②层：出入点）"
            )

    # 第③层：场景分区（归属正确——未分区镜头即跨分区违规；分区序号不降）
    indices = [scenes.scene_index_of(clip.shot_id) for clip in edl.clips]
    for prev, curr in zip(indices, indices[1:], strict=False):
        if curr < prev:
            raise ValidationError(
                f"EDL 场景乱序：分区序号 {prev} → {curr} 回退（第③层：场景顺序不降）"
            )

    # 第④层：转场规则库（配置驱动，与门禁共用同一规则库）
    for i, clip in enumerate(edl.clips):
        transition = clip.transition
        if transition.type not in allowed:
            raise ValidationError(
                f"转场类型 {transition.type!r} 不在规则库 allowed={list(allowed)}（第④层）"
            )
        if transition.type == "dissolve" and transition.duration_ms > dissolve_max_ms:
            raise ValidationError(
                f"叠化时长 {transition.duration_ms}ms 超 dissolve_max_ms="
                f"{dissolve_max_ms}ms（第④层）"
            )
        if (
            forbid_jump_cut
            and transition.type == "cut"
            and i + 1 < len(edl.clips)
            and indices[i + 1] == indices[i]
        ):
            raise ValidationError(
                f"同区跳切：{clip.shot_id!r} → {edl.clips[i + 1].shot_id!r} 同分区 "
                f"用 cut 衔接（forbid_jump_cut_within_scene 开启，第④层）"
            )
