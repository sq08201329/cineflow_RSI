"""五段交接纯映射（功能 015 US2 / T1515，契约 C5~C9 + 功能 018 US2 的 `dev` → 剧本交接）。

四段交接把"上游 Agent 的产物视图"映射为"下游 Agent 的输入视图"：
剧本 → 分镜段落（复用 009 `export_segment`）、分镜 → 视觉生成参数、视听产物 → 剪辑输入、
成片 → 宣发物料。全部为**纯函数**（不读文件、不落树、不调外部服务），
输入输出都是 JSON 可序列化的 dict / 下游 schema 对象，便于随运行记录落盘与续跑恢复。

**双向字段集锁定**：每段给出 `FieldParity(upstream, downstream, dropped)`，并断言
`upstream == downstream | dropped` 且 `downstream ∩ dropped == ∅`——**丢字段必须写进
声明**（不许静默丢）。枚举值（行种类/情绪/景别/机位/运动/侧别）逐条保真，
数量守恒（镜头数 == 参数数、片段数 == 镜头库条目数）即断言。

**功能 018 的第五段（`dev` → 剧本）另有形状**（契约 C5/C6/C7）：上游是 017 的**选题产出
导出面**（`export_slate`），下游是剧本阶段的输入视图。该段沿用"丢字段必须声明"的纪律，但
**多出两类来源**——**运行级**与**改名承接**，故守恒等式扩为
`下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`：
`reads` 是**三类**来源（承接含改名 / 运行级 / 派生，合计且每键恰一类），`dropped` 是与之
**并列的独立集合**（不是第四类），另有第三类登记项**取数依据**（`entries`/`production_marks`，
选条入口，既不进 `reads` 也不进 `dropped`）。**承接 ≠ 丢弃**：`genre → topic` 是改名承接、
登记在 `renames`（`genre` 不进丢弃集）。`FieldParity.consistent()` 的三式**只适用于同名字段
子集**（原样保留于上游 → 下游的同名映射层）；改名承接/运行级层**不复用**它，另立断言 ①~⑥
（`DevScriptHandoff.violations()`）——两层断言互不放宽、都须成立。

**形态无关**（宪章原则五）：本模块只从配置读取规格值（片段尺寸、物料规格），
不做任何形态分支；上游未产出的东西如实标注（如无音轨 → `has_audio=False`），
不伪造输入。
"""

import ast
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from agents.dev import artifact as dev_artifact
from agents.dev import export_slate as export_slate_module
from agents.editing.shots import Scene, SceneStructure, ShotEntry, ShotLibrary
from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.export_segment import export_segment
from agents.screenplay.loop import SCRIPT_INPUT_READS
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
# C5/C6/C7 链首 `dev` → 剧本的字段级交接（功能 018 US2）
# ---------------------------------------------------------------------------

# 生效来源（FR-016 二值，无第三值；禁止静默择一）
HANDOFF_MODE_DEV = "dev_script_handoff"
HANDOFF_MODE_RUN_LEVEL = "run_level_pilot_inputs"
HANDOFF_MODES = (HANDOFF_MODE_DEV, HANDOFF_MODE_RUN_LEVEL)
# `reads` 的三类来源（`dropped` 是与之**并列的独立集合**，不是同一分类的第四项）
READ_CLASSES = ("承接", "运行级", "派生")
# 取数依据（第三类登记项）：选条入口，既不进 `reads`、也不进 `dropped`
SELECTION_SOURCES = frozenset({"entries", "production_marks"})
# 上游可移交要点集合（契约 B-01：守恒等式里的"上游"= 可移交要点，不是整张 017 导出面）
TRANSFERABLE_FIELDS = frozenset({"genre", "constraints", "characters"})
# 本交接的丢弃项：可移交要点之外、又不进下游的上游字段（要点**不得**进本集）
DEV_SCRIPT_DROPPED = frozenset(
    {
        "direction_id",
        "rationale",
        "eval_components",
        "in_production",
        "schema_version",
        "signal_sources",
    }
)
# 改名承接（下游键 → 上游要点字段名；同名承接登记为**恒等映射**，不得漏登）
DEV_SCRIPT_RENAMES = {"topic": "genre", "constraints": "constraints", "characters": "characters"}
# 逐键来源类（有 `dev`：三键承接 + 运行级时长）
DEV_SCRIPT_READS = {
    "topic": "承接",
    "constraints": "承接",
    "characters": "承接",
    "target_duration_min": "运行级",
}
# 逐键来源类（无 `dev`：四键全运行级，回落既有短剧链）
RUN_LEVEL_READS = {key: "运行级" for key in sorted(SCRIPT_INPUT_READS)}
# 本侧声明所依据的上游 schema 版本（C7 三处同步之一：上游升版即须同批更新）
UPSTREAM_SCHEMA_VERSION = "1.0.0"
# 读取集锁定的源码域（两式 `inputs[...]` / `inputs.get(...)` 逐点扫描）
READ_SCAN_FILES = ("agents/screenplay/loop.py", "agents/pilot/stages.py")


