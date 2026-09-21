"""池化回放接线（功能 011 US2/US3，contracts/replay-hit.md C3~C5、lineage-acceptance.md C9）。

把合并池（US1）接到 002 的回放模拟器上：`probe` 的候选选择改为**跨项目结构键匹配**
（`cross_match`），揭示/计费/预算/时延量子/虚拟时钟等语义全部复用 002——
`ReplaySimulator._match_candidates` 是唯一扩展点，002 非池化路径逐字节不变（T1013 回归断言）。

- 版本集隔离：回放只在本版本组内揭示与匹配（`version_hash`；多版本池必须显式指定）；
- 冲突即 UNKNOWN：不揭示、不得分（`cross_match` 口径），诊断进回放结果归属（供 `hit_stats`）；
- 已揭示节点不再命中（002 同口径：揭示只发生一次、虚拟成本不重复计入）；
- 留出树（validation）：`exclude_tree_ids` 既不揭示也不参与匹配（防泄漏红线，C5）；
- 全程零生成：继承 002 的装配归零 + 断言双保险（原则三）；
- 池选择（T1021）：`select_replay_pool` 按 `replay.pooling.enabled_for_dreaming` 选池
  （默认关闭 → 单项目池；开启且前置满足 → 合并池；不足 → 回落并注明），
  `MergedSimulatorPool` 与 002 `SimulatorPool` 同 `build` 接口——调用方零特化（原则五）。
"""

from dataclasses import dataclass

from core.replay.cross_match import PoolIndex, build_pool_index, cross_match
from core.replay.errors import PoolError, ValidationError
from core.replay.hit_stats import ReplayOutcome, outcome_for_match
from core.replay.merged_pool import build_merged_pool, select_version_group
from core.replay.pool import SimulatorPool
from core.replay.pool_snapshot import persist_pool_snapshot
from core.replay.pooling_models import MergedPool, PoolingConfig, PoolSnapshot
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


@dataclass(frozen=True)
class PoolSelection:
    """回放池选择结果（C9）：池对象 + 是否合并池 + **如实注明的理由**。

    - pool：002 口径的 `SimulatorPool` 或同接口的 `MergedSimulatorPool`（调用方零特化）；
    - merged_pool / snapshot：合并池记录与构建快照（未走合并池为 None）；
    - note：为何未用合并池（开关关闭 / 前置条件不足 / 合并池构建被拒），如实可见不静默。
    """

    pool: object
    merged: bool
    note: str
    merged_pool: MergedPool | None = None
    snapshot: PoolSnapshot | None = None

    def to_dict(self) -> dict:
        return {
            "merged": self.merged,
            "note": self.note,
            "pool_id": None if self.merged_pool is None else self.merged_pool.pool_id,
            "tree_count": None if self.merged_pool is None else self.merged_pool.tree_count,
            "enabled_for_dreaming": (
                None if self.snapshot is None else self.snapshot.enabled_for_dreaming
            ),
            "conditions_met": None if self.snapshot is None else self.snapshot.conditions_met,
        }


class MergedSimulatorPool:
    """合并池的 `SimulatorPool` 形态（同一 `build` 接口，供做梦/回放装配零特化复用）。

    `.build(...)` 产出 `PooledReplaySimulator`（probe 走跨项目 `cross_match`）；
    `.trees` 为池内树实体（版本组隔离后的稳定序）。
    """

    def __init__(
        self,
        pool: MergedPool,
        store: TreeStore,
        *,
        version_hash: str | None = None,
        exclude_tree_ids: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(pool, MergedPool):
            raise ValidationError(f"pool 必须为 MergedPool，实际为 {type(pool).__name__}")
        self._pool = pool
        self._store = store
        self._version_hash = version_hash
        self._exclude_tree_ids = tuple(exclude_tree_ids)

    @property
    def pool(self) -> MergedPool:
        return self._pool

    @property
    def trees(self) -> tuple[DiscoveryTree, ...]:
        return pool_trees(
            self._store,
            self._pool,
            version_hash=self._version_hash,
            exclude_tree_ids=self._exclude_tree_ids,
        )

    def build(self, *, worker_count: int, budget: Budget, latency_quantum_ms: int):
        """构建池化回放模拟器（接口与 002 `SimulatorPool.build` 一致）。"""
        return PooledReplaySimulator.from_pool(
            self._pool,
            self._store,
            worker_count=worker_count,
            budget=budget,
            latency_quantum_ms=latency_quantum_ms,
            version_hash=self._version_hash,
            exclude_tree_ids=self._exclude_tree_ids,
        )


def select_replay_pool(
    store: TreeStore,
    agent_id: str,
    form: str,
    cfg: PoolingConfig,
    *,
    single_project_trees,
    version_hash: str | None = None,
) -> PoolSelection:
    """按 `replay.pooling.enabled_for_dreaming` 选择回放池（C9；通用机制，不特化 Agent）。

    - **默认关闭** → 单项目池（传入树集合的 002 `SimulatorPool`）+ note「未启用：开关关闭」，
      不构建合并池、不落快照（未越权产出池化产物）；
    - 开启且前置条件满足（树数 ≥ min_trees）→ 合并池 + 快照（开关状态与前置判定入快照）；
    - 开启但前置不足 → 单项目池 + note「未启用：前置条件不足」+ 快照（conditions_met=False）；
    - 合并池构建被拒（跨形态/缺版本集等）→ 单项目池 + note 如实转述拒绝理由（不静默吞错）。

    version_hash：部署评估器版本集哈希（回放作用域；多版本池必须显式给出——跨版本不混池）。
    """
    if not isinstance(cfg, PoolingConfig):
        raise ValidationError(f"cfg 必须为 PoolingConfig，实际为 {type(cfg).__name__}")
    trees = tuple(single_project_trees)
    if not trees:
        raise ValidationError("单项目池的树集合不得为空（回放装配需要候选树）")
    single_pool = SimulatorPool(store)
    for tree in trees:
        single_pool.add_tree(tree)

    if not cfg.enabled_for_dreaming:
        return PoolSelection(
            pool=single_pool,
            merged=False,
            note="未启用：开关关闭（replay.pooling.enabled_for_dreaming=false）",
        )

    try:
        merged_pool = build_merged_pool(store, agent_id, form, cfg, enforce_min_trees=False)
        snapshot = persist_pool_snapshot(merged_pool, cfg)
    except (PoolError, ValidationError) as exc:
        return PoolSelection(
            pool=single_pool,
            merged=False,
            note=f"未启用：合并池构建被拒（{exc}）——回落单项目池",
        )
    if not merged_pool.conditions_met:
        return PoolSelection(
            pool=single_pool,
            merged=False,
            note=f"未启用：前置条件不足（{merged_pool.note}）——回落单项目池",
            merged_pool=merged_pool,
            snapshot=snapshot,
        )
    successful_group = select_version_group(merged_pool, version_hash)
    return PoolSelection(
        pool=MergedSimulatorPool(
            merged_pool, store, version_hash=successful_group.evaluator_versions_hash
        ),
        merged=True,
        note="合并池已启用（跨项目）；构建快照已落盘",
        merged_pool=merged_pool,
        snapshot=snapshot,
    )
