"""分镜脚本 ShotList 模型与执行前三层合法性校验（功能 008，research 决策 2）。

ShotList：shots 有序 + schema_version（FR-012 下游衔接契约：字段名/枚举值稳定，
视觉线生成参数建议与剪辑线候选镜头库按此消费）；规范化 JSON（键排序）即回放匹配键
（002 观测槽精确匹配依据），BLAKE3 哈希为运营表唯一键分量。

validate_shotlist 三层校验全部在执行前（违规 0 渲染 0 成本，FR-002 / C1 场景 2）：
① 承接的剧本行存在（covers 的每个 line_id 在剧本内）；
② 场景承接（每场景 ≥1 镜 + key=True 行逐条被 covers 承接——必覆盖清单，澄清 Q1；
   普通台词合并/拆分不违规；清单为空则本层只剩场景级要求，降级口径由 rule.coverage
   门禁在 diagnostics 注明）；
③ 景别/机位/运动档位在规则库枚举内（storyboard.shot_grammar，与 rule.shot_grammar
   门禁共用同一规则库——配置单一事实源）。
"""

import json
from dataclasses import dataclass

import blake3

from agents.storyboard.script import SIDES, ScriptSegment
from core.tree.errors import ValidationError

# ShotList schema 版本（FR-012：下游契约稳定标识，字段/枚举变更即升版本）
SCHEMA_VERSION = "1.0.0"


def _require_nonempty_str(value, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} 必须为非空字符串，实际为 {value!r}")
    return value


def _require_positive_int(value, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError(f"{field_name} 必须为正整数，实际为 {value!r}")
    return value


@dataclass(frozen=True)
class ShotEntry:
    """分镜镜头：景别/机位（含侧别）/运动档位 + 所属场景与承接剧本行 + 备选数。

    alternatives 为策略参数（下游视觉线按备选数生成并行方案，本特性只保证 schema），
    必须存在且为 ≥1 整数——缺字段/越界在构造即拒绝（不允许静默零备选）。
    """

    shot_id: str
    scene_id: str
    covers: tuple[str, ...] | list[str]
    shot_size: str
    camera: str
    side: str
    movement: str
    est_duration_ms: int
    alternatives: int

    def __post_init__(self) -> None:
        _require_nonempty_str(self.shot_id, "shot_id")
        _require_nonempty_str(self.scene_id, "scene_id")
        _require_nonempty_str(self.shot_size, "shot_size")
        _require_nonempty_str(self.camera, "camera")
        _require_nonempty_str(self.movement, "movement")
        if self.side not in SIDES:
            raise ValidationError(f"side 必须 ∈ {list(SIDES)}，实际为 {self.side!r}")
        _require_positive_int(self.est_duration_ms, "est_duration_ms")
        _require_positive_int(self.alternatives, "alternatives 备选数")
        if not isinstance(self.covers, (list, tuple)) or not self.covers:
            raise ValidationError(f"covers 必须为非空行 id 列表，实际为 {self.covers!r}")
        for line_id in self.covers:
            _require_nonempty_str(line_id, "covers[] 行 id")
        object.__setattr__(self, "covers", tuple(self.covers))

    @classmethod
    def from_dict(cls, data: dict) -> "ShotEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"shot 必须为 dict，实际为 {data!r}")
        return cls(
            shot_id=data.get("shot_id"),
            scene_id=data.get("scene_id"),
            covers=data.get("covers", ()),
            shot_size=data.get("shot_size"),
            camera=data.get("camera"),
            side=data.get("side"),
            movement=data.get("movement"),
            est_duration_ms=data.get("est_duration_ms"),
            alternatives=data.get("alternatives"),
        )

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "covers": list(self.covers),
            "shot_size": self.shot_size,
            "camera": self.camera,
            "side": self.side,
            "movement": self.movement,
            "est_duration_ms": self.est_duration_ms,
            "alternatives": self.alternatives,
        }


def _normalize_shot(item: ShotEntry | dict) -> ShotEntry:
    if isinstance(item, ShotEntry):
        return item
    return ShotEntry.from_dict(item)


