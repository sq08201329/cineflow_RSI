"""3 万节点回放性能基准（T138 / SC-006）。

分支因子 10 建树（BFS 至 3 万节点）→ 构建模拟器 → 回放参考策略，
全程 < 10 分钟断言。优先 PG（CINEFLOW_PG_TEST_DSN），不可用时退
SQLite 内存库——保证本地可跑。
"""

import time

import pytest
from sqlalchemy import create_engine, text

from core.replay.simulator import ReplaySimulator
from core.tree.db import create_schema, metadata
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id
from core.tree.store import create_tree_store
from policies.base import Budget
from tests.integration.conftest import PG_DSN
from tests.stubs import ReferencePolicy

pytestmark = pytest.mark.integration

TOTAL_NODES = 30_000
BRANCHING = 10
TIME_BUDGET_SECONDS = 600  # SC-006：全程 < 10 分钟


@pytest.fixture(scope="module")
def bench_backend():
    """优先 PG，不可用退 SQLite 内存库（本地可跑）。"""
    try:
        engine = create_engine(PG_DSN)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        backend_name = "postgresql"
    except Exception:  # noqa: BLE001 - 基准回退 SQLite
        engine = create_engine("sqlite+pysqlite:///:memory:")
        backend_name = "sqlite"
    with engine.begin() as conn:
        metadata.drop_all(conn)
    create_schema(engine)
    yield engine, backend_name
    with engine.begin() as conn:
        metadata.drop_all(conn)
    engine.dispose()


def test_3万节点回放基准(bench_backend):
    engine, backend_name = bench_backend
    store = create_tree_store(engine)

    timings: dict[str, float] = {"backend": backend_name}
    total_start = time.perf_counter()

    # 1) 建树：分支因子 10，BFS 至 3 万节点
    start = time.perf_counter()
    tree = DiscoveryTree(
        tree_id=new_id(),
        project_id="bench",
        agent_id="agent-bench",
        policy_version="a1b2c3d4e5f6",
        root_id=new_id(),
        node_ids=[],
        config_snapshot={
            "evaluator_weights": {"rule.x": 0.0},
            "observation_fields": ["gen_params"],
        },
    )
    store.create_tree(tree)

    def make_node(node_id, parent_id, depth, serial, gen_params, score):
        return TreeNode(
            node_id=node_id,
            tree_id=tree.tree_id,
            parent_id=parent_id,
            depth=depth,
            agent_id="agent-bench",
            policy_version="a1b2c3d4e5f6",
            prompt="",
            observation_context={"gen_params": gen_params},
            artifact_hash="ab" * 32,
            eval_breakdown={"rule.x@1": {"score": score}},
            score=score,
            cost=CostRecord(llm_calls=1),
            status=NodeStatus.EVALUATED,
            created_at=float(serial),
        )

    store.append_node(make_node(tree.root_id, None, 0, 0, {}, 0.4))
    created = 1
    frontier = [(tree.root_id, 0)]
    serial = 0
    while created < TOTAL_NODES:
        next_frontier = []
        for parent_id, parent_depth in frontier:
            for _ in range(BRANCHING):
                if created >= TOTAL_NODES:
                    break
                serial += 1
                node_id = new_id()
                store.append_node(
                    make_node(
                        node_id,
                        parent_id,
                        parent_depth + 1,
                        serial,
                        {"lane": serial % 4},
                        0.5 + (serial % 10) * 0.01,
                    )
                )
                next_frontier.append((node_id, parent_depth + 1))
                created += 1
        frontier = next_frontier
    timings["build_tree"] = time.perf_counter() - start

    # 2) 构建模拟器（3 万节点全量装载）
    start = time.perf_counter()
    simulator = ReplaySimulator.from_trees(
        [tree], store, worker_count=4, budget=Budget(max_probes=50), latency_quantum_ms=0
    )
    timings["build_simulator"] = time.perf_counter() - start

    # 3) 回放参考策略（进程内形态；沙箱形态由 e2e 覆盖）
    start = time.perf_counter()
    policy = ReferencePolicy(param_grid=[{"lane": 1}, {"lane": 2}], max_rounds=40)
    final_id = policy.solve(simulator, simulator.budget)
    simulator.finalize(policy_version=policy.policy_version, final_node_id=final_id)
    trajectory = simulator.trajectory()
    timings["replay"] = time.perf_counter() - start

    timings["total"] = time.perf_counter() - total_start
    print(f"\n基准实测（{backend_name}）：{timings}")

    assert trajectory.probe_count > 0
    assert len(store.nodes_of(tree.tree_id)) == TOTAL_NODES
    assert timings["total"] < TIME_BUDGET_SECONDS, f"全程 {timings['total']:.1f}s 超过 10 分钟预算"