def repo_root() -> Path:
    """仓库根（本文件位于 `agents/pilot/`）。"""
    return Path(__file__).resolve().parents[2]


def _upstream_face() -> tuple[frozenset[str], frozenset[str]]:
    """上游导出面的**反解引用**（顶层 / 条目级字段集）：唯一真相是 017 的常量，不得复制。"""
    return (
        frozenset(export_slate_module.EXPORT_FIELDS),
        frozenset(export_slate_module.EXPORT_ENTRY_FIELDS),
    )


def _assert_upstream_synced() -> None:
    """C7 两侧同步机检：上游导出面增删字段、或 `SCHEMA_VERSION` 升版而未同步本侧声明 ⇒ 报错。"""
    top, entry = _upstream_face()
    if dev_artifact.SCHEMA_VERSION != UPSTREAM_SCHEMA_VERSION:
        raise HandoffError(
            f"上游 SCHEMA_VERSION 已变（{dev_artifact.SCHEMA_VERSION} != "
            f"{UPSTREAM_SCHEMA_VERSION}）：本侧交接声明必须同批复核（两侧同步，缺一即红）"
        )
    missing_essentials = sorted(TRANSFERABLE_FIELDS - entry)
    if missing_essentials:
        raise HandoffError(
            f"上游导出面缺少可移交要点 {missing_essentials}：本交接的上游界定为可移交要点集合，"
            "字段消失即两侧不同步"
        )
    undeclared = sorted(
        (top | entry) - (TRANSFERABLE_FIELDS | DEV_SCRIPT_DROPPED | SELECTION_SOURCES)
    )
    if undeclared:
        raise HandoffError(
            f"上游导出面出现未声明字段 {undeclared}：本侧交接声明必须同批更新"
            "（标为可移交要点 / 写入丢弃集 / 登记为取数依据；两侧同步，缺一即红）"
        )
    missing_basis = sorted(SELECTION_SOURCES - (top | entry))
    if missing_basis:
        raise HandoffError(f"取数依据 {missing_basis} 不再是上游导出面字段：本侧取数入口须同批复核")
    unbacked = sorted(DEV_SCRIPT_DROPPED - (top | entry))
    if unbacked:
        raise HandoffError(
            f"本侧丢弃集声明了导出面上不存在的字段 {unbacked}：上游字段集变更须两侧同步"
        )


