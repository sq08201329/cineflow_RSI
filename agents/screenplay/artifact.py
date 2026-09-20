"""剧本工件 ScriptArtifact（功能 009 / research 决策 3）：三段产出 + 结构化标记。

三段产出（`stage` ∈ outline|scenes|script）是分阶段落树与独立评估的粒度；三段共用
同一结构化标记——`beats` 节拍清单、`scenes` 场景头（`heading = "{内景|外景} - {地点}
- {时间描述}"`，地点段与 `location` 字段机检一致）、`characters` 角色表含 `aliases`、
`lines` 行（归属场景 + 归属角色 + 关键行标注 + 情绪）。**缺任一标记即拒绝**：评估器
必须确定性（原则一），"读懂自由文本"不可复现，结构化标记是前提（缺标记宁拒绝不静默）。

内容寻址：`canonical_json()`（键排序，嵌套同口径）→ BLAKE3 即 `artifact_hash()`——
既是运营表的幂等键分量，也是回放精确匹配键（002 同口径）；工件本体按哈希入对象存储。

`lines[].scene_id` 必须指向既有场景（否则导出 008 ScriptSegment 时会静默丢行）；
地点/角色一致性与时间线单调性等**语义缺陷不做构造期拒绝**——它们是门禁与代理的
判定对象（rule.scene_character / proxy.entity_consistency / proxy.timeline_conflict），
构造期只保证"可确定性解析"。
"""

import json
from dataclasses import dataclass

import blake3

from core.tree.errors import ValidationError

# schema 版本（导出 008 ScriptSegment 的对接契约稳定标识，字段/枚举变更即升版本）
SCHEMA_VERSION = "1.0.0"
# 三段产出（分阶段落树粒度；judge.dramatic_tension 仅 outline 阶段适用）
STAGES = ("outline", "scenes", "script")
# 行类型与侧别枚举：与 008 ScriptSegment.LINE_KINDS / SIDES 同域（快照断言双向锁定）
LINE_KINDS = ("dialogue", "action")
SIDES = ("A", "B")
# 场景头形态（地点一致性机检依赖：第 2 段即场景地点）
INTERIOR = "内景"
EXTERIOR = "外景"
SCENE_PREFIXES = (INTERIOR, EXTERIOR)
HEADING_PARTS = 3
_HEADING_SEPARATOR = " - "

_MISSING = object()


def _require_nonempty_str(value, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} 必须为非空字符串，实际为 {value!r}")
    return value


def _require_bool(value, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} 必须为布尔，实际为 {value!r}")
    return value


