"""跨项目池化领域模型（功能 011，data-model.md §领域模型）。

- 全部 frozen dataclass，构造即校验（core/replay/errors.ValidationError）；校验风格与
  core/tree/models.py、core/calibration/models.py 一致（非空字符串/整数域/占比域/哈希格式）；
- `PoolingConfig`：configs/*.yaml 的 replay.pooling 段解析（前置树数/稀释阈值/跨形态开关/
  做梦开关/快照根目录）——缺段或缺语义项即拒绝，不静默取默认值（原则五）；
- 池化是读路径扩展（无新 DB 表）：模型是构建快照与统计报告的机读形态（JSON 可序列化）。
"""

import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import yaml

from core.replay.errors import ValidationError

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# 快照落盘根目录的配置缺省值（路径而非池化语义，可被部署配置覆盖）
DEFAULT_POOLS_DIR = "replay/pools"


class PoolingConfigError(ValidationError):
    """replay.pooling 段缺失/非法（配置即形态：不静默取默认值）。"""


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} 必须为非空字符串，实际为 {value!r}")


def _require_int(name: str, value: int, *, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValidationError(f"{name} 必须为 ≥ {minimum} 的整数，实际为 {value!r}")


def _require_number(name: str, value: float, *, minimum: float = 0.0) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum:
        raise ValidationError(f"{name} 必须为 ≥ {minimum} 的数值，实际为 {value!r}")


def _require_ratio(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValidationError(f"{name} 必须 ∈ [0,1]，实际为 {value!r}")


def _require_bool(name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise ValidationError(f"{name} 必须为 bool，实际为 {value!r}")


def _require_hash(name: str, value: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.match(value):
        raise ValidationError(f"{name} 必须为 64 位小写十六进制（BLAKE3），实际为 {value!r}")


def _require_optional_hash(name: str, value: str) -> None:
    if value:
        _require_hash(name, value)


def _as_tuple(name: str, value, expected: type | tuple[type, ...]) -> tuple:
    """归一为元组（frozen 模型内部一律元组）：非序列即拒绝，元素类型不符即拒绝。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValidationError(f"{name} 必须为元组或列表，实际为 {value!r}")
    for item in value:
        if not isinstance(item, expected):
            raise ValidationError(f"{name} 的元素必须为 {expected}，实际为 {item!r}")
    return tuple(value)


@dataclass(frozen=True)
class PoolingConfig:
    """replay.pooling 段配置（前置树数/稀释阈值/跨形态开关/做梦开关/快照根目录）。

    - `min_trees`：前置条件（同 Agent 同形态树 ≥ N 棵，不足即拒绝，FR-002）；
    - `dilution_hit_ratio_threshold`：稀释告警判定口径 = 命中占比超阈（澄清 Q2）；
    - `allow_cross_form`：跨形态合并需显式开启（缺省关闭，FR-007）；
    - `enabled_for_dreaming`：做梦层用合并池需显式开启（缺省关闭，FR-011）；
    - `pools_dir`：快照落盘根目录（路径非语义，配置缺省时用 DEFAULT_POOLS_DIR）。
    """

    min_trees: int = 3
    dilution_hit_ratio_threshold: float = 0.7
    allow_cross_form: bool = False
    enabled_for_dreaming: bool = False
    pools_dir: str = DEFAULT_POOLS_DIR

    def __post_init__(self) -> None:
        try:
            _require_int("min_trees", self.min_trees, minimum=1)
            _require_ratio("dilution_hit_ratio_threshold", self.dilution_hit_ratio_threshold)
            _require_bool("allow_cross_form", self.allow_cross_form)
            _require_bool("enabled_for_dreaming", self.enabled_for_dreaming)
            _require_non_empty("pools_dir", self.pools_dir)
        except ValidationError as exc:
            # 配置项非法统一为 PoolingConfigError（仍是 ValidationError 语义）
            raise PoolingConfigError(f"replay.pooling 非法：{exc}") from exc

    @classmethod
    def from_config(cls, config: dict) -> "PoolingConfig":
        """从形态配置（configs/*.yaml 全量映射）读 replay.pooling 段。

        四个语义项必须显式给出（缺项即拒绝——不静默回落到"可以混池"的默认档）；
        pools_dir 为落盘路径，缺省即 DEFAULT_POOLS_DIR。
        """
        if not isinstance(config, dict) or not isinstance(config.get("replay"), dict):
            raise PoolingConfigError("形态配置缺少 replay 段（映射）")
        section = config["replay"].get("pooling")
        if not isinstance(section, dict):
            raise PoolingConfigError("形态配置缺少 replay.pooling 段（映射）")

        def req(key):
            if key not in section:
                raise PoolingConfigError(f"replay.pooling 缺少配置项 {key!r}")
            return section[key]

        try:
            return cls(
                min_trees=req("min_trees"),
                dilution_hit_ratio_threshold=req("dilution_hit_ratio_threshold"),
                allow_cross_form=req("allow_cross_form"),
                enabled_for_dreaming=req("enabled_for_dreaming"),
                pools_dir=section.get("pools_dir", DEFAULT_POOLS_DIR),
            )
        except ValidationError as exc:
            raise PoolingConfigError(f"replay.pooling 非法：{exc}") from exc

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PoolingConfig":
        return cls.from_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class PoolTreeRef:
    """池内单棵树的引用：项目归属 + 时间戳 + 节点数 + 同版本组内的 config 附注。

    created_at = 树根的 created_at（树创建时刻）；config_note 记"同版本集但
    config_snapshot 其余键与组内基准不同"的如实标注（不影响分组）。
    """

    tree_id: str
    project_id: str
    created_at: float
    node_count: int
    config_note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("tree_id", self.tree_id)
        _require_non_empty("project_id", self.project_id)
        _require_number("created_at", self.created_at)
        _require_int("node_count", self.node_count, minimum=1)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VersionGroup:
    """版本分组：同评估器版本集的树集合（跨版本不混池的载体，原则一）。

    trees 非空——版本组是"同语义最小单位"，空组无意义；
    evaluator_versions 为附注（{评估器 ID: 版本}，快照可读性），分组语义只看 hash。
    """

    evaluator_versions_hash: str
    trees: tuple = ()
    evaluator_versions: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_hash("evaluator_versions_hash", self.evaluator_versions_hash)
        object.__setattr__(self, "trees", _as_tuple("trees", self.trees, PoolTreeRef))
        if not self.trees:
            raise ValidationError("trees 必须为非空树清单（版本组不得为空）")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MergedPool:
    """合并池：按 (Agent, 形态) 分组的跨项目池 + 版本分组 + 前置条件判定。

    pool_id = 分组输入的确定性哈希（同输入即同 id——幂等落盘的前提）；
    tree_count 必须等于全部版本组的树清单合计（分组与清单双记账一致，不得虚报）；
    conditions_met = 前置条件（树数 ≥ min_trees）判定结论。
    """

    pool_id: str
    agent_id: str
    form: str
    version_groups: tuple = ()
    min_trees: int = 3
    conditions_met: bool = True
    tree_count: int = 0
    build_snapshot: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        _require_hash("pool_id", self.pool_id)
        _require_non_empty("agent_id", self.agent_id)
        _require_non_empty("form", self.form)
        object.__setattr__(
            self, "version_groups", _as_tuple("version_groups", self.version_groups, VersionGroup)
        )
        if not self.version_groups:
            raise ValidationError("version_groups 必须为非空版本分组清单")
        _require_int("min_trees", self.min_trees, minimum=1)
        _require_bool("conditions_met", self.conditions_met)
        _require_int("tree_count", self.tree_count)
        if self.tree_count != sum(len(group.trees) for group in self.version_groups):
            raise ValidationError(
                f"tree_count 必须等于树清单合计 {sum(len(g.trees) for g in self.version_groups)}，"
                f"实际为 {self.tree_count}"
            )

    @property
    def trees(self) -> tuple:
        """全部版本组的树引用（按版本组顺序展开）。"""
        return tuple(ref for group in self.version_groups for ref in group.trees)

    def with_build_snapshot(self, path: str | Path) -> "MergedPool":
        """回填快照落盘路径（只增不改：产出新实例，原池不变）。"""
        return replace(self, build_snapshot=str(path))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConflictHit:
    """冲突命中条目：一棵树的得分与项目归属（ScoreConflict 的组成）。"""

    tree_id: str
    project_id: str
    score: float

    def __post_init__(self) -> None:
        _require_non_empty("tree_id", self.tree_id)
        _require_non_empty("project_id", self.project_id)
        _require_ratio("score", self.score)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ScoreConflict:
    """得分冲突诊断（澄清 Q1）：多棵命中且得分不同 → UNKNOWN 的理由，如实留痕。

    必须"多棵且得分不同"才构成冲突——单一得分或得分一致都不构成
    "可复现假设不成立"的证据。
    """

    structure_key: str
    hits: tuple = ()
    note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("structure_key", self.structure_key)
        object.__setattr__(self, "hits", _as_tuple("hits", self.hits, ConflictHit))
        if len(self.hits) < 2:
            raise ValidationError(f"hits 必须为 ≥ 2 棵命中树的清单，实际为 {len(self.hits)}")
        if len({hit.score for hit in self.hits}) < 2:
            raise ValidationError("hits 得分必须不同（得分一致不构成冲突）")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProjectHitStats:
    """单项目命中统计（稀释控制的双报告之一）。

    hit_ratio 为项目口径占比；tree_ratio 为树数占比（参考维度，同步呈现）。
    """

    project_id: str
    hits: int
    unknowns: int
    hit_ratio: float
    tree_count: int
    tree_ratio: float

    def __post_init__(self) -> None:
        _require_non_empty("project_id", self.project_id)
        _require_int("hits", self.hits)
        _require_int("unknowns", self.unknowns)
        _require_ratio("hit_ratio", self.hit_ratio)
        _require_int("tree_count", self.tree_count, minimum=1)
        _require_ratio("tree_ratio", self.tree_ratio)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HitDistribution:
    """命中分布：per-project 与合并口径双报告 + 冲突清单（FR-006）。

    合并口径必须等于逐项目合计（双记账一致，不得虚报）；无项目参与时允许空分布。
    """

    per_project: tuple = ()
    merged_hits: int = 0
    merged_unknowns: int = 0
    conflicts: tuple = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "per_project", _as_tuple("per_project", self.per_project, ProjectHitStats)
        )
        object.__setattr__(self, "conflicts", _as_tuple("conflicts", self.conflicts, ScoreConflict))
        _require_int("merged_hits", self.merged_hits)
        _require_int("merged_unknowns", self.merged_unknowns)
        total_hits = sum(stats.hits for stats in self.per_project)
        total_unknowns = sum(stats.unknowns for stats in self.per_project)
        if self.merged_hits != total_hits:
            raise ValidationError(
                f"merged_hits 必须等于逐项目合计 {total_hits}，实际为 {self.merged_hits}"
            )
        if self.merged_unknowns != total_unknowns:
            raise ValidationError(
                "merged_unknowns 必须等于逐项目合计 "
                f"{total_unknowns}，实际为 {self.merged_unknowns}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DilutionAlert:
    """稀释告警（如实可见，不阻止）：项目命中占比超阈 + 树数占比参考（FR-006）。

    只能在超阈（hit_ratio > threshold）时构造——未超阈的"告警"是误导；
    单项目构成（tree_ratio = 1.0）必须在 note 如实标注"单项目构成"（SC-004）。
    """

    project_id: str
    hit_ratio: float
    tree_ratio: float
    threshold: float
    note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("project_id", self.project_id)
        _require_ratio("hit_ratio", self.hit_ratio)
        _require_ratio("tree_ratio", self.tree_ratio)
        _require_ratio("threshold", self.threshold)
        if self.hit_ratio <= self.threshold:
            raise ValidationError(
                f"稀释告警必须在命中占比超阈时构造：命中占比 {self.hit_ratio} "
                f"≤ 阈值 {self.threshold}"
            )
        if self.tree_ratio == 1.0 and "单项目构成" not in self.note:
            raise ValidationError("单项目构成（树数占比 1.0）必须在 note 如实标注")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PoolSnapshot:
    """构建快照（不可变记录）：分组/版本分组/树清单/前置判定/做梦开关/构建时间。

    落盘于 replay/pools/{agent}/{form}/{pool_id}.json，只增不改；
    content_hash = 快照内容哈希（构建时间不入哈希——同输入必得同哈希，幂等前提）。
    """

    pool_id: str
    agent_id: str
    form: str
    version_groups: tuple = ()
    trees: tuple = ()
    min_trees: int = 3
    conditions_met: bool = True
    enabled_for_dreaming: bool = False
    built_at: str = ""
    content_hash: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        _require_hash("pool_id", self.pool_id)
        _require_non_empty("agent_id", self.agent_id)
        _require_non_empty("form", self.form)
        object.__setattr__(
            self, "version_groups", _as_tuple("version_groups", self.version_groups, VersionGroup)
        )
        if not self.version_groups:
            raise ValidationError("version_groups 必须为非空版本分组清单")
        object.__setattr__(self, "trees", _as_tuple("trees", self.trees, PoolTreeRef))
        if not self.trees:
            raise ValidationError("trees 必须为非空树清单（快照的树清单不得为空）")
        _require_int("min_trees", self.min_trees, minimum=1)
        _require_bool("conditions_met", self.conditions_met)
        _require_bool("enabled_for_dreaming", self.enabled_for_dreaming)
        _require_non_empty("built_at", self.built_at)
        _require_optional_hash("content_hash", self.content_hash)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LineageTreeRef:
    """谱系树条目：跨项目树（tree_id + 项目归属）。"""

    tree_id: str
    project_id: str

    def __post_init__(self) -> None:
        _require_non_empty("tree_id", self.tree_id)
        _require_non_empty("project_id", self.project_id)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ChildVersionRef:
    """谱系子版本条目：由该策略版本派生的策略版本 + 其项目归属。"""

    version: str
    project_id: str

    def __post_init__(self) -> None:
        _require_non_empty("version", self.version)
        _require_non_empty("project_id", self.project_id)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CrossProjectLineage:
    """跨项目谱系报表（FR-009）：policy_version → 跨项目树 → 子策略版本。

    trees / child_versions 缺省为空元组——单项目版本的跨项目字段为空列表而非缺失
    （字段恒在，JSON 可机读、项目归属可标注）。
    """

    policy_version: str
    trees: tuple = ()
    child_versions: tuple = ()
    note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty("policy_version", self.policy_version)
        object.__setattr__(self, "trees", _as_tuple("trees", self.trees, LineageTreeRef))
        object.__setattr__(
            self,
            "child_versions",
            _as_tuple("child_versions", self.child_versions, ChildVersionRef),
        )

    def to_dict(self) -> dict:
        return asdict(self)