@dataclass(frozen=True)
class DevScriptHandoff:
    """`dev` → 剧本的字段级交接声明（契约 C5/C6）。

    守恒等式：`下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`，且
    `丢弃 ⊆ 上游`、`丢弃 ∩ (承接映射值集 ∪ 运行级键集 ∪ 派生键集) == ∅`、
    `派生 ∩ (上游 ∪ 运行级 ∪ 承接映射值集) == ∅`。
    来源分类是 `reads` 的**三类**（承接含改名 / 运行级 / 派生，合计且每键恰一类）；
    `dropped` 是与之**并列的独立集合**；`sources` 是第三类登记项（取数依据）。

    **两层断言**：同名字段子集层用 `FieldParity.consistent()`；改名承接/运行级层
    **不复用**它（`renames` 引入后下游键名与上游字段名不再逐一对应，逐式不可能也不应成立），
    由本类的 `violations()` 给出机检等价的 ①~⑥。
    """

    mode: str
    reads: Mapping[str, str]
    renames: Mapping[str, str]
    dropped: frozenset[str]
    derived: frozenset[str]
    sources: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reads", dict(self.reads))
        object.__setattr__(self, "renames", dict(self.renames))
        object.__setattr__(self, "dropped", frozenset(self.dropped))
        object.__setattr__(self, "derived", frozenset(self.derived))
        object.__setattr__(self, "sources", frozenset(self.sources))

    def by_class(self, name: str) -> frozenset[str]:
        """某一来源类下的读取键集（`reads` 三类的切片）。"""
        return frozenset(key for key, cls in self.reads.items() if cls == name)

    def violations(self) -> list[str]:
        """承接/运行级层的机检（C6 断言 ①~⑥）：返回违反清单（空列表 = 通过）。"""
        problems: list[str] = []
        if self.mode not in HANDOFF_MODES:
            problems.append(
                f"mode 取值非法 {self.mode!r}：只接受 {list(HANDOFF_MODES)}（无第三值）"
            )
        # ① 读取集：合计（每个读取键都有类）且每键恰一类
        unknown_classes = sorted(set(self.reads.values()) - set(READ_CLASSES))
        if unknown_classes:
            problems.append(
                f"① reads 出现未声明的来源类 {unknown_classes}：只接受 {list(READ_CLASSES)}"
            )
        missing = sorted(set(SCRIPT_INPUT_READS) - set(self.reads))
        if missing:
            problems.append(f"① 读取集缺键 {missing}（每个读取输入均须声明来源类）")
        extra = sorted(set(self.reads) - set(SCRIPT_INPUT_READS))
        if extra:
            problems.append(
                f"① 出现未声明读取键 {extra}（禁止未声明直通：必须登记来源类，出现即拒绝）"
            )
        run_level = self.by_class("运行级")
        derived = frozenset(self.derived)
        if set(derived) != set(self.by_class("派生")):
            problems.append(
                f"派生集 {sorted(derived)} != reads 的派生类键集 {sorted(self.by_class('派生'))}"
                "（派生键须在 reads 里标为派生，且声明来源）"
            )
        renaming_values = frozenset(self.renames.values())
        # ⑤ 承接类键逐一有映射（同名承接为恒等映射；漏登即改名被错记为"丢弃 + 派生"）
        if set(self.renames) != set(self.by_class("承接")):
            problems.append(
                f"⑤ renames 键集 {sorted(self.renames)} != 承接类键集 "
                f"{sorted(self.by_class('承接'))}（承接类键逐一登记，不得漏登）"
            )
        # ② 守恒等式：下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生
        renamed = frozenset(
            key
            for key, source in self.renames.items()
            if source in TRANSFERABLE_FIELDS - self.dropped
        )
        expected = renamed | run_level | derived
        if set(self.reads) != set(expected):
            problems.append(
                f"② 守恒等式不成立：下游读取集 {sorted(self.reads)} != "
                f"renames(上游 − 丢弃) ∪ 运行级 ∪ 派生 = {sorted(expected)}"
            )
        # ③ 丢弃集：上界 + 与承接/运行级/派生互斥 + 可移交要点不得进丢弃集
        top, entry = _upstream_face()
        outside = sorted(self.dropped - (top | entry))
        if outside:
            problems.append(f"③ 丢弃集出现上游导出面之外的字段 {outside}（丢弃 ⊆ 上游）")
        overlap = sorted(self.dropped & (renaming_values | run_level | derived))
        if overlap:
            problems.append(f"③ 丢弃集与承接映射值集/运行级/派生相交 {overlap}（承接 ↔ 丢弃互斥）")
        essentials = sorted(self.dropped & TRANSFERABLE_FIELDS)
        if essentials:
            problems.append(
                f"③ 可移交要点不得进丢弃集 {essentials}"
                "（承接 ≠ 丢弃：改名承接登记在 renames，`genre` 不进丢弃集）"
            )
        # ④ 派生键不得占用上游 / 运行级 / 承接目标的键名
        collision = sorted(derived & (TRANSFERABLE_FIELDS | run_level | renaming_values))
        if collision:
            problems.append(
                f"④ 派生键占用上游要点/运行级/承接目标键名 {collision}（派生须声明来源）"
            )
        # ⑥ 取数依据登记完备，且与 reads/dropped 无交集
        if self.sources != SELECTION_SOURCES:
            problems.append(
                f"⑥ 取数依据登记不完整 {sorted(self.sources)} != {sorted(SELECTION_SOURCES)}"
            )
        mixed = sorted(self.sources & (set(self.reads) | self.dropped))
        if mixed:
            problems.append(
                f"⑥ 取数依据与 reads/dropped 混记 {mixed}（选条入口既不进 reads、也不进丢弃集）"
            )
        return problems

    def assert_consistent(self) -> None:
        """一致性断言：任一违反即拒绝（交接不得带缺陷往下走）。"""
        problems = self.violations()
        if problems:
            raise HandoffError(
                f"dev → 剧本 交接声明不一致（mode={self.mode}）：\n"
                + "\n".join(f"- {item}" for item in problems)
            )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "reads": {key: self.reads[key] for key in sorted(self.reads)},
            "renames": {key: self.renames[key] for key in sorted(self.renames)},
            "dropped": sorted(self.dropped),
            "derived": sorted(self.derived),
            "sources": sorted(self.sources),
        }


