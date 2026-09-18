"""回放模拟器单测（US1 / T112）。

契约 §1 全表：揭示状态机、UNKNOWN 不得分、多节点同揭示、FAILED 节点可回放、
预算耗尽拒绝、已揭示单调递增、零生成断言、冻结/异 Agent 校验、空模拟器。
"""

import pytest

from core.replay.errors import BudgetExhaustedError, PoolError, ValidationError
from core.replay.simulator import ReplaySimulator, quanta_needed
from core.tree.models import CostRecord, NodeStatus
from policies.base import Budget

PARAMS_A = {"temperature": 0.3}
PARAMS_B = {"temperature": 0.7}

# 树形：root(0.4) ─┬─ a(PARAMS_A, 0.5) ── a1(PARAMS_A, 0.95)
#                ├─ b(PARAMS_B, 0.9)
#                └─ f(PARAMS_A, FAILED)
TREE_SPEC = [
    (None, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
    (0, PARAMS_A, 0.5, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
    (0, PARAMS_B, 0.9, NodeStatus.EVALUATED, CostRecord(llm_calls=3)),
    (1, PARAMS_A, 0.95, NodeStatus.EVALUATED, CostRecord(llm_calls=4)),
    (0, PARAMS_A, None, NodeStatus.FAILED, CostRecord(llm_calls=5)),
]


@pytest.fixture()
def sim(tree_store, build_historical_tree):
    tree, ids = build_historical_tree(TREE_SPEC)
    simulator = ReplaySimulator.from_trees(
        [tree], tree_store, worker_count=2, budget=Budget(max_probes=10), latency_quantum_ms=0
    )
    return simulator, tree, ids


class Test构建校验:
    def test_零生成强制归零(self, tree_store, build_historical_tree):
        """FR-005：回放装配强制 max_generation_calls = 0，不依赖调用方自觉。"""
        from policies.base import assert_replay_budget

        tree, _ = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree],
            tree_store,
            worker_count=2,
            budget=Budget(max_probes=10, max_generation_calls=99),
            latency_quantum_ms=0,
        )
        assert simulator.budget.max_generation_calls == 0
        assert_replay_budget(simulator.budget)

    def test_worker_count_非法报_ValidationError(self, tree_store, build_historical_tree):
        tree, _ = build_historical_tree(TREE_SPEC)
        with pytest.raises(ValidationError):
            ReplaySimulator.from_trees(
                [tree], tree_store, worker_count=0, budget=Budget(1), latency_quantum_ms=0
            )

    def test_未冻结树拒绝(self, tree_store, make_tree):
        """FR-001：未落盘（仍在写入中）的树入池必须被拒绝。"""
        ghost = make_tree()  # 未 create_tree
        with pytest.raises(PoolError, match="冻结"):
            ReplaySimulator.from_trees(
                [ghost], tree_store, worker_count=1, budget=Budget(1), latency_quantum_ms=0
            )

    def test_异_Agent_拒绝(self, tree_store, build_historical_tree):
        tree_a, _ = build_historical_tree(TREE_SPEC, agent_id="agent-a")
        tree_b, _ = build_historical_tree(TREE_SPEC, agent_id="agent-b", project_id="proj-b")
        with pytest.raises(PoolError, match="agent"):
            ReplaySimulator.from_trees(
                [tree_a, tree_b],
                tree_store,
                worker_count=1,
                budget=Budget(1),
                latency_quantum_ms=0,
            )

    def test_空树列表_空模拟器(self, tree_store):
        """边界：模拟器池为空——observed 空集、probe UNKNOWN、轨迹为空而非崩溃。"""
        simulator = ReplaySimulator.from_trees(
            [], tree_store, worker_count=1, budget=Budget(1), latency_quantum_ms=0
        )
        assert simulator.observed() == {}
        result = simulator.probe("anything", PARAMS_A)
        assert result.status == "unknown"
        trajectory = simulator.trajectory()
        assert trajectory.best_score_curve == []
        assert trajectory.probe_count == 1  # probe 调用计数照记（UNKNOWN 也耗预算）
        assert trajectory.total_cost == CostRecord()


