"""参考策略端到端回放（US1 / T114，验收场景 1-6 的进程内闭环）。

手工小树 + 确定性 ReferencePolicy：全程回放后对轨迹逐字段与手工预期对账。
"""

import pytest

from core.replay.simulator import ReplaySimulator
from core.tree.models import CostRecord, NodeStatus
from policies.base import Budget
from tests.stubs import ReferencePolicy

# 手工小树：
#   root(0.4) ─┬─ n1({"temperature":0.3}, 0.5)
#              └─ n2({"temperature":0.7}, 0.9)   # 策略从未命中该走法 → 永不揭示
TREE_SPEC = [
    (None, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
    (0, {"temperature": 0.3}, 0.5, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
    (0, {"temperature": 0.7}, 0.9, NodeStatus.EVALUATED, CostRecord(llm_calls=3)),
]

# ReferencePolicy 网格 [{"temperature":0.3},{"temperature":0.7}]，max_rounds=4：
#   R0: observed（轮1，曲线 0.4）→ probe(root, 0.3) 命中 n1（轮2，probe1，⌈1/2⌉=1 轮，曲线 0.5）
#   R1: observed（轮3，曲线 0.5）→ probe(n1, 0.7) UNKNOWN（轮4，probe2，曲线 0.5）
#   R2: observed（轮5，曲线 0.5）→ probe(n1, 0.3) UNKNOWN（轮6，probe3，曲线 0.5）
#   R3: observed（轮7，曲线 0.5）→ probe(n1, 0.7) UNKNOWN（轮8，probe4=预算上限，曲线 0.5）
# 最终选中 n1（0.5）；n2（0.9）因走法未被探测而始终隐藏——UNKNOWN 不得分的直接体现。


@pytest.fixture()
def replay_setup(tree_store, build_historical_tree):
    tree, ids = build_historical_tree(TREE_SPEC)
    simulator = ReplaySimulator.from_trees(
        [tree], tree_store, worker_count=2, budget=Budget(max_probes=4), latency_quantum_ms=0
    )
    return simulator, tree, ids


class Test端到端回放对账:
    def test_轨迹逐字段与手工预期一致(self, replay_setup):
        simulator, _, ids = replay_setup
        policy = ReferencePolicy(max_rounds=4)
        final_id = policy.solve(simulator, simulator.budget)
        simulator.finalize(policy_version=policy.policy_version, final_node_id=final_id)
        trajectory = simulator.trajectory()

        assert trajectory.policy_version == policy.policy_version  # FR-015 谱系字段
        assert trajectory.best_score_curve == [0.4, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        assert trajectory.probe_count == 4
        assert trajectory.effective_sequential_rounds == 1.0  # 仅一次真实揭示，batch=1，⌈1/2⌉=1
        assert trajectory.total_cost == CostRecord(llm_calls=2)  # 仅 n1 入账
        assert trajectory.final_node_id == ids[1]
        assert trajectory.status == "completed"
        assert simulator.clock.decision_rounds == 8

        # 验收场景 2/4：UNKNOWN 走法零泄漏——n2 全程未出现在任何观测中
        assert ids[2] not in simulator.observed()
        # 验收场景 3：生成调用恒为 0（装配强制归零）
        assert simulator.budget.max_generation_calls == 0

    def test_确定性_同树同策略两次回放轨迹一致(self, tree_store, build_historical_tree):
        """回放正确性前提：同一冻结树 + 同一确定性策略 → 逐字节相同轨迹。"""
        tree, _ = build_historical_tree(TREE_SPEC)
        trajectories = []
        for _ in range(2):
            simulator = ReplaySimulator.from_trees(
                [tree],
                tree_store,
                worker_count=2,
                budget=Budget(max_probes=4),
                latency_quantum_ms=0,
            )
            policy = ReferencePolicy(max_rounds=4)
            final_id = policy.solve(simulator, simulator.budget)
            simulator.finalize(policy_version=policy.policy_version, final_node_id=final_id)
            trajectories.append(simulator.trajectory().to_json())
        assert trajectories[0] == trajectories[1]

    def test_零预算策略_仅观察并返回结论(self, tree_store, build_historical_tree):
        """边界：预算为 0 的策略不得 probe，仅可观察已揭示集合并返回结论。"""
        tree, ids = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=2, budget=Budget(max_probes=0), latency_quantum_ms=0
        )
        policy = ReferencePolicy(max_rounds=4)
        final_id = policy.solve(simulator, simulator.budget)
        assert final_id == ids[0]  # 仅根可见，结论即根
        assert simulator.trajectory().probe_count == 0

    def test_ReferencePolicy_版本号为代码哈希前12位(self):
        import inspect

        import blake3

        policy = ReferencePolicy()
        expected = blake3.blake3(inspect.getsource(ReferencePolicy).encode()).hexdigest()[:12]
        assert policy.policy_version == expected
        assert len(policy.policy_version) == 12
