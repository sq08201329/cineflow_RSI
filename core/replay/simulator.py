"""回放模拟器（契约 §1，FR-001~FR-007）。

由冻结历史树构建的只读回放环境：
- 状态 = 已揭示节点集合；节点仅 latent → revealed 单向迁移；
- 策略唯一信息入口：observed()（白名单投影）与 probe()（规范化精确匹配）；
- 无匹配返回 UNKNOWN，策略不得获得得分与任何节点信息（FR-004）；
- 全程零生成：budget.max_generation_calls 装配时强制归零（FR-005）；
- probe/observed 响应填充至 latency_quantum_ms 整数倍（决策 3，不变量 4）；
- `_latent` 只在宿主进程内，无任何出进程的通道（沙箱化见 core/sandbox）。
"""

import math
import time

from core.replay.clock import VirtualClock
from core.replay.errors import BudgetExhaustedError, PoolError, ValidationError
from core.replay.matching import node_gen_params, params_match
from core.replay.observation import (
    ProbeResult,
    observation_whitelist,
    project_observation,
    sum_costs,
)
from core.replay.trajectory import ReplayTrajectory, TrajectoryStatus
from core.tree.errors import NotFoundError
from core.tree.models import CostRecord, DiscoveryTree, TreeNode
from core.tree.store import TreeStore
from policies.base import Budget, assert_replay_budget, zero_generation_budget


def quanta_needed(elapsed_ms: float, quantum_ms: int) -> int:
    """计算已耗时间跨越的量子数（0 = 无需填充；quantum 为 0 时不填充）。"""
    if quantum_ms <= 0:
        return 0
    return math.ceil(elapsed_ms / quantum_ms)


def pad_to_quantum(start: float, quantum_ms: int) -> None:
    """确定性填充：sleep 至下一个时延量子边界（决策 3——常量填充，回放可复现）。"""
    if quantum_ms <= 0:
        return
    elapsed_ms = (time.perf_counter() - start) * 1000
    target_quanta = quanta_needed(elapsed_ms, quantum_ms)
    if target_quanta == 0:
        target_quanta = 1  # 任何调用至少占一个量子——响应时间不携带"是否命中"的信息
    delay_ms = target_quanta * quantum_ms - elapsed_ms
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)


def ensure_frozen(store: TreeStore, tree: DiscoveryTree) -> None:
    """冻结校验（FR-001）：树已完整落盘（根节点可查）才允许入池。"""
    try:
        store.get_node(tree.root_id)
    except NotFoundError as exc:
        raise PoolError(f"树未冻结（根节点未落盘，仍在写入中）：{tree.tree_id}") from exc


