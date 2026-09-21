"""跨项目匹配（功能 011 US2，contracts/replay-hit.md C3）。

- 匹配规则 = **结构键**（002 规范化精确匹配槽 `gen_params`）+ **版本集 hash**，
  在池内全部项目树中查找（跨项目复用历史得分，不再 UNKNOWN）；
- 单棵命中 → 返回该历史节点得分；多棵命中且得分相同 → 命中（一致口径）；
- **多棵命中且得分不同（冲突）→ UNKNOWN + ScoreConflict 诊断**（树清单/得分/归属齐全）：
  不取均值、不取最新、不编造——"可复现"假设在该结构键上不成立（澄清 Q1，原则六）；
- 跨版本集不命中（评估器版本是匹配的组成，原则一）；
- 回放口径：已揭示节点不再命中（与 002 同口径，避免重复揭示与重复计费）。

读路径为 001 三维索引的跨项目扩展（`store.trees_by(agent_id=...)` + `nodes_of`），
无树写入、无新 DB 表；`PoolIndex` 供回放路径预建一次，避免逐 probe 重扫树。
"""

from dataclasses import dataclass
from typing import Literal

from core.replay.errors import PoolError, ValidationError
from core.replay.matching import node_gen_params, normalize_params
from core.replay.pooling_models import ConflictHit, MergedPool, ScoreConflict
from core.tree.models import TreeNode
from core.tree.store import TreeStore


@dataclass(frozen=True)
class IndexedNode:
    """池内可匹配节点：节点 + 树/项目归属（跨项目归属可追溯）。"""

    tree_id: str
    project_id: str
    node: TreeNode

    def to_dict(self) -> dict:
        """机读形态（诊断/报告用；不含观测内容）。"""
        return {
            "tree_id": self.tree_id,
            "project_id": self.project_id,
            "node_id": self.node.node_id,
            "score": self.node.score,
        }


@dataclass(frozen=True)
class PoolIndex:
    """池内匹配索引：按版本集 hash 收集候选节点（池序：(created_at, project_id, tree_id)）。

    version_hash_by_tree / project_by_tree 为归属查询表（UNKNOWN 结果的探测树归属用）。
    """

    pool_id: str
    version_hash_by_tree: dict
    project_by_tree: dict
    by_version: dict

    def nodes_for(self, version_hash: str) -> tuple[IndexedNode, ...]:
        """该版本集的候选节点（池序；版本集不在池内 → 空——不命中）。"""
        return tuple(self.by_version.get(version_hash, ()))

    def node(self, node_id: str) -> IndexedNode | None:
        """按节点 id 取索引条目（回放揭示用；不在索引内 → None）。"""
        for entries in self.by_version.values():
            for entry in entries:
                if entry.node.node_id == node_id:
                    return entry
        return None


@dataclass(frozen=True)
class MatchedHit:
    """命中条目：树/项目归属 + 节点 + 得分（FAILED 节点无得分，与 002 观测口径一致）。"""

    tree_id: str
    project_id: str
    node_id: str
    score: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.tree_id, str) or not self.tree_id:
            raise ValidationError("tree_id 必须为非空字符串")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ValidationError("project_id 必须为非空字符串")
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValidationError("node_id 必须为非空字符串")
        if self.score is not None and (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not 0.0 <= self.score <= 1.0
        ):
            raise ValidationError(f"score 必须 ∈ [0,1] 或 None，实际为 {self.score!r}")

    def to_dict(self) -> dict:
        return {
            "tree_id": self.tree_id,
            "project_id": self.project_id,
            "node_id": self.node_id,
            "score": self.score,
        }


@dataclass(frozen=True)
class MatchResult:
    """跨项目匹配结果：命中（得分 + 命中树清单）或 UNKNOWN（可带冲突诊断）。"""

    status: Literal["hit", "unknown"]
    structure_key: str
    version_hash: str
    hits: tuple = ()
    score: float | None = None
    conflict: ScoreConflict | None = None

    def __post_init__(self) -> None:
        if self.status not in ("hit", "unknown"):
            raise ValidationError(f"status 必须为 'hit' 或 'unknown'，实际为 {self.status!r}")
        if not isinstance(self.structure_key, str) or not self.structure_key:
            raise ValidationError("structure_key 必须为非空字符串（规范化结构键）")
        if not isinstance(self.version_hash, str) or not self.version_hash:
            raise ValidationError("version_hash 必须为非空字符串")
        hits = tuple(self.hits)
        for hit in hits:
            if not isinstance(hit, MatchedHit):
                raise ValidationError(f"hits 的元素必须为 MatchedHit，实际为 {hit!r}")
        object.__setattr__(self, "hits", hits)
        if self.status == "hit":
            if not hits:
                raise ValidationError("命中结果必须含非空命中树清单")
            if self.conflict is not None:
                raise ValidationError("命中结果不得携带冲突诊断（冲突即 UNKNOWN）")
        else:
            if hits:
                raise ValidationError("UNKNOWN 结果不得携带命中树（不得分、零信息）")
            if self.score is not None:
                raise ValidationError("UNKNOWN 结果不得携带得分（不取均值、不取最新）")
            if self.conflict is not None and not isinstance(self.conflict, ScoreConflict):
                raise ValidationError("conflict 必须为 ScoreConflict 或 None")

    @property
    def matched_project_ids(self) -> tuple[str, ...]:
        """参与命中的项目（去重保序：命中清单按池序排列）。"""
        seen: list[str] = []
        for hit in self.hits:
            if hit.project_id not in seen:
                seen.append(hit.project_id)
        return tuple(seen)

    @property
    def matched_tree_ids(self) -> tuple[str, ...]:
        return tuple(hit.tree_id for hit in self.hits)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "structure_key": self.structure_key,
            "version_hash": self.version_hash,
            "hits": [hit.to_dict() for hit in self.hits],
            "score": self.score,
            "conflict": None if self.conflict is None else self.conflict.to_dict(),
        }