def _require_nonneg_int(value, field_name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValidationError(f"{field_name} 必须为 ≥{minimum} 的整数，实际为 {value!r}")
    return value


def _require_key(data: dict, key: str) -> object:
    """取必填键：缺失即报错（缺结构化标记不允许静默降级）。"""
    value = data.get(key, _MISSING)
    if value is _MISSING:
        raise ValidationError(f"剧本工件缺少结构化标记 {key!r}")
    return value


def _require_marker_list(value, key: str) -> list:
    """结构化标记必须为非空列表（空列表与缺失同罪：无从确定性解析）。"""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError(f"剧本工件结构化标记 {key} 必须为非空列表，实际为 {value!r}")
    return list(value)


def heading_parts(heading: str) -> tuple[str, str, str]:
    """场景头解析：`"{内景|外景} - {地点} - {时间描述}"` → (前缀, 地点, 时间描述)。

    地点一致性机检（rule.scene_character）与摘要函数共用本函数——禁止第二套解析。
    """
    _require_nonempty_str(heading, "场景 heading")
    parts = tuple(heading.split(_HEADING_SEPARATOR))
    if len(parts) != HEADING_PARTS or not all(parts):
        raise ValidationError(
            f"场景 heading 必须为 '{INTERIOR}|{EXTERIOR} - 地点 - 时间描述' "
            f"{HEADING_PARTS} 段式，实际为 {heading!r}"
        )
    if parts[0] not in SCENE_PREFIXES:
        raise ValidationError(
            f"场景 heading 首段必须 ∈ {list(SCENE_PREFIXES)}，实际为 {parts[0]!r}"
            f"（heading={heading!r}）"
        )
    return parts  # type: ignore[return-value]


@dataclass(frozen=True)
class BeatEntry:
    """节拍清单条目：act 归属（三幕/序列）+ required（关键节拍存在性判定依据）。"""

    beat_id: str
    act: str
    description: str
    required: bool = True

    def __post_init__(self) -> None:
        _require_nonempty_str(self.beat_id, "beat_id")
        _require_nonempty_str(self.act, "beat.act")
        _require_nonempty_str(self.description, "beat.description")
        _require_bool(self.required, "beat.required")

    @classmethod
    def from_dict(cls, data: dict) -> "BeatEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"beat 必须为 dict，实际为 {data!r}")
        return cls(
            beat_id=data.get("beat_id"),
            act=data.get("act"),
            description=data.get("description"),
            required=data.get("required", True),
        )

    def to_dict(self) -> dict:
        return {
            "beat_id": self.beat_id,
            "act": self.act,
            "required": self.required,
            "description": self.description,
        }


@dataclass(frozen=True)
class CharacterEntry:
    """角色表条目：name（规范写法）+ aliases（别名，可为空但不得多义）。"""

    name: str
    aliases: tuple[str, ...] | list[str] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "character.name")
        if not isinstance(self.aliases, (list, tuple)):
            raise ValidationError(f"character.aliases 必须为列表，实际为 {self.aliases!r}")
        for alias in self.aliases:
            _require_nonempty_str(alias, "character.aliases[]")
        object.__setattr__(self, "aliases", tuple(self.aliases))

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"character 必须为 dict，实际为 {data!r}")
        return cls(name=data.get("name"), aliases=data.get("aliases", ()))

    def to_dict(self) -> dict:
        return {"name": self.name, "aliases": list(self.aliases)}


@dataclass(frozen=True)
class SceneMarker:
    """场景头标记：场景头文本 + 地点字段 + 剧内时间戳 + 出场角色 + 轴向基准。

    `location`/`time_marker` 是机检字段（地点一致性与时间线单调性），`heading` 是
    人可读场景头；`axis_base` 与 008 ScriptScene.axis_base 同域（导出直通，不臆造）。
    """

    scene_id: str
    heading: str
    location: str
    time_marker: int
    characters: tuple[str, ...] | list[str]
    axis_base: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.scene_id, "scene_id")
        heading_parts(self.heading)
        _require_nonempty_str(self.location, "scene.location")
        _require_nonneg_int(self.time_marker, "scene.time_marker")
        if not isinstance(self.characters, (list, tuple)):
            raise ValidationError(f"scene.characters 必须为列表，实际为 {self.characters!r}")
        for name in self.characters:
            _require_nonempty_str(name, "scene.characters[]")
        if self.axis_base is not None and self.axis_base not in SIDES:
            raise ValidationError(
                f"scene.axis_base 必须 ∈ {list(SIDES)} 或缺失，实际为 {self.axis_base!r}"
            )
        object.__setattr__(self, "characters", tuple(self.characters))

    @classmethod
    def from_dict(cls, data: dict) -> "SceneMarker":
        if not isinstance(data, dict):
            raise ValidationError(f"scene 必须为 dict，实际为 {data!r}")
        return cls(
            scene_id=data.get("scene_id"),
            heading=data.get("heading"),
            location=data.get("location"),
            time_marker=data.get("time_marker"),
            characters=data.get("characters", ()),
            axis_base=data.get("axis_base"),
        )

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "heading": self.heading,
            "location": self.location,
            "time_marker": self.time_marker,
            "characters": list(self.characters),
            "axis_base": self.axis_base,
        }


