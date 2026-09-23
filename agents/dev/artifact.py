"""立项组合工件 TopicSlate（功能 017 / 契约 C4~C6）：本轮立项决策的内容寻址载体。

单一产出（澄清第 8 条）：一轮产出**一份**立项组合工件——条目列表（本轮推荐立项的多个选题，
每条含方向标识、论证要点、条目级分量呈现、可移交下游的剧本输入要点）+ 组合级"本轮进入生产"
指向 + 模拟数据源标注；不拆组合与单选两套 schema。

内容寻址：`canonical_json()`（键排序，嵌套同口径）→ BLAKE3 即 `slate_hash()`——既是运营表
幂等键分量，也是回放精确匹配与审计的核对依据；工件本体按哈希入对象存储。

**构造期只拒绝"缺结构标记"，不代判缺陷**（规格边界："越界即门禁判 0，不由代码兜底"）：

- 拒绝：条目列表非空（空组合）、方向标识与论证要点非空、形状非法（`constraints`/`characters`
  必须是字符串列表、`eval_components` 必须是 mapping、`in_production` 必须是布尔）、
  数据来源标注缺失或标注与"模拟非真实"不符；
- 不代判：方向标识组合内唯一性、条目数区间、标记数量与指向（含**悬空标记**如实保留）——
  这些是 `rule.slate_structure` / `rule.slate_combination` 的判定对象（C5/C7/C8），
  故本模块不代判、不兜底；要点"字段在但取值为空"（`genre`/`constraints`/`characters`）
  同理由门禁点名判 0。

数据来源标注（SC-009）：`signal_sources` 随产物本体落盘，逐条含来源标识 + `simulated` 位 +
说明（含"非真实商业数据"），**不得**让模拟信号看起来像真实商业数据（真实票房/舆情接入属 G3）。
"""

import json
from dataclasses import dataclass, field, replace

import blake3

from core.tree.errors import ValidationError

# 立项组合 schema 版本（下游 G2 交接与导出取数的稳定标识，字段/枚举变更即升版本）
SCHEMA_VERSION = "1.0.0"
# 模拟数据源标识（数据来源标注的机读键；真实渠道接入属 G3，届时口径变更即升 schema）
SIMULATED_SOURCE_IDS = ("simulated.box_office_regression", "simulated.buzz_heat")
# 标注口径（SC-009）：模拟数据源必须自证"非真实商业数据"，不得让模拟信号看起来像真实数据
SIMULATED_NOTE = (
    "模拟数据源（simulated）：代理分量的驱动数据为确定性模拟数据源 + 夹具，"
    "非真实商业数据（真实票房库/舆情接口属 G3 渠道范围，受预算门禁与账单对账约束）。"
)

_MISSING = object()


def _require_nonempty_str(value, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} 必须为非空字符串，实际为 {value!r}")
    return value


def _require_bool(value, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} 必须为布尔，实际为 {value!r}")
    return value


def _require_key(data: dict, key: str) -> object:
    """取必填键：缺失即报错（缺结构化标记不允许静默降级）。"""
    value = data.get(key, _MISSING)
    if value is _MISSING:
        raise ValidationError(f"立项组合工件缺少结构化标记 {key!r}")
    return value


def _require_marker_list(value, key: str) -> list:
    """结构化标记必须为非空列表（空列表与缺失同罪：门禁与下游无从确定性解析）。"""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError(f"立项组合工件结构化标记 {key} 必须为非空列表，实际为 {value!r}")
    return list(value)


