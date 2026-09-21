"""池化回放接线（功能 011 US2 / T1016，contracts/replay-hit.md C3~C5）。

把合并池（US1）接到 002 的回放模拟器上：`probe` 的候选选择改为**跨项目结构键匹配**
（`cross_match`），揭示/计费/预算/时延量子/虚拟时钟等语义全部复用 002——
`ReplaySimulator._match_candidates` 是唯一扩展点，002 非池化路径逐字节不变（T1013 回归断言）。

- 版本集隔离：回放只在本版本组内揭示与匹配（`version_hash`；多版本池必须显式指定）；
- 冲突即 UNKNOWN：不揭示、不得分（`cross_match` 口径），诊断进回放结果归属（供 `hit_stats`）；
- 已揭示节点不再命中（002 同口径：揭示只发生一次、虚拟成本不重复计入）；
- 留出树（validation）：`exclude_tree_ids` 既不揭示也不参与匹配（防泄漏红线，C5）；
- 全程零生成：继承 002 的装配归零 + 断言双保险（原则三）。
"""

from core.replay.cross_match import PoolIndex, build_pool_index, cross_match
from core.replay.errors import ValidationError
from core.replay.hit_stats import ReplayOutcome, outcome_for_match
from core.replay.merged_pool import select_version_group
from core.replay.pooling_models import MergedPool
from core.replay.simulator import ReplaySimulator
from core.tree.models import DiscoveryTree, TreeNode
from core.tree.store import TreeStore
from policies.base import Budget


def pool_trees(
    store: TreeStore,
    pool: MergedPool,
    *,
    version_hash: str | None = None,
    exclude_tree_ids: tuple[str, ...] = (),
) -> tuple[DiscoveryTree, ...]:
    """取池内树实体（001 三维索引读路径），按池内稳定序返回。

    - version_hash：只取指定版本组的树（None = 单版本池缺省，多版本池拒绝）；
    - exclude_tree_ids：留出树（validation）不参与回放；
    - 树实体缺失（树被删/库不一致）→ 拒绝（不得静默少树回放）。
    """
    if not isinstance(pool, MergedPool):
        raise ValidationError(f"pool 必须为 MergedPool，实际为 {type(pool).__name__}")
    excluded = {str(tree_id) for tree_id in exclude_tree_ids}
    refs = select_version_group(pool, version_hash).trees
    unknown = sorted(excluded - {ref.tree_id for ref in refs})
    if unknown:
        raise ValidationError(f"留出树不属于池（版本组内），拒绝静默不生效：{unknown}")
    wanted = [ref for ref in refs if ref.tree_id not in excluded]
    if not wanted:
        raise ValidationError(
            f"留出后无可回放树（池 {pool.pool_id}：版本组内 {len(refs)} 棵，"
            f"留出 {len(excluded)} 棵）"
        )
    by_id = {tree.tree_id: tree for tree in store.trees_by(agent_id=pool.agent_id)}
    missing = [ref.tree_id for ref in wanted if ref.tree_id not in by_id]
    if missing:
        raise ValidationError(f"池内树实体缺失（存储不一致）：{missing}")
    return tuple(by_id[ref.tree_id] for ref in wanted)


class PooledReplaySimulator(ReplaySimulator):
    """池化回放模拟器：probe 走跨项目结构键匹配（C3），其余语义全部复用 002。

    回放结果归属（`outcomes`）覆盖所有**走匹配**的 probe（命中或 UNKNOWN，含冲突），
    供 `hit_stats` 产命中分布与稀释告警；未揭示父节点的 probe 与 002 一致（UNKNOWN，
    不走匹配、无从归属，不计入分布）。
    """

    def __init__(self, *, worker_count: int, budget: Budget, latency_quantum_ms: int) -> None:
        super().__init__(
            worker_count=worker_count, budget=budget, latency_quantum_ms=latency_quantum_ms
        )
        self._pool: MergedPool | None = None
        self._index: PoolIndex | None = None
        self._version_hash = ""
        self._outcomes: list[ReplayOutcome] = []

    @classmethod
    def from_pool(
        cls,
        pool: MergedPool,
        store: TreeStore,
        *,
        worker_count: int,
        budget: Budget,
        latency_quantum_ms: int,
        version_hash: str | None = None,
        exclude_tree_ids: tuple[str, ...] = (),
    ) -> "PooledReplaySimulator":
        """由合并池构建池化回放模拟器（版本组隔离 + 可选留出树）。"""
        trees = pool_trees(
            store, pool, version_hash=version_hash, exclude_tree_ids=exclude_tree_ids
        )
        simulator = cls.from_trees(
            list(trees),
            store,
            worker_count=worker_count,
            budget=budget,
            latency_quantum_ms=latency_quantum_ms,
        )
        simulator._pool = pool
        simulator._index = build_pool_index(store, pool, exclude_tree_ids=exclude_tree_ids)
        simulator._version_hash = simulator._index.version_hash_by_tree[trees[0].tree_id]
        return simulator

    @property
    def pool(self) -> MergedPool | None:
        return self._pool

    @property
    def version_hash(self) -> str:
        """本次回放所用版本集哈希（版本组隔离的机检字段）。"""
        return self._version_hash

    @property
    def outcomes(self) -> tuple[ReplayOutcome, ...]:
        """回放结果归属（命中/UNKNOWN 逐次；供 hit_stats 产分布与稀释告警）。"""
        return tuple(self._outcomes)

    def _match_candidates(self, parent_id: str, gen_params: dict) -> list[TreeNode]:
        """池级结构键匹配（C3）：候选集跨项目扩充；冲突即 UNKNOWN（不揭示、不得分）。"""
        index = self._index
        if index is None:  # pragma: no cover - 装配必经 from_pool
            raise ValidationError("池化回放模拟器未经 from_pool 装配（缺索引）")
        result = cross_match(
            self._pool,
            gen_params,
            self._version_hash,
            index=index,
            exclude_node_ids=frozenset(self._revealed),
        )
        # 归属口径：UNKNOWN 归属被 probe 的**树**（parent_id 是节点 id，需经索引映射）
        probe_tree_id = self._tree_of_node.get(parent_id, "")
        if not probe_tree_id:  # pragma: no cover - 已揭示父节点必属池内树
            return []
        self._outcomes.append(
            outcome_for_match(
                result,
                probe_tree_id=probe_tree_id,
                probe_project_id=index.project_by_tree.get(probe_tree_id),
            )
        )
        if result.status != "hit":
            return []
        return [index.by_node[hit.node_id].node for hit in result.hits]