class ReplaySimulator:
    """进程内回放模拟器（实现 SimulatorEnv 协议；沙箱形态由 runner 经 IPC 桥接）。"""

    def __init__(self, *, worker_count: int, budget: Budget, latency_quantum_ms: int) -> None:
        if (
            not isinstance(latency_quantum_ms, int)
            or isinstance(latency_quantum_ms, bool)
            or latency_quantum_ms < 0
        ):
            raise ValidationError(
                f"latency_quantum_ms 必须为 ≥ 0 的整数，实际为 {latency_quantum_ms!r}"
            )
        self._clock = VirtualClock(worker_count=worker_count)
        self._budget = budget
        self._latency_quantum_ms = latency_quantum_ms
        self._revealed: dict[str, TreeNode] = {}
        self._latent: dict[str, list[TreeNode]] = {}
        self._whitelist_by_tree: dict[str, tuple[str, ...]] = {}
        self._tree_of_node: dict[str, str] = {}
        self._probe_count = 0
        self._total_cost = CostRecord()
        self._best_curve: list[float] = []
        self._agent_id: str | None = None
        self._policy_version = ""
        self._final_node_id: str | None = None
        self._status = TrajectoryStatus.COMPLETED
        self._diagnostics: dict = {}

    @classmethod
    def from_trees(
        cls,
        trees: list[DiscoveryTree],
        store: TreeStore,
        *,
        worker_count: int,
        budget: Budget,
        latency_quantum_ms: int,
    ) -> "ReplaySimulator":
        """由一棵或多棵已冻结的同 Agent 发现树构建模拟器（空列表 = 空池，合法）。"""
        if not isinstance(worker_count, int) or isinstance(worker_count, bool) or worker_count < 1:
            raise ValidationError(f"worker_count 必须为 ≥ 1 的整数，实际为 {worker_count!r}")

        # FR-005：回放装配强制零生成——归零 + 断言双落实
        budget = zero_generation_budget(budget)
        assert_replay_budget(budget)

        agent_ids = {tree.agent_id for tree in trees}
        if len(agent_ids) > 1:
            raise PoolError(f"模拟器池仅支持同 Agent 多树合并，实际 agent_id 集合：{agent_ids}")
        for tree in trees:
            ensure_frozen(store, tree)

        simulator = cls(
            worker_count=worker_count, budget=budget, latency_quantum_ms=latency_quantum_ms
        )
        simulator._agent_id = trees[0].agent_id if trees else None
        for tree in trees:
            simulator._whitelist_by_tree[tree.tree_id] = observation_whitelist(tree.config_snapshot)
            for node in store.nodes_of(tree.tree_id):
                simulator._tree_of_node[node.node_id] = tree.tree_id
                if node.parent_id is None:
                    simulator._revealed[node.node_id] = node
                else:
                    simulator._latent.setdefault(node.parent_id, []).append(node)
        return simulator

    @property
    def budget(self) -> Budget:
        return self._budget

    @property
    def agent_id(self) -> str | None:
        """池内树的 agent_id（空池为 None）；版本落盘路径的谱系字段。"""
        return self._agent_id

    @property
    def clock(self) -> VirtualClock:
        return self._clock

    def _project(self, node: TreeNode):
        whitelist = self._whitelist_by_tree[self._tree_of_node[node.node_id]]
        return project_observation(node, whitelist)

    def _record_best(self) -> None:
        """每个决策轮后记一次当前最优得分（无已得分节点则曲线不增长——不编造分数）。"""
        scores = [n.score for n in self._revealed.values() if n.score is not None]
        if scores:
            self._best_curve.append(max(scores))

    def _match_candidates(self, parent_id: str, gen_params: dict) -> list[TreeNode]:
        """已揭示父节点的候选选择（002 口径：同参子节点，规范化精确匹配）。

        扩展点：跨项目池化回放（功能 011）覆写本方法改为**池级结构键匹配**
        （候选集跨项目扩充），probe 的揭示/计费/预算/时延量子语义全部复用。
        """
        return [
            node
            for node in self._latent.get(parent_id, [])
            if params_match(node_gen_params(node), gen_params)
        ]

    def observed(self) -> dict:
        """策略唯一的信息入口之一：仅已揭示节点的白名单投影；决策轮 +1。"""
        start = time.perf_counter()
        self._clock.tick_decision()
        result = {nid: self._project(node) for nid, node in self._revealed.items()}
        self._record_best()
        pad_to_quantum(start, self._latency_quantum_ms)
        return result

    def probe(self, parent_id: str, gen_params: dict) -> ProbeResult:
        """揭示动作：规范化精确匹配真实历史节点，揭示并计虚拟成本；无匹配 UNKNOWN。"""
        start = time.perf_counter()
        if self._probe_count >= self._budget.max_probes:
            raise BudgetExhaustedError(
                f"probe 预算耗尽（上限 {self._budget.max_probes} 次，FR-007）"
            )
        self._probe_count += 1
        self._clock.tick_decision()

        candidates: list[TreeNode] = []
        # 未揭示的父节点对策略不可见：探测它按 UNKNOWN 处理，不泄漏存在性
        if parent_id in self._revealed:
            candidates = self._match_candidates(parent_id, gen_params)

        if not candidates:
            self._record_best()
            pad_to_quantum(start, self._latency_quantum_ms)
            return ProbeResult.unknown()

        for node in candidates:  # latent → revealed 单向迁移
            self._revealed[node.node_id] = node
        self._latent[parent_id] = [n for n in self._latent[parent_id] if n not in candidates]
        self._clock.tick_execution(batch_size=len(candidates))
        virtual_cost = sum_costs(node.cost for node in candidates)
        self._total_cost = sum_costs([self._total_cost, virtual_cost])
        self._record_best()
        result = ProbeResult(
            status="ok",
            nodes=[self._project(node) for node in candidates],
            virtual_cost=virtual_cost,
        )
        pad_to_quantum(start, self._latency_quantum_ms)
        return result

    def finalize(
        self,
        *,
        policy_version: str,
        final_node_id: str | None,
        status: str | TrajectoryStatus = TrajectoryStatus.COMPLETED,
        diagnostics: dict | None = None,
    ) -> None:
        """回放收尾：写入策略版本（FR-015 谱系字段）与结局。"""
        self._policy_version = policy_version
        self._final_node_id = final_node_id
        self._status = TrajectoryStatus(status)
        self._diagnostics = dict(diagnostics or {})

    def trajectory(self) -> ReplayTrajectory:
        """截至当前的完整轨迹快照（frozen，可直接 JSON 序列化供做梦层消费）。"""
        return ReplayTrajectory(
            policy_version=self._policy_version,
            best_score_curve=list(self._best_curve),
            probe_count=self._probe_count,
            effective_sequential_rounds=self._clock.effective_sequential_rounds,
            total_cost=self._total_cost,
            final_node_id=self._final_node_id,
            status=self._status,
            diagnostics=dict(self._diagnostics),
        )