@dataclass(frozen=True)
class LineEntry:
    """剧本行标记：归属场景 + 归属角色（可缺失）+ 关键行标注 + 情绪（可缺失）。

    归属角色缺失的**对白行**由 `proxy.entity_consistency` 记为"指代歧义"诊断
    （不构造期拒绝）；动作行可无主体（群体动作）。
    """

    line_id: str
    scene_id: str
    kind: str
    text: str
    character: str | None = None
    key: bool = False
    emotion: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.line_id, "line_id")
        _require_nonempty_str(self.scene_id, "line.scene_id")
        if self.kind not in LINE_KINDS:
            raise ValidationError(f"line.kind 必须 ∈ {list(LINE_KINDS)}，实际为 {self.kind!r}")
        _require_nonempty_str(self.text, "line.text")
        if self.character is not None:
            _require_nonempty_str(self.character, "line.character")
        _require_bool(self.key, "line.key")
        if self.emotion is not None:
            _require_nonempty_str(self.emotion, "line.emotion")

    @classmethod
    def from_dict(cls, data: dict) -> "LineEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"line 必须为 dict，实际为 {data!r}")
        return cls(
            line_id=data.get("line_id"),
            scene_id=data.get("scene_id"),
            kind=data.get("kind"),
            text=data.get("text"),
            character=data.get("character"),
            key=data.get("key", False),
            emotion=data.get("emotion"),
        )

    def to_dict(self) -> dict:
        return {
            "line_id": self.line_id,
            "scene_id": self.scene_id,
            "kind": self.kind,
            "text": self.text,
            "character": self.character,
            "key": self.key,
            "emotion": self.emotion,
        }


def _normalize(item, cls):
    if isinstance(item, cls):
        return item
    if isinstance(item, dict):
        return cls.from_dict(item)
    raise ValidationError(f"{cls.__name__} 必须为 dict 或 {cls.__name__}，实际为 {item!r}")


def _require_unique(values: list[str], field_name: str) -> None:
    repeated = sorted({value for value in values if values.count(value) > 1})
    if repeated:
        raise ValidationError(f"{field_name} 必须唯一，重复：{repeated}")


