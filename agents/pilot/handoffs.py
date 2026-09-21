"""四段交接纯映射（功能 015 US2 / T1515，契约 C5~C9）。

四段交接把"上游 Agent 的产物视图"映射为"下游 Agent 的输入视图"：
剧本 → 分镜段落（复用 009 `export_segment`）、分镜 → 视觉生成参数、视听产物 → 剪辑输入、
成片 → 宣发物料。全部为**纯函数**（不读文件、不落树、不调外部服务），
输入输出都是 JSON 可序列化的 dict / 下游 schema 对象，便于随运行记录落盘与续跑恢复。

**双向字段集锁定**：每段给出 `FieldParity(upstream, downstream, dropped)`，并断言
`upstream == downstream | dropped` 且 `downstream ∩ dropped == ∅`——**丢字段必须写进
声明**（不许静默丢）。枚举值（行种类/情绪/景别/机位/运动/侧别）逐条保真，
数量守恒（镜头数 == 参数数、片段数 == 镜头库条目数）即断言。

**形态无关**（宪章原则五）：本模块只从配置读取规格值（片段尺寸、物料规格），
不做任何形态分支；上游未产出的东西如实标注（如无音轨 → `has_audio=False`），
不伪造输入。
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

from agents.editing.shots import Scene, SceneStructure, ShotEntry, ShotLibrary
from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.export_segment import export_segment
from agents.storyboard.script import ScriptSegment, validate_script
from agents.storyboard.shotlist import ShotList
from core.tree.errors import ValidationError


class HandoffError(Exception):
    """交接契约违反（字段集不一致 / 数量不守恒 / 上游不合格 / 缺件）。"""


@dataclass(frozen=True)
class FieldParity:
    """字段映射声明：上游导出字段集 / 下游输入字段集 / 显式声明丢弃与派生字段集。

    守恒等式：`下游 == (上游 − 丢弃) ∪ 派生`，且 `丢弃 ⊆ 上游`、`派生 ∩ 上游 == ∅`。
    即：下游字段要么来自上游（可追溯），要么是**声明过的**派生新增（如由形态配置派生的
    尺寸/帧率），要么是**声明过的**丢弃——三者之外即漂移，立即报错。
    """

    label: str
    upstream: frozenset[str]
    downstream: frozenset[str]
    dropped: frozenset[str] = frozenset()
    derived: frozenset[str] = frozenset()

    def consistent(self) -> bool:
        expected = (self.upstream - self.dropped) | self.derived
        return (
            self.downstream == expected
            and self.dropped <= self.upstream
            and not (self.derived & self.upstream)
        )

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "upstream": sorted(self.upstream),
            "downstream": sorted(self.downstream),
            "dropped": sorted(self.dropped),
            "derived": sorted(self.derived),
            "consistent": self.consistent(),
        }

    def assert_consistent(self) -> None:
        if not self.consistent():
            raise HandoffError(
                f"{self.label} 字段集不一致：上游 {sorted(self.upstream)}，"
                f"下游 {sorted(self.downstream)}，丢弃 {sorted(self.dropped)}，"
                f"派生 {sorted(self.derived)}"
                "（等式：下游 == (上游 − 丢弃) ∪ 派生——丢字段/派生字段必须写进声明）"
            )


# ---------------------------------------------------------------------------
# C5 剧本 → 分镜
# ---------------------------------------------------------------------------

# 行字段映射：上游 LineEntry 的行面字段 → 下游 ScriptLine 字段（丢弃项显式声明）。
# `scene_id` 在下游由**结构**承载（行归属场景），`character` 不计入 008 分镜契约
# （角色表经 coverage/实体一致性在剧本侧判定）——两者都是 009 既定映射，此处锁定防漂移。
SCRIPT_LINE_FIELDS = frozenset(
    {"line_id", "scene_id", "kind", "character", "text", "key", "emotion"}
)
SEGMENT_LINE_FIELDS = frozenset({"line_id", "kind", "text", "key", "emotion"})
SEGMENT_LINE_DROPPED = frozenset({"scene_id", "character"})


def script_to_segment(artifact: ScriptArtifact) -> ScriptSegment:
    """剧本工件 → 008 分镜段落（复用 009 `export_segment`，再做双向锁定与下游预检）。"""
    if not isinstance(artifact, ScriptArtifact):
        raise HandoffError(f"剧本交接输入必须为 ScriptArtifact，实际为 {artifact!r}")
    if not artifact.scenes:
        raise HandoffError("剧本不足：场景列表为空（下游拒绝启动，不静默产空分镜）")
    segment = export_segment(artifact)
    _assert_counts("剧本", artifact.total_lines(), len(segment.line_ids()))
    script_parity(artifact).assert_consistent()
    try:
        validate_script(segment)
    except ValidationError as exc:  # 下游预检失败 → 交接拒绝（不静默降级）
        raise HandoffError(f"分镜侧剧本预检拒绝：{exc}") from exc
    _assert_segment_values(artifact, segment)
    return segment


def script_parity(artifact: ScriptArtifact) -> FieldParity:
    """C5 双向快照：上游行字段集 == 下游行字段集 ∪ 声明丢弃字段。"""
    if not isinstance(artifact, ScriptArtifact):
        raise HandoffError(f"剧本交接输入必须为 ScriptArtifact，实际为 {artifact!r}")
    upstream = {field for line in artifact.lines for field in line.to_dict()}
    return FieldParity(
        label="剧本→分镜（行字段）",
        upstream=frozenset(upstream) or SCRIPT_LINE_FIELDS,
        downstream=SEGMENT_LINE_FIELDS,
        dropped=SEGMENT_LINE_DROPPED,
    )


def _assert_segment_values(artifact: ScriptArtifact, segment: ScriptSegment) -> None:
    """枚举值与关键行逐条保真（含情绪枚举）。"""
    by_id = {line.line_id: line for line in artifact.lines}
    for scene in segment.scenes:
        for line in scene.lines:
            source = by_id.get(line.line_id)
            if source is None:
                raise HandoffError(f"下游出现上游不存在的行 {line.line_id!r}")
            if (line.kind, line.key, line.emotion) != (source.kind, source.key, source.emotion):
                raise HandoffError(
                    f"行 {line.line_id!r} 枚举值漂移："
                    f"{source.kind}/{source.key}/{source.emotion} → "
                    f"{line.kind}/{line.key}/{line.emotion}"
                )


# ---------------------------------------------------------------------------
# C6 分镜 → 视觉
# ---------------------------------------------------------------------------

# 镜头字段映射：`covers`（承接清单）在视觉侧无面（分镜侧 coverage 门禁已判定），
# `alternatives` 为策略侧候选数（不是生成参数）。
SHOT_FIELDS = frozenset(
    {
        "shot_id",
        "scene_id",
        "covers",
        "shot_size",
        "camera",
        "side",
        "movement",
        "est_duration_ms",
        "alternatives",
    }
)
GEN_PARAMS_FIELDS = frozenset(
    {
        "shot_id",
        "scene_id",
        "shot_size",
        "camera",
        "side",
        "movement",
        "style",
        "seed_tier",
        "duration_s",
        "est_duration_ms",
        "width",
        "height",
        "fps",
    }
)
GEN_PARAMS_DROPPED = frozenset({"covers", "alternatives"})
# 派生新增字段：全部由**形态配置**（clip_spec）与镜序派生，非上游字段
GEN_PARAMS_DERIVED = frozenset({"style", "seed_tier", "duration_s", "width", "height", "fps"})

# 风格档位（视觉模拟生成器的可辨识样式档；由景别/机位派生，枚举固定）
_STYLE_BY_SIZE = {
    "extreme_close_up": "特写",
    "close_up": "特写",
    "medium": "中景",
    "full": "全景",
    "wide": "全景",
}


def shotlist_to_gen_params(shotlist: ShotList, *, config: Any) -> list[dict]:
    """分镜 → 每镜一组视觉生成参数（**尺寸/帧率/时长一律来自形态配置**）。

    接线纪律（重要）：模拟视频生成器在 `gen_params` 缺 `width`/`height` 时回落
    320x240，而 `rule.format_compliance` 按配置 `clip_spec` 对照——尺寸必须由配置派生，
    否则每镜都会被门禁判 0。
    """
    if not isinstance(shotlist, ShotList):
        raise HandoffError(f"分镜交接输入必须为 ShotList，实际为 {shotlist!r}")
    spec = _clip_spec(config)
    shots = tuple(shotlist.shots)
    if not shots:
        raise HandoffError("分镜为空：下游视觉阶段拒绝启动（不静默产空片段）")
    params = []
    for index, shot in enumerate(shots):
        params.append(
            {
                "shot_id": shot.shot_id,
                "scene_id": shot.scene_id,
                "shot_size": shot.shot_size,
                "camera": shot.camera,
                "side": shot.side,
                "movement": shot.movement,
                "style": _STYLE_BY_SIZE.get(shot.shot_size, "中景"),
                "seed_tier": index % 3 + 1,
                "duration_s": float(spec["duration_seconds"]),
                "est_duration_ms": int(shot.est_duration_ms),
                "width": int(spec["width"]),
                "height": int(spec["height"]),
                "fps": int(spec["fps"]),
            }
        )
    _assert_counts("分镜", len(shots), len(params))
    shotlist_parity(shotlist, params).assert_consistent()
    return params


def shotlist_parity(shotlist: ShotList, params: Sequence[Mapping]) -> FieldParity:
    """C6 双向快照：镜头字段集 == 生成参数字段集 ∪ 声明丢弃字段（数量守恒）。"""
    _assert_counts("分镜", len(tuple(shotlist.shots)), len(tuple(params)))
    upstream = {field for shot in shotlist.shots for field in shot.to_dict()}
    downstream = {field for param in params for field in param}
    parity = FieldParity(
        label="分镜→视觉（镜头字段）",
        upstream=frozenset(upstream) or SHOT_FIELDS,
        downstream=frozenset(downstream),
        dropped=GEN_PARAMS_DROPPED,
        derived=GEN_PARAMS_DERIVED,
    )
    if not GEN_PARAMS_FIELDS <= parity.downstream:
        raise HandoffError(
            f"生成参数缺字段：{sorted(GEN_PARAMS_FIELDS - parity.downstream)}（不得缺项）"
        )
    return parity


def _clip_spec(config: Any) -> Mapping:
    spec = getattr(config, "clip_spec", None)
    if not isinstance(spec, Mapping):
        raise HandoffError("形态配置缺少 clip_spec（片段规格，缺即拒绝：不静默用默认尺寸）")
    for key in ("width", "height", "fps", "duration_seconds"):
        if key not in spec:
            raise HandoffError(f"clip_spec 缺字段 {key!r}（尺寸/帧率/时长必须由配置派生）")
    return spec


# ---------------------------------------------------------------------------
# C7 视觉 + 声音 → 剪辑
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditInputs:
    """剪辑输入：镜头库 + 分区结构 + 音轨（无音轨时如实标注 `has_audio=False`）。"""

    shot_library: ShotLibrary
    scene_structure: SceneStructure
    audio_tracks: tuple[str, ...]
    has_audio: bool

    def to_dict(self) -> dict:
        return {
            "shot_library": {
                "shots": [
                    {
                        "shot_id": shot.shot_id,
                        "artifact_hash": shot.artifact_hash,
                        "duration_ms": shot.duration_ms,
                        "scene_id": shot.scene_id,
                        "metadata": dict(shot.metadata),
                    }
                    for shot in self.shot_library.shots
                ],
                "audio_tracks": list(self.shot_library.audio_tracks),
            },
            "scene_structure": {
                "scenes": [
                    {"scene_id": scene.scene_id, "shot_ids": list(scene.shot_ids)}
                    for scene in self.scene_structure.scenes
                ]
            },
            "audio_tracks": list(self.audio_tracks),
            "has_audio": self.has_audio,
        }


CLIP_FIELDS = frozenset(
    {"shot_id", "scene_id", "artifact_hash", "duration_ms", "gen_params", "score"}
)
SHOT_LIBRARY_FIELDS = frozenset({"shot_id", "scene_id", "artifact_hash", "duration_ms", "metadata"})
CLIP_DROPPED = frozenset({"gen_params", "score"})
# 派生新增字段：镜头库保留的元数据扩展面（本次交接置空，键始终存在）
CLIP_DERIVED = frozenset({"metadata"})


def av_to_edit_inputs(
    clips: Sequence[Mapping], audio: Mapping | None, *, config: Any
) -> EditInputs:
    """视觉片段 + 声音工件 → 剪辑镜头库与音轨（无音轨路径如实标注）。

    - 镜头库条目与片段**一一对应**（数量守恒，不静默丢片段）；
    - 分区结构按片段的 `scene_id` 归组（顺序 = 片段顺序），归属一致性由
      `SceneStructure` 构造即校验；
    - 音轨引用取自声音阶段明细；`audio=None` 表示上游未产出音轨 → 空音轨 + 如实标注。
    """
    del config  # 剪辑输入面不读形态规格（镜头时长/归属全部来自上游产出）
    clip_list = list(clips)
    if not clip_list:
        raise HandoffError("片段为空：剪辑阶段拒绝启动（不静默产空镜头库）")
    shots: list[ShotEntry] = []
    for clip in clip_list:
        missing = {"shot_id", "artifact_hash", "duration_ms", "scene_id"} - set(clip)
        if missing:
            raise HandoffError(f"片段明细缺字段 {sorted(missing)}（交接不得缺项）")
        shots.append(
            ShotEntry(
                shot_id=clip["shot_id"],
                artifact_hash=clip["artifact_hash"],
                duration_ms=int(clip["duration_ms"]),
                scene_id=clip["scene_id"],
                metadata=dict(clip.get("metadata") or {}),
            )
        )
    tracks, track_refs = _audio_tracks(audio)
    library = ShotLibrary(shots=shots, audio_tracks=track_refs)
    scenes: list[Scene] = []
    for shot in library.shots:
        if scenes and scenes[-1].scene_id == shot.scene_id:
            scenes[-1] = Scene(
                scene_id=shot.scene_id, shot_ids=(*scenes[-1].shot_ids, shot.shot_id)
            )
            continue
        scenes.append(Scene(scene_id=shot.scene_id, shot_ids=(shot.shot_id,)))
    structure = SceneStructure(scenes=tuple(scenes), shot_library=library)
    edits = EditInputs(
        shot_library=library,
        scene_structure=structure,
        audio_tracks=track_refs,
        has_audio=bool(track_refs),
    )
    edit_inputs_parity(clip_list, edits).assert_consistent()
    return edits


def _audio_tracks(audio: Mapping | None) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """音轨明细 → (明细, 引用集合)；无音轨（None 或空 tracks）→ 空集合（如实标注）。"""
    if audio is None:
        return (), ()
    tracks = list(audio.get("tracks") or ())
    refs = []
    for index, track in enumerate(tracks):
        gen_type = track.get("gen_type")
        artifact_hash = track.get("artifact_hash")
        if not gen_type or not artifact_hash:
            raise HandoffError(f"音轨明细缺 gen_type/artifact_hash（第 {index} 条）")
        refs.append(f"{gen_type}-{str(artifact_hash)[:12]}")
    if len(set(refs)) != len(refs):
        raise HandoffError("音轨引用重复（同一类型同一工件只应引用一次）")
    return tuple(tracks), tuple(refs)


def edit_inputs_parity(clips: Sequence[Mapping], edits: EditInputs) -> FieldParity:
    """C7 双向快照：片段字段集 == 镜头库字段集 ∪ 声明丢弃字段（数量守恒）。"""
    clip_list = list(clips)
    _assert_counts("视听→剪辑", len(clip_list), len(edits.shot_library.shots))
    upstream = {field for clip in clip_list for field in clip}
    downstream = {field.name for shot in edits.shot_library.shots for field in fields(shot)}
    parity = FieldParity(
        label="视听→剪辑（镜头字段）",
        upstream=frozenset(upstream) or CLIP_FIELDS,
        downstream=frozenset(downstream),
        dropped=CLIP_DROPPED,
        derived=CLIP_DERIVED,
    )
    if not SHOT_LIBRARY_FIELDS <= parity.downstream:
        raise HandoffError(
            f"镜头库缺字段：{sorted(SHOT_LIBRARY_FIELDS - parity.downstream)}（不得缺项）"
        )
    return parity


# ---------------------------------------------------------------------------
# C8 成片 → 宣发
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromoMaterials:
    """宣发物料素材：成片片段 / 封面 / 文案三件（规格全部来自形态配置）。"""

    materials: tuple[dict, ...]
    reel_ref: str
    reel_hash: str

    def to_dict(self) -> dict:
        return {
            "materials": [dict(material) for material in self.materials],
            "reel_ref": self.reel_ref,
            "reel_hash": self.reel_hash,
        }


MATERIAL_FIELDS = frozenset({"material_id", "kind", "spec", "source_ref", "source_hash"})


def reel_to_promo_materials(reel: Mapping, *, config: Any) -> PromoMaterials:
    """成片 → 宣发物料素材引用（成片片段按配置时长裁剪、封面按配置尺寸、文案按配置字数）。"""
    spec = _material_spec(config)
    reel_hash = str(reel.get("artifact_hash") or "")
    if not reel_hash:
        raise HandoffError("成片交接输入缺少 artifact_hash（素材引用不得凭空）")
    duration_ms = int(reel.get("duration_ms") or 0)
    if duration_ms <= 0:
        raise HandoffError("成片时长缺失或非正（素材引用须可核）")
    clip_ms = min(duration_ms, int(spec["max_duration_seconds"]) * 1000)
    reel_ref = f"reel-{reel_hash[:12]}"
    materials = (
        {
            "material_id": "material-clip-1",
            "kind": "clip",
            "spec": {
                "duration_ms": clip_ms,
                "width": reel.get("width"),
                "height": reel.get("height"),
            },
            "source_ref": reel_ref,
            "source_hash": reel_hash,
        },
        {
            "material_id": "material-poster-1",
            "kind": "poster",
            "spec": {"poster_size": str(spec["poster_size"])},
            "source_ref": reel_ref,
            "source_hash": reel_hash,
        },
        {
            "material_id": "material-copy-1",
            "kind": "copy",
            "spec": {"max_copy_chars": int(spec["max_copy_chars"])},
            "source_ref": reel_ref,
            "source_hash": reel_hash,
        },
    )
    for material in materials:
        for key in MATERIAL_FIELDS:
            if key not in material:
                raise HandoffError(f"物料缺字段 {key!r}（素材引用与元数据必须齐备）")
    return PromoMaterials(materials=materials, reel_ref=reel_ref, reel_hash=reel_hash)


def _material_spec(config: Any) -> Mapping:
    spec = getattr(config, "material_spec", None)
    if not isinstance(spec, Mapping):
        raise HandoffError("形态配置缺少 material_spec（物料规格，缺即拒绝：不静默用默认规格）")
    for key in ("max_copy_chars", "poster_size", "max_duration_seconds"):
        if key not in spec:
            raise HandoffError(f"material_spec 缺字段 {key!r}（物料规格必须由配置派生）")
    return spec


# ---------------------------------------------------------------------------
# 共用校验
# ---------------------------------------------------------------------------


def _assert_counts(label: str, upstream: int, downstream: int) -> None:
    if upstream != downstream:
        raise HandoffError(
            f"{label} 交接数量不守恒：上游 {upstream} → 下游 {downstream}（一一对应，不静默丢弃）"
        )


def canonical_json(payload: Any) -> str:
    """规范化 JSON（键排序）：交接明细进运行记录/样片包的口径。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "CLIP_DERIVED",
    "CLIP_DROPPED",
    "CLIP_FIELDS",
    "EditInputs",
    "FieldParity",
    "GEN_PARAMS_DERIVED",
    "GEN_PARAMS_DROPPED",
    "GEN_PARAMS_FIELDS",
    "HandoffError",
    "MATERIAL_FIELDS",
    "PromoMaterials",
    "SEGMENT_LINE_DROPPED",
    "SEGMENT_LINE_FIELDS",
    "SHOT_LIBRARY_FIELDS",
    "av_to_edit_inputs",
    "canonical_json",
    "edit_inputs_parity",
    "reel_to_promo_materials",
    "script_parity",
    "script_to_segment",
    "shotlist_parity",
    "shotlist_to_gen_params",
]