def dev_script_handoff_declaration(mode: str = HANDOFF_MODE_DEV) -> DevScriptHandoff:
    """本交接的**单一构建点**：逐键标类 + 改名承接 + 独立丢弃集 + 取数依据。

    `mode` 两值：有 `dev` ⇒ `dev_script_handoff`（承接被标记条目的可移交要点）；
    无 `dev`（既有短剧链）⇒ `run_level_pilot_inputs`（四键全运行级）。
    构建即做 C7 上游同步机检与 C6 ①~⑥ 断言（不合格即拒绝，不产带缺陷的声明）。
    """
    _assert_upstream_synced()
    if mode == HANDOFF_MODE_DEV:
        declaration = DevScriptHandoff(
            mode=mode,
            reads=dict(DEV_SCRIPT_READS),
            renames=dict(DEV_SCRIPT_RENAMES),
            dropped=DEV_SCRIPT_DROPPED,
            derived=frozenset(),
            sources=SELECTION_SOURCES,
        )
    elif mode == HANDOFF_MODE_RUN_LEVEL:
        declaration = DevScriptHandoff(
            mode=mode,
            reads=dict(RUN_LEVEL_READS),
            renames={},
            dropped=frozenset(),
            derived=frozenset(),
            sources=SELECTION_SOURCES,
        )
    else:
        raise HandoffError(
            f"交接 mode 取值非法 {mode!r}：只接受 {list(HANDOFF_MODES)}（FR-016 二值）"
        )
    declaration.assert_consistent()
    return declaration


def script_input_mode(upstream_stage_ids: Sequence[str]) -> str:
    """生效来源的**单一判定**（FR-016，禁止静默择一）：上游有 `dev` ⇒ 交接结果；否则回落运行级。"""
    return HANDOFF_MODE_DEV if "dev" in set(upstream_stage_ids) else HANDOFF_MODE_RUN_LEVEL


def scanned_read_keys(root: str | Path | None = None) -> tuple[str, ...]:
    """源码扫描式锁定读取集：`inputs[<常量>]` 与 `inputs.get(<常量>)` 两种写法逐点收集。

    读取集**不靠记忆**：实现里新增一个读取点而未登记来源类 ⇒ `read_set_mismatches` 点名该键。
    """
    base = Path(root) if root is not None else repo_root()
    keys: set[str] = set()
    for relative in READ_SCAN_FILES:
        path = base / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        keys |= _read_keys_of(tree)
    return tuple(sorted(keys))


def _read_keys_of(tree: ast.AST) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "inputs"
        ):
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id == "inputs"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


def read_set_mismatches(root: str | Path | None = None) -> list[str]:
    """读取集与声明的**双向绑定**机检：未声明读取键 / 声明了却没人读，任一向即红。"""
    scanned = set(scanned_read_keys(root))
    declared = set(SCRIPT_INPUT_READS)
    problems: list[str] = []
    if scanned - declared:
        problems.append(
            f"源码出现未声明读取键 {sorted(scanned - declared)}：交接声明必须逐键标类"
            "（禁止未声明直通，出现即拒绝）"
        )
    if declared - scanned:
        problems.append(
            f"声明了但源码未读取的键 {sorted(declared - scanned)}：读取集须与实现双向绑定"
        )
    return problems