@dataclass(frozen=True)
class ShotList:
    """分镜脚本：shots 有序 + schema_version；构造做形状校验，语义校验走 validate_shotlist。"""

    shots: tuple[ShotEntry | dict, ...] | list[ShotEntry | dict]
    schema_version: str = SCHEMA_VERSION

    SCHEMA_VERSION = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_nonempty_str(self.schema_version, "schema_version")
        shots = tuple(_normalize_shot(shot) for shot in self.shots)
        if not shots:
            raise ValidationError("ShotList shots 不能为空")
        shot_ids = [shot.shot_id for shot in shots]
        repeated = sorted({sid for sid in shot_ids if shot_ids.count(sid) > 1})
        if repeated:
            raise ValidationError(f"shot_id 必须唯一，重复：{repeated}")
        object.__setattr__(self, "shots", shots)

    @classmethod
    def from_dict(cls, data: dict) -> "ShotList":
        if not isinstance(data, dict):
            raise ValidationError(f"ShotList 必须为 dict，实际为 {data!r}")
        return cls(
            shots=data.get("shots", ()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "shots": [shot.to_dict() for shot in self.shots],
        }

    def canonical_json(self) -> str:
        """规范化 JSON（键排序）：回放精确匹配键（同 004/006/007 canonical 口径）。"""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def shotlist_hash(self) -> str:
        """规范化 BLAKE3（运营表唯一键 (round_id, shotlist_hash) 分量）。"""
        return blake3.blake3(self.canonical_json().encode()).hexdigest()

    def shot(self, shot_id: str) -> ShotEntry:
        for shot in self.shots:
            if shot.shot_id == shot_id:
                return shot
        raise ValidationError(f"ShotList 中不存在镜头 {shot_id!r}")

    def shot_ids(self) -> tuple[str, ...]:
        return tuple(shot.shot_id for shot in self.shots)

    def scene_ids(self) -> tuple[str, ...]:
        """镜头所属场景序列（保持出现顺序，去重）。"""
        seen: list[str] = []
        for shot in self.shots:
            if shot.scene_id not in seen:
                seen.append(shot.scene_id)
        return tuple(seen)

    def shots_of_scene(self, scene_id: str) -> tuple[str, ...]:
        return tuple(shot.shot_id for shot in self.shots if shot.scene_id == scene_id)

    def covered_line_ids(self) -> tuple[str, ...]:
        return tuple(line_id for shot in self.shots for line_id in shot.covers)

    def total_est_duration_ms(self) -> int:
        return sum(shot.est_duration_ms for shot in self.shots)


def _require_rules(grammar_rules: dict) -> dict:
    """规则库读取：缺规则库即报错（不允许静默放过门禁，C3 / 原则五）。"""
    if not isinstance(grammar_rules, dict):
        raise ValidationError(f"grammar_rules 必须为 dict，实际为 {grammar_rules!r}")
    for key in ("shot_sizes", "camera_positions", "movements"):
        allowed = grammar_rules.get(key)
        if not isinstance(allowed, (list, tuple)) or not allowed:
            raise ValidationError(f"规则库 {key} 必须为非空列表（镜头语法规则库缺失）")
    return grammar_rules


def validate_shotlist(shotlist: ShotList, script: ScriptSegment, grammar_rules: dict) -> None:
    """执行前三层合法性校验：违规即 ValidationError（0 渲染 0 成本，C1）。

    grammar_rules 为 storyboard.shot_grammar 段（镜头语法规则库），与 rule.shot_grammar
    门禁共用——执行前校验 = 门禁的提前计算。
    """
    if not isinstance(shotlist, ShotList):
        raise ValidationError(f"shotlist 必须为 ShotList，实际为 {shotlist!r}")
    if not isinstance(script, ScriptSegment):
        raise ValidationError(f"script 必须为 ScriptSegment，实际为 {script!r}")
    rules = _require_rules(grammar_rules)

    # 第①层：承接的剧本行存在（covers 的每个 line_id 在剧本内）
    known_lines = set(script.line_ids())
    for shot in shotlist.shots:
        for line_id in shot.covers:
            if line_id not in known_lines:
                raise ValidationError(
                    f"镜头 {shot.shot_id!r} 承接不存在的剧本行 {line_id!r}（第①层：引用存在）"
                )

    # 第②层：场景承接（每场景 ≥1 镜 + key 行逐条被 covers 承接）
    known_scenes = set(script.scene_ids())
    for shot in shotlist.shots:
        if shot.scene_id not in known_scenes:
            raise ValidationError(
                f"镜头 {shot.shot_id!r} 引用不存在场景 {shot.scene_id!r}（第②层：场景承接）"
            )
    covered = set(shotlist.covered_line_ids())
    for scene in script.scenes:
        if not shotlist.shots_of_scene(scene.scene_id):
            raise ValidationError(f"场景 {scene.scene_id!r} 无任何镜头承接（第②层：场景承接）")
        missing = [line_id for line_id in scene.key_line_ids() if line_id not in covered]
        if missing:
            raise ValidationError(
                f"场景 {scene.scene_id!r} 关键行未承接：{missing}（第②层：必覆盖清单）"
            )

    # 第③层：景别/机位/运动档位在规则库枚举内
    for shot in shotlist.shots:
        if shot.shot_size not in rules["shot_sizes"]:
            raise ValidationError(
                f"镜头 {shot.shot_id!r} 景别档位 {shot.shot_size!r} 不在规则库 "
                f"{list(rules['shot_sizes'])}（第③层：档位枚举）"
            )
        if shot.camera not in rules["camera_positions"]:
            raise ValidationError(
                f"镜头 {shot.shot_id!r} 机位档位 {shot.camera!r} 不在规则库 "
                f"{list(rules['camera_positions'])}（第③层：档位枚举）"
            )
        if shot.movement not in rules["movements"]:
            raise ValidationError(
                f"镜头 {shot.shot_id!r} 运动档位 {shot.movement!r} 不在规则库 "
                f"{list(rules['movements'])}（第③层：档位枚举）"
            )