def _require_str_list(value, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{field_name} 必须为字符串列表，实际为 {value!r}")
    for item in value:
        _require_nonempty_str(item, f"{field_name}[]")
    return tuple(value)


def _canonical(value) -> str:
    """规范化 JSON（键排序）：内容寻址与参数摘要的确定性底座。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def simulated_signal_sources(signal_params: dict) -> tuple[dict, ...]:
    """模拟数据源标注（随产物落盘）：两条来源标识 + `simulated` 位 + 说明 + 参数摘要。

    参数摘要即行为口径印记（原则一：数据源参数变更 ⇒ 新评估器版本、旧节点不重算）；
    标注随产物本体（不是只落报告侧），使"分量来自模拟源"在产物层面可机读（SC-009）。
    """
    if not isinstance(signal_params, dict) or not signal_params:
        raise ValidationError(f"模拟数据源参数必须为非空 mapping，实际为 {signal_params!r}")
    digest = blake3.blake3(_canonical(signal_params).encode()).hexdigest()[:12]
    return tuple(
        {
            "source": source_id,
            "simulated": True,
            "note": SIMULATED_NOTE,
            "params_digest": digest,
        }
        for source_id in SIMULATED_SOURCE_IDS
    )


def _require_signal_sources(value) -> tuple[dict, ...]:
    """数据来源标记载荷校验：来源标识 + 模拟位 + 说明（声称真实即拒绝，SC-009）。

    本环节的数据源**只有**模拟源（真实票房/舆情接入属 G3）：`simulated` 必须为 True，
    且说明必须含"非真实商业数据"——任何指向真实商业数据来源的标注即拒绝，
    正是"不得让模拟信号看起来像真实商业数据"的机检落点。真实渠道接入时应升 schema 版本
    并同步标注口径（而非放宽本断言）。
    """
    sources = _require_marker_list(value, "signal_sources")
    normalized: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValidationError(f"数据来源标注必须为 mapping，实际为 {source!r}")
        source_id = _require_nonempty_str(source.get("source"), "signal_sources[].source")
        simulated = _require_bool(source.get("simulated"), "signal_sources[].simulated")
        note = _require_nonempty_str(source.get("note"), "signal_sources[].note")
        if simulated is not True:
            raise ValidationError(
                f"数据来源标注 {source_id!r} 的 simulated 必须为 True：本环节数据源只有"
                "确定性模拟源（真实票房/舆情接入属 G3，届时应升 schema 版本并同步标注口径）"
            )
        if "非真实商业数据" not in note:
            raise ValidationError(
                f"数据来源标注 {source_id!r} 的说明必须含'非真实商业数据'"
                f"（不得让模拟信号看起来像真实商业数据），实际为 {note!r}"
            )
        normalized.append({**source, "source": source_id, "simulated": simulated, "note": note})
    return tuple(normalized)


@dataclass(frozen=True)
class SlateEntry:
    """组合条目：一条推荐立项的选题（方向标识 + 论证要点 + 可移交要点 + 条目级分量）。

    `genre`/`constraints`/`characters` 是可移交下游的剧本输入要点（FR-011 的判据是三者非空）；
    **要点为空不构造期拒绝**——它由 `rule.slate_structure` 判 0 并点名缺失字段（C5），
    构造期只保证形状可确定性解析。`in_production` 是条目级标记位，与组合级指向同步
    （见 `TopicSlate.__post_init__`）。
    """

    direction_id: str
    rationale: str
    eval_components: dict = field(default_factory=dict)
    genre: str = ""
    constraints: tuple[str, ...] | list[str] = ()
    characters: tuple[str, ...] | list[str] = ()
    in_production: bool = False

    def __post_init__(self) -> None:
        _require_nonempty_str(self.direction_id, "direction_id")
        _require_nonempty_str(self.rationale, "rationale")
        if not isinstance(self.eval_components, dict):
            raise ValidationError(
                f"eval_components 必须为 mapping（条目级分量呈现），实际为 {self.eval_components!r}"
            )
        if not isinstance(self.genre, str):
            raise ValidationError(f"genre 必须为字符串，实际为 {self.genre!r}")
        object.__setattr__(self, "constraints", _require_str_list(self.constraints, "constraints"))
        object.__setattr__(self, "characters", _require_str_list(self.characters, "characters"))
        _require_bool(self.in_production, "in_production")
        object.__setattr__(self, "eval_components", dict(self.eval_components))

    @classmethod
    def from_dict(cls, data: dict) -> "SlateEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"组合条目必须为 dict，实际为 {data!r}")
        return cls(
            direction_id=data.get("direction_id"),
            rationale=data.get("rationale"),
            eval_components=data.get("eval_components", {}),
            genre=data.get("genre", ""),
            constraints=data.get("constraints", ()),
            characters=data.get("characters", ()),
            in_production=data.get("in_production", False),
        )

    def to_dict(self) -> dict:
        return {
            "direction_id": self.direction_id,
            "rationale": self.rationale,
            "eval_components": dict(self.eval_components),
            "genre": self.genre,
            "constraints": list(self.constraints),
            "characters": list(self.characters),
            "in_production": self.in_production,
        }

    def handoff_complete(self) -> bool:
        """可移交下游的剧本输入要点是否齐备（FR-011 判据：三字段非空）。"""
        return bool(self.genre and self.constraints and self.characters)


def _normalize_entry(item) -> SlateEntry:
    if isinstance(item, SlateEntry):
        return item
    if isinstance(item, dict):
        return SlateEntry.from_dict(item)
    raise ValidationError(f"组合条目必须为 dict 或 SlateEntry，实际为 {item!r}")


@dataclass(frozen=True)
class TopicSlate:
    """立项组合工件：本轮推荐立项的多个选题 + 组合级"进入生产"指向 + 数据来源标注。"""

    entries: tuple[SlateEntry | dict, ...] | list[SlateEntry | dict]
    signal_sources: tuple[dict, ...] | list[dict]
    production_marks: tuple[str, ...] | list[str] = ()
    schema_version: str = SCHEMA_VERSION

    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_nonempty_str(self.schema_version, "schema_version")
        entries = tuple(
            _normalize_entry(entry) for entry in _require_marker_list(self.entries, "entries")
        )
        sources = _require_signal_sources(self.signal_sources)
        marks = _require_str_list(self.production_marks, "production_marks")
        if not marks:  # 缺省由条目级标记位派生（两处呈现归一为单一事实源，永不漂移）
            marks = tuple(entry.direction_id for entry in entries if entry.in_production)
        entries = tuple(
            entry
            if entry.in_production == (entry.direction_id in marks)
            else replace(entry, in_production=entry.direction_id in marks)
            for entry in entries
        )
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "signal_sources", sources)
        object.__setattr__(self, "production_marks", marks)

    @classmethod
    def from_dict(cls, data: dict) -> "TopicSlate":
        if not isinstance(data, dict):
            raise ValidationError(f"立项组合工件必须为 dict，实际为 {data!r}")
        return cls(
            entries=_require_marker_list(_require_key(data, "entries"), "entries"),
            signal_sources=_require_marker_list(
                _require_key(data, "signal_sources"), "signal_sources"
            ),
            production_marks=data.get("production_marks", ()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "entries": [entry.to_dict() for entry in self.entries],
            "production_marks": list(self.production_marks),
            "signal_sources": [dict(source) for source in self.signal_sources],
        }

    def canonical_json(self) -> str:
        """规范化 JSON（键排序）：内容寻址与回放核对键（002/004/008 canonical 口径）。"""
        return _canonical(self.to_dict())

    def slate_hash(self) -> str:
        """规范化 BLAKE3（64 位小写 hex）：运营表幂等键分量 + 对象存储地址。"""
        return blake3.blake3(self.canonical_json().encode()).hexdigest()

    # --- 条目与方向 ---
    def direction_ids(self) -> tuple[str, ...]:
        return tuple(entry.direction_id for entry in self.entries)

    def entry(self, direction_id: str) -> SlateEntry:
        for entry in self.entries:
            if entry.direction_id == direction_id:
                return entry
        raise ValidationError(f"立项组合工件中不存在方向 {direction_id!r}")

    def marked_entries(self) -> tuple[SlateEntry, ...]:
        """指向已解析的标记条目（悬空标记不在其列——组合门禁点名违规，不代判）。"""
        return tuple(entry for entry in self.entries if entry.in_production)

    def produce_ids(self) -> tuple[str, ...]:
        """本轮进入生产的指向（策略产出原样：悬空项如实保留，合法性由组合门禁判定）。"""
        return tuple(self.production_marks)

    def source_ids(self) -> tuple[str, ...]:
        return tuple(str(source["source"]) for source in self.signal_sources)