@dataclass(frozen=True)
class ScriptArtifact:
    """剧本工件：三段产出的统一载体（结构化标记齐全即内容寻址工件）。"""

    stage: str
    text: str
    beats: tuple[BeatEntry | dict, ...] | list[BeatEntry | dict]
    scenes: tuple[SceneMarker | dict, ...] | list[SceneMarker | dict]
    characters: tuple[CharacterEntry | dict, ...] | list[CharacterEntry | dict]
    lines: tuple[LineEntry | dict, ...] | list[LineEntry | dict]
    schema_version: str = SCHEMA_VERSION

    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValidationError(f"stage 必须 ∈ {list(STAGES)}，实际为 {self.stage!r}")
        _require_nonempty_str(self.text, "text")
        _require_nonempty_str(self.schema_version, "schema_version")

        beats = tuple(_normalize(beat, BeatEntry) for beat in self.beats)
        scenes = tuple(_normalize(scene, SceneMarker) for scene in self.scenes)
        characters = tuple(_normalize(character, CharacterEntry) for character in self.characters)
        lines = tuple(_normalize(line, LineEntry) for line in self.lines)
        _require_unique([beat.beat_id for beat in beats], "beat_id")
        _require_unique([scene.scene_id for scene in scenes], "scene_id")
        _require_unique([line.line_id for line in lines], "line_id")
        _require_unique([character.name for character in characters], "character.name")
        # 别名表禁止多义：同一写法只能归属一个角色（实体一致性的规范化前提）
        seen: dict[str, str] = {}
        for character in characters:
            for spelling in (character.name, *character.aliases):
                if spelling in seen and seen[spelling] != character.name:
                    raise ValidationError(
                        f"角色名/别名 {spelling!r} 同时归属 {seen[spelling]!r} 与 "
                        f"{character.name!r}（同一写法不得多义）"
                    )
                seen[spelling] = character.name
        # 行必须归属既有场景（否则导出 008 ScriptSegment 时会静默丢行）
        known_scenes = {scene.scene_id for scene in scenes}
        for line in lines:
            if line.scene_id not in known_scenes:
                raise ValidationError(
                    f"行 {line.line_id!r} 引用不存在场景 {line.scene_id!r}"
                    "（结构化标记完整性：行须归属既有场景）"
                )
        object.__setattr__(self, "beats", beats)
        object.__setattr__(self, "scenes", scenes)
        object.__setattr__(self, "characters", characters)
        object.__setattr__(self, "lines", lines)

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptArtifact":
        if not isinstance(data, dict):
            raise ValidationError(f"剧本工件必须为 dict，实际为 {data!r}")
        return cls(
            stage=data.get("stage"),
            text=data.get("text"),
            beats=_require_marker_list(_require_key(data, "beats"), "beats"),
            scenes=_require_marker_list(_require_key(data, "scenes"), "scenes"),
            characters=_require_marker_list(_require_key(data, "characters"), "characters"),
            lines=_require_marker_list(_require_key(data, "lines"), "lines"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "text": self.text,
            "beats": [beat.to_dict() for beat in self.beats],
            "scenes": [scene.to_dict() for scene in self.scenes],
            "characters": [character.to_dict() for character in self.characters],
            "lines": [line.to_dict() for line in self.lines],
        }

    def canonical_json(self) -> str:
        """规范化 JSON（键排序）：内容寻址与回放精确匹配键（002/004/008 canonical 口径）。"""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def artifact_hash(self) -> str:
        """规范化 BLAKE3（64 位小写 hex）：运营表幂等键分量 + 内容寻址地址。"""
        return blake3.blake3(self.canonical_json().encode()).hexdigest()

    # --- 节拍 ---
    def beat_ids(self) -> tuple[str, ...]:
        return tuple(beat.beat_id for beat in self.beats)

    def required_beat_ids(self) -> tuple[str, ...]:
        """关键节拍清单（rule.beat_structure 的存在性判定依据）。"""
        return tuple(beat.beat_id for beat in self.beats if beat.required)

    # --- 场景与行 ---
    def scene_ids(self) -> tuple[str, ...]:
        return tuple(scene.scene_id for scene in self.scenes)

    def scene(self, scene_id: str) -> SceneMarker:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        raise ValidationError(f"剧本工件中不存在场景 {scene_id!r}")

    def lines_of_scene(self, scene_id: str) -> tuple[LineEntry, ...]:
        return tuple(line for line in self.lines if line.scene_id == scene_id)

    def line_ids(self) -> tuple[str, ...]:
        return tuple(line.line_id for line in self.lines)

    def line(self, line_id: str) -> LineEntry:
        for line in self.lines:
            if line.line_id == line_id:
                return line
        raise ValidationError(f"剧本工件中不存在行 {line_id!r}")

    def key_line_ids(self) -> tuple[str, ...]:
        """关键行清单（导出 008 后即必覆盖清单，须逐条被镜头承接）。"""
        return tuple(line.line_id for line in self.lines if line.key)

    # --- 角色 ---
    def character_names(self) -> tuple[str, ...]:
        return tuple(character.name for character in self.characters)

    def aliases_of(self, name: str) -> tuple[str, ...]:
        for character in self.characters:
            if character.name == name:
                return character.aliases
        raise ValidationError(f"剧本工件中不存在角色 {name!r}")

    def registered_names(self) -> frozenset[str]:
        """登记写法集合（规范名 ∪ 别名）——实体一致性的规范化依据。"""
        return frozenset(
            spelling
            for character in self.characters
            for spelling in (character.name, *character.aliases)
        )

    def character_references(self) -> tuple[str, ...]:
        """角色指称清单（行归属 + 场景出场，按出现顺序去重；代理评估器扫描面）。"""
        seen: list[str] = []
        for line in self.lines:
            if line.character is not None and line.character not in seen:
                seen.append(line.character)
        for scene in self.scenes:
            for name in scene.characters:
                if name not in seen:
                    seen.append(name)
        return tuple(seen)

    # --- 统计 ---
    def total_lines(self) -> int:
        return len(self.lines)

    def count_kind(self, kind: str) -> int:
        if kind not in LINE_KINDS:
            raise ValidationError(f"kind 必须 ∈ {list(LINE_KINDS)}，实际为 {kind!r}")
        return sum(1 for line in self.lines if line.kind == kind)

    def page_count(self, lines_per_page: int) -> float:
        """页数换算：总行数 ÷ lines_per_page（rule.page_minutes 对照面）。"""
        _require_nonneg_int(lines_per_page, "lines_per_page", minimum=1)
        return self.total_lines() / lines_per_page
