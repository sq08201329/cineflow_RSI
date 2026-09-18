"""模拟器池单测（US1 / T113）。

多树合并（同 Agent）、未冻结树拒绝、异 Agent 拒绝、空池空轨迹。
"""

import pytest

from core.replay.errors import PoolError
from core.replay.pool import SimulatorPool
from core.tree.models import CostRecord, NodeStatus
from policies.base import Budget

PARAMS = {"temperature": 0.5}
SPEC = [
    (None, {}, 0.3, NodeStatus.EVALUATED, CostRecord()),
    (0, PARAMS, 0.6, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
]


class Test池校验:
    def test_未冻结树拒绝(self, tree_store, make_tree):
        pool = SimulatorPool(tree_store)
        with pytest.raises(PoolError, match="冻结"):
            pool.add_tree(make_tree())  # 未落盘 = 未冻结

    def test_异_Agent_拒绝(self, tree_store, build_historical_tree):
        tree_a, _ = build_historical_tree(SPEC, agent_id="agent-a")
        tree_b, _ = build_historical_tree(SPEC, agent_id="agent-b", project_id="p2")
        pool = SimulatorPool(tree_store)
        pool.add_tree(tree_a)
        with pytest.raises(PoolError, match="agent"):
            pool.add_tree(tree_b)

    def test_池内树清单可查(self, tree_store, build_historical_tree):
        tree, _ = build_historical_tree(SPEC)
        pool = SimulatorPool(tree_store)
        pool.add_tree(tree)
        assert [t.tree_id for t in pool.trees] == [tree.tree_id]


class Test多树合并:
    def test_候选取自池内全部树(self, tree_store, build_historical_tree):
        """US1 验收场景 6：probe 候选来自池内全部树的真实历史节点。"""
        tree_a, ids_a = build_historical_tree(SPEC, project_id="pa")
        tree_b, ids_b = build_historical_tree(SPEC, project_id="pb")
        pool = SimulatorPool(tree_store)
        pool.add_tree(tree_a)
        pool.add_tree(tree_b)
        simulator = pool.build(worker_count=1, budget=Budget(4), latency_quantum_ms=0)

        # 两棵树的根都在初始已揭示集合中
        assert set(simulator.observed()) == {ids_a[0], ids_b[0]}
        # 分别 probe 两棵树的根，各自揭示真实子节点
        result_a = simulator.probe(ids_a[0], PARAMS)
        result_b = simulator.probe(ids_b[0], PARAMS)
        assert [n.node_id for n in result_a.nodes] == [ids_a[1]]
        assert [n.node_id for n in result_b.nodes] == [ids_b[1]]
        trajectory = simulator.trajectory()
        assert trajectory.probe_count == 2
        assert trajectory.best_score_curve[-1] == 0.6

    def test_空池构建_空轨迹(self, tree_store):
        """边界：模拟器池为空——回放直接产出空轨迹而非崩溃。"""
        pool = SimulatorPool(tree_store)
        simulator = pool.build(worker_count=1, budget=Budget(0), latency_quantum_ms=0)
        assert simulator.observed() == {}
        trajectory = simulator.trajectory()
        assert trajectory.best_score_curve == []
        assert trajectory.probe_count == 0
        assert trajectory.total_cost == CostRecord()
        assert trajectory.status == "completed"