@dataclass(frozen=True)
class ScriptInputView:
    """剧本阶段输入视图：生效来源（`mode`）+ 读取集 + 逐字段可追溯的取数依据。"""

    mode: str
    inputs: Mapping[str, Any]
    declaration: DevScriptHandoff
    trace: Mapping[str, Mapping[str, str]]
    selected_entry: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", dict(self.inputs))
        object.__setattr__(self, "trace", {key: dict(value) for key, value in self.trace.items()})

    def to_detail(self) -> dict:
        """运行记录 `StageState.detail` 的确定性视图（生效 `mode` 随机读可见，无墙钟/路径）。"""
        return {
            "input_source": self.mode,
            "input_reads": dict(self.declaration.reads),
            "input_trace": {key: dict(value) for key, value in self.trace.items()},
            "selected_entry": self.selected_entry,
        }


def dev_to_script_inputs(export: Mapping, pilot_inputs: Mapping) -> ScriptInputView:
    """有 `dev` 链：取**恰好一条**「本轮进入生产」标记指向条目的可移交要点。

    取数入口（FR-005）：标记**恰好一条**且指向组合内已存在条目方可交接；悬空 / 越界（数量
    越出"恰好一条"，即指向多条）/ 缺失 ⇒ **下游拒绝启动**并点名（不静默取第一条兜底、不伪装
    成"选题为空"）。017 的导出面**不因缺陷丢条目**，故拒绝发生在交接侧（显式拒绝，非静默降级）。
    被标记条目的三个可移交要点任一为空同样拒绝（**承接不解除要点校验**，且必须在承接之前判）。
    """
    declaration = dev_script_handoff_declaration(HANDOFF_MODE_DEV)
    entry = _marked_entry(export)
    direction_id = str(entry["direction_id"])
    _require_essentials(entry, direction_id)
    target_minutes = _run_level_target(pilot_inputs)
    inputs = {
        "topic": str(entry["genre"]),  # 改名承接：topic ← genre
        "constraints": [str(item) for item in entry["constraints"]],
        "characters": [str(item) for item in entry["characters"]],
        "target_duration_min": target_minutes,
    }
    trace = {
        "topic": {"class": "承接", "source": "genre", "entry": direction_id},
        "constraints": {"class": "承接", "source": "constraints", "entry": direction_id},
        "characters": {"class": "承接", "source": "characters", "entry": direction_id},
        "target_duration_min": {"class": "运行级", "source": "pilot_inputs.target_duration_min"},
    }
    return _view(HANDOFF_MODE_DEV, inputs, declaration, trace, direction_id)


def run_level_script_inputs(pilot_inputs: Mapping) -> ScriptInputView:
    """无 `dev` 链（既有短剧试水链）：四键全取运行级 `PilotInputs`（缺项即拒绝）。

    与 `dev_script_handoff` **并列的第二条来源**：生效哪一条由上游是否含 `dev` 判定
    （`script_input_mode`）并随机读可见——**禁止静默择一**。
    """
    declaration = dev_script_handoff_declaration(HANDOFF_MODE_RUN_LEVEL)
    topic = str(pilot_inputs.get("topic") or "")
    if not topic:
        raise HandoffError("运行级输入缺 topic（题材）：剧本阶段拒绝启动（不静默补默认）")
    inputs = {
        "topic": topic,
        "constraints": [str(item) for item in (pilot_inputs.get("constraints") or ())],
        "characters": [str(item) for item in (pilot_inputs.get("characters") or ())],
        "target_duration_min": _run_level_target(pilot_inputs),
    }
    trace = {key: {"class": "运行级", "source": f"pilot_inputs.{key}"} for key in sorted(inputs)}
    return _view(HANDOFF_MODE_RUN_LEVEL, inputs, declaration, trace)


def _view(mode, inputs, declaration, trace, selected_entry="") -> ScriptInputView:
    if set(inputs) != set(SCRIPT_INPUT_READS):
        raise HandoffError(
            f"剧本输入视图键集 {sorted(inputs)} != 声明读取集 {sorted(SCRIPT_INPUT_READS)}"
            "（每个读取输入都必须有来源类）"
        )
    if set(trace) != set(inputs):
        raise HandoffError(f"逐字段可追溯性不足：trace 键集 {sorted(trace)} != 输入键集")
    view = ScriptInputView(
        mode=mode,
        inputs=inputs,
        declaration=declaration,
        trace=trace,
        selected_entry=selected_entry,
    )
    if view.declaration.mode != mode:
        raise HandoffError(f"输入视图 mode {mode!r} 与声明 {view.declaration.mode!r} 不一致")
    return view