class Test揭示状态机:
    def test_初始仅根已揭示(self, sim):
        simulator, tree, ids = sim
        assert set(simulator.observed()) == {ids[0]}

    def test_probe_精确匹配揭示真实节点(self, sim):
        simulator, _, ids = sim
        result = simulator.probe(ids[0], PARAMS_B)
        assert result.status == "ok"
        assert [n.node_id for n in result.nodes] == [ids[2]]
        assert result.nodes[0].score == 0.9
        assert result.nodes[0].cost.llm_calls == 3
        assert result.virtual_cost.llm_calls == 3
        assert ids[2] in simulator.observed()

    def test_多节点同揭示_批计虚拟成本(self, sim):
        """同父同参的 a 与 f 一次全部揭示；batch=2 → ⌈2/2⌉=1 串行轮。"""
        simulator, _, ids = sim
        result = simulator.probe(ids[0], PARAMS_A)
        assert result.status == "ok"
        assert {n.node_id for n in result.nodes} == {ids[1], ids[4]}
        assert result.virtual_cost.llm_calls == 2 + 5
        assert simulator.clock.effective_sequential_rounds == 1.0

    def test_FAILED_节点照常揭示(self, sim):
        """FR-014：FAILED 节点得分为空、成本有效，照常揭示。"""
        simulator, _, ids = sim
        simulator.probe(ids[0], PARAMS_A)
        obs = simulator.observed()[ids[4]]
        assert obs.score is None
        assert obs.cost.llm_calls == 5

    def test_UNKNOWN_不得分零信息(self, sim):
        """FR-004：无匹配走法返回 UNKNOWN，曲线不变、无节点信息。"""
        simulator, _, ids = sim
        simulator.observed()  # 让曲线先有一笔
        curve_before = list(simulator.trajectory().best_score_curve)
        result = simulator.probe(ids[0], {"temperature": 9.9})
        assert result.status == "unknown"
        assert result.nodes == []
        assert simulator.trajectory().best_score_curve == curve_before + [0.4]  # 仅记账无新分
        assert set(simulator.observed()) == {ids[0]}

    def test_probe_未揭示父节点返回_UNKNOWN(self, sim):
        """未揭示的父节点对策略不可见——探测它不得泄漏其存在性。"""
        simulator, _, ids = sim
        result = simulator.probe(ids[2], PARAMS_A)  # ids[2] 尚未揭示
        assert result.status == "unknown"
        assert ids[1] not in simulator.observed()  # ids[2] 的子节点也未被连带揭示

    def test_已揭示集合单调递增(self, sim):
        simulator, _, ids = sim
        snapshots = []
        snapshots.append(set(simulator.observed()))
        simulator.probe(ids[0], PARAMS_A)
        snapshots.append(set(simulator.observed()))
        simulator.probe(ids[0], PARAMS_B)
        snapshots.append(set(simulator.observed()))
        for earlier, later in zip(snapshots, snapshots[1:], strict=False):
            assert earlier < later  # 严格超集：只增不减

    def test_重复_probe_同参数_第二次_UNKNOWN(self, sim):
        """已揭示节点不再作为候选（latent → revealed 单向迁移）。"""
        simulator, _, ids = sim
        assert simulator.probe(ids[0], PARAMS_B).status == "ok"
        assert simulator.probe(ids[0], PARAMS_B).status == "unknown"


class Test预算:
    def test_预算耗尽拒绝(self, tree_store, build_historical_tree):
        tree, ids = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=1, budget=Budget(max_probes=1), latency_quantum_ms=0
        )
        simulator.probe(ids[0], PARAMS_A)
        with pytest.raises(BudgetExhaustedError, match="预算"):
            simulator.probe(ids[0], PARAMS_B)

    def test_预算为零_首次_probe_即拒绝(self, tree_store, build_historical_tree):
        tree, ids = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=1, budget=Budget(max_probes=0), latency_quantum_ms=0
        )
        assert simulator.observed()  # 观察不受预算限制
        with pytest.raises(BudgetExhaustedError):
            simulator.probe(ids[0], PARAMS_A)


class Test时钟:
    def test_observed_与_probe_各记一个决策轮(self, sim):
        simulator, _, ids = sim
        simulator.observed()
        simulator.probe(ids[0], PARAMS_A)
        simulator.observed()
        assert simulator.clock.decision_rounds == 3

    def test_未知probe_不计执行轮(self, sim):
        simulator, _, ids = sim
        simulator.probe(ids[0], {"temperature": 9.9})
        assert simulator.clock.effective_sequential_rounds == 0.0


class Test轨迹:
    def test_轨迹快照字段(self, sim):
        simulator, _, ids = sim
        simulator.observed()
        simulator.probe(ids[0], PARAMS_A)
        simulator.finalize(policy_version="abc123def456", final_node_id=ids[1])
        trajectory = simulator.trajectory()
        assert trajectory.policy_version == "abc123def456"
        assert trajectory.best_score_curve == [0.4, 0.5]
        assert trajectory.probe_count == 1
        assert trajectory.effective_sequential_rounds == 1.0
        assert trajectory.total_cost.llm_calls == 7  # a(2) + f(5)
        assert trajectory.final_node_id == ids[1]
        assert trajectory.status == "completed"

    def test_轨迹可序列化为_JSON(self, sim):
        import json

        simulator, _, ids = sim
        simulator.probe(ids[0], PARAMS_A)
        report = json.loads(simulator.trajectory().to_json())
        assert report["probe_count"] == 1
        assert report["status"] == "completed"

    def test_观测投影不泄漏白名单外字段(self, sim):
        simulator, _, ids = sim
        obs = simulator.observed()[ids[0]]
        assert set(obs.fields) == {"gen_params"}


class Test时延量子:
    def test_quanta_needed_计算(self):
        assert quanta_needed(0, 50) == 0
        assert quanta_needed(1, 50) == 1
        assert quanta_needed(50, 50) == 1
        assert quanta_needed(51, 50) == 2
        assert quanta_needed(10, 0) == 0  # 量子为 0 = 不填充

    def test_probe_响应填充至量子整数倍(self, tree_store, build_historical_tree):
        """不变量 4（进程内形态）：响应时间不早于下一个量子边界。"""
        import time

        tree, ids = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=1, budget=Budget(5), latency_quantum_ms=20
        )
        start = time.perf_counter()
        simulator.probe(ids[0], PARAMS_B)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms >= 20
        assert elapsed_ms < 100  # 容忍调度误差，但不得超过两个量子太多
