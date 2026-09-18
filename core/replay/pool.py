"""模拟器池（FR-001，US1 验收场景 6）：同 Agent 多棵冻结树合并。

候选节点取自池内全部树的真实历史节点；未冻结（仍在写入中）的树与
异 Agent 的树入池即拒绝；空池合法——回放产出空轨迹而非崩溃。
"""

from core.replay.errors import PoolError
from core.replay.simulator import ReplaySimulator, ensure_frozen
from core.tree.models import DiscoveryTree
from core.tree.store import TreeStore
from policies.base import Budget


class SimulatorPool:
    """模拟器池：收集冻结树并构建合并视图的模拟器。"""

    def __init__(self, store: TreeStore) -> None:
        self._store = store
        self._trees: list[DiscoveryTree] = []
        self._agent_id: str | None = None

    @property
    def trees(self) -> list[DiscoveryTree]:
        return list(self._trees)

    def add_tree(self, tree: DiscoveryTree) -> None:
        """入池校验：冻结（FR-001）+ 同 Agent（跨项目合并为二期事项）。"""
        if self._agent_id is not None and tree.agent_id != self._agent_id:
            raise PoolError(
                f"模拟器池仅支持同 Agent 合并：池内 {self._agent_id}，入池 {tree.agent_id}"
            )
        ensure_frozen(self._store, tree)
        self._trees.append(tree)
        self._agent_id = tree.agent_id

    def build(
        self, *, worker_count: int, budget: Budget, latency_quantum_ms: int
    ) -> ReplaySimulator:
        """构建合并候选视图的模拟器（空池 = 空模拟器，合法）。"""
        return ReplaySimulator.from_trees(
            list(self._trees),
            self._store,
            worker_count=worker_count,
            budget=budget,
            latency_quantum_ms=latency_quantum_ms,
        )