def _run_level_target(pilot_inputs: Mapping) -> Any:
    value = pilot_inputs.get("target_duration_min")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise HandoffError(
            f"运行级输入 target_duration_min 缺失或非正（{value!r}）：剧本阶段拒绝启动"
        )
    return value


def _marked_entry(export: Mapping) -> dict:
    """取数入口：组合内「本轮进入生产」标记**恰好一条**且指向组内条目（四类违规即拒绝点名）。

    **口径（功能 018 裁决后）**：交接侧要求"恰好一条"，两形态配置 `dev.production_marks` 已
    声明为 `{min: 1, max: 1}`（短剧原上界 2 与链"一轮产出一部影片"直接矛盾——策略依该区间
    标两条标记会让链在短剧形态拒绝启动，故收窄为同值）。标记 2 条时本条**如实拒绝启动**（不猜、
    不取第一条），该分支仍是交接侧独立的第二道防线；既有短剧试水链（无 `dev`）不受影响
    （回落运行级输入）。
    """
    if not isinstance(export, Mapping):
        raise HandoffError(f"上游交接输入必须为导出面映射，实际为 {export!r}")
    entries = [entry for entry in (export.get("entries") or ()) if isinstance(entry, Mapping)]
    if not entries:
        raise HandoffError("上游导出面无条目（选题为空）：下游拒绝启动（不静默产空剧本）")
    marks = list(export.get("production_marks") or ())
    if not marks:
        raise HandoffError(
            "取数入口缺失：组合内无「本轮进入生产」标记（不静默取第一条兜底、不伪装成选题为空）"
        )
    if len(marks) > 1:
        raise HandoffError(
            f"取数入口越界/多条：标记数量越出「恰好一条」——指向多条 {sorted(str(m) for m in marks)}"
            "（不猜、不静默取第一条）"
        )
    mark = marks[0]
    for entry in entries:
        if entry.get("direction_id") == mark:
            return dict(entry)
    known = sorted(str(entry.get("direction_id")) for entry in entries)
    raise HandoffError(f"取数入口悬空：标记 {mark!r} 指向组外条目（组内 {known}）——下游拒绝启动")


def _require_essentials(entry: Mapping, direction_id: str) -> None:
    """可移交要点校验（**承接之前**判）：任一为空即拒绝并点名（承接不解除要点校验）。"""
    missing = [field for field in ("genre", "constraints", "characters") if not entry.get(field)]
    if missing:
        raise HandoffError(
            f"被标记条目 {direction_id!r} 的可移交要点为空 {missing}：下游拒绝启动"
            "（与 017 的 rule.slate_structure 判 0 口径同源，此处为交接侧的第二道防线）"
        )


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
    "DEV_SCRIPT_DROPPED",
    "DEV_SCRIPT_READS",
    "DEV_SCRIPT_RENAMES",
    "DevScriptHandoff",
    "EditInputs",
    "FieldParity",
    "GEN_PARAMS_DERIVED",
    "GEN_PARAMS_DROPPED",
    "GEN_PARAMS_FIELDS",
    "HANDOFF_MODE_DEV",
    "HANDOFF_MODE_RUN_LEVEL",
    "HANDOFF_MODES",
    "HandoffError",
    "MATERIAL_FIELDS",
    "PromoMaterials",
    "READ_CLASSES",
    "READ_SCAN_FILES",
    "RUN_LEVEL_READS",
    "SEGMENT_LINE_DROPPED",
    "SEGMENT_LINE_FIELDS",
    "SELECTION_SOURCES",
    "SHOT_LIBRARY_FIELDS",
    "SCRIPT_INPUT_READS",
    "av_to_edit_inputs",
    "canonical_json",
    "edit_inputs_parity",
    "reel_to_promo_materials",
    "script_parity",
    "script_to_segment",
    "shotlist_parity",
    "shotlist_to_gen_params",
]