def build_pool_index(
    store: TreeStore, pool: MergedPool, *, exclude_tree_ids: tuple[str, ...] = ()
) -> PoolIndex:
    """构建池内匹配索引（按版本集 hash 收集候选节点；池序稳定）。

    exclude_tree_ids：留出树（如 validation 分树）——不揭示、不参与匹配（防泄漏红线）。
    """
    if not isinstance(pool, MergedPool):
        raise ValidationError(f"pool 必须为 MergedPool，实际为 {type(pool).__name__}")
    excluded = {str(tree_id) for tree_id in exclude_tree_ids}
    version_hash_by_tree: dict[str, str] = {}
    project_by_tree: dict[str, str] = {}
    by_version: dict[str, list[IndexedNode]] = {}
    for group in pool.version_groups:
        for ref in group.trees:  # 池内树序已按 (created_at, project_id, tree_id) 稳定排序
            if ref.tree_id in excluded:
                continue
            version_hash_by_tree[ref.tree_id] = group.evaluator_versions_hash
            project_by_tree[ref.tree_id] = ref.project_id
            entries = by_version.setdefault(group.evaluator_versions_hash, [])
            for node in store.nodes_of(ref.tree_id):
                entries.append(
                    IndexedNode(tree_id=ref.tree_id, project_id=ref.project_id, node=node)
                )
    return PoolIndex(
        pool_id=pool.pool_id,
        version_hash_by_tree=version_hash_by_tree,
        project_by_tree=project_by_tree,
        by_version={key: tuple(value) for key, value in by_version.items()},
    )


def _conflict(structure_key: str, hits: tuple[MatchedHit, ...], scores: set) -> ScoreConflict:
    """冲突诊断（澄清 Q1）：命中树/得分/归属齐全，如实标注不取均值、不取最新。"""
    detail = "、".join(
        f"{hit.project_id}/{hit.tree_id}={hit.score}"
        for hit in sorted(hits, key=lambda h: (h.project_id, h.tree_id))
        if hit.score is not None
    )
    return ScoreConflict(
        structure_key=structure_key,
        hits=tuple(
            ConflictHit(tree_id=hit.tree_id, project_id=hit.project_id, score=hit.score)
            for hit in hits
            if hit.score is not None
        ),
        note=(
            f"多棵命中且得分不同（{sorted(scores)}）：{detail} → UNKNOWN 不得分"
            "（不取均值、不取最新——可复现假设不成立，澄清 Q1）"
        ),
    )


def cross_match(
    pool: MergedPool,
    structure_key: dict,
    version_hash: str,
    *,
    store: TreeStore | None = None,
    index: PoolIndex | None = None,
    exclude_node_ids: frozenset[str] | tuple[str, ...] = frozenset(),
) -> MatchResult:
    """跨项目匹配（C3）：结构键 + 版本集 hash 在池内全部项目树中查找。

    - store：读路径（001 三维索引）；index：已建索引（回放路径预建，二者至少其一）；
    - exclude_node_ids：已揭示节点（不再命中——与 002 同口径）；
    - 命中（得分一致）→ MatchResult(status="hit", hits=..., score=...)；
      冲突（多棵命中得分不同）→ UNKNOWN + ScoreConflict；无匹配 → UNKNOWN。
    """
    if not isinstance(pool, MergedPool):
        raise ValidationError(f"pool 必须为 MergedPool，实际为 {type(pool).__name__}")
    normalized_key = normalize_params(structure_key)
    if not isinstance(version_hash, str) or not version_hash:
        raise ValidationError(f"version_hash 必须为非空字符串，实际为 {version_hash!r}")
    if index is None:
        if store is None:
            raise ValidationError("cross_match 必须提供 store（读路径）或 index（已建索引）")
        index = build_pool_index(store, pool)
    if index.pool_id != pool.pool_id:
        raise PoolError(
            f"匹配索引与池不符（索引 {index.pool_id}，池 {pool.pool_id}）：拒绝跨池匹配"
        )
    excluded = frozenset(exclude_node_ids)

    hits = tuple(
        MatchedHit(
            tree_id=entry.tree_id,
            project_id=entry.project_id,
            node_id=entry.node.node_id,
            score=entry.node.score,
        )
        for entry in index.nodes_for(version_hash)
        if entry.node.node_id not in excluded
        and normalize_params(node_gen_params(entry.node)) == normalized_key
    )
    if not hits:
        return MatchResult(
            status="unknown", structure_key=normalized_key, version_hash=version_hash
        )

    scores = {hit.score for hit in hits if hit.score is not None}
    if len(scores) > 1:
        return MatchResult(
            status="unknown",
            structure_key=normalized_key,
            version_hash=version_hash,
            conflict=_conflict(normalized_key, hits, scores),
        )
    return MatchResult(
        status="hit",
        structure_key=normalized_key,
        version_hash=version_hash,
        hits=hits,
        score=next(iter(scores)) if scores else None,
    )
