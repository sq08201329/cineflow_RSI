"""M=128 全量档做梦基准（T424 / SC-001）。

一轮做梦 M=128 全池沙箱回放全程 < 30 分钟断言。本地 Docker 真实执行
（hardened 后端）；数据源 PG 优先退 SQLite；实测耗时打印入报告。
"""

import time

import pytest
from sqlalchemy import create_engine, text

from dreaming.candidates import MutatorGenerator
from dreaming.pipeline import default_sandbox_replay, run_dream_round
from tests.integration.conftest import PG_DSN

pytestmark = pytest.mark.integration

TIME_BUDGET_SECONDS = 1800  # SC-001：全程 < 30 分钟


@pytest.fixture(scope="module")
def bench_pool():
    """做梦基准池：PG 优先退 SQLite；两棵小树（回放负载与树规模弱相关）。"""
    from core.replay.pool import SimulatorPool
    from core.tree.db import create_schema
    from core.tree.db import metadata as tree_metadata
    from core.tree.models import CostRecord, NodeStatus
    from core.tree.store import create_tree_store

    try:
        engine = create_engine(PG_DSN)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        name = "postgresql"
    except Exception:  # noqa: BLE001 - 基准回退 SQLite
        engine = create_engine("sqlite+pysqlite:///:memory:")
        name = "sqlite"
    with engine.begin() as conn:
        tree_metadata.drop_all(conn)
    create_schema(engine)
    store = create_tree_store(engine)

    # 两棵最小树（root + 两档子节点）
    from core.tree.models import DiscoveryTree, TreeNode, new_id

    pool = SimulatorPool(store)
    for _t in range(2):
        tree = DiscoveryTree(
            tree_id=new_id(),
            project_id="dream-bench",
            agent_id="agent-dream",
            policy_version="a1b2c3d4e5f6",
            root_id=new_id(),
            node_ids=[],
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params"],
            },
        )
        store.create_tree(tree)
        store.append_node(
            TreeNode(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                parent_id=None,
                depth=0,
                agent_id="agent-dream",
                policy_version="a1b2c3d4e5f6",
                prompt="",
                observation_context={"gen_params": {}},
                artifact_hash="ab" * 32,
                eval_breakdown={},
                score=0.4,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=1.0,
            )
        )
        for i, temp in enumerate((0.3, 0.7)):
            store.append_node(
                TreeNode(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="agent-dream",
                    policy_version="a1b2c3d4e5f6",
                    prompt="",
                    observation_context={"gen_params": {"temperature": temp}},
                    artifact_hash="ab" * 32,
                    eval_breakdown={},
                    score=0.5 + 0.1 * i,
                    cost=CostRecord(llm_calls=1),
                    status=NodeStatus.EVALUATED,
                    created_at=2.0 + i,
                )
            )
        pool.add_tree(tree)
    return pool, name


def test_M128全量档做梦基准(bench_pool, docker_available, champion_source, dream_config, tmp_path):
    pool, backend_name = bench_pool
    start = time.perf_counter()
    result = run_dream_round(
        "agent-dream",
        champion_source(),
        MutatorGenerator("dream-agent-dream-bench"),
        pool,
        None,
        dream_config,
        replay_fn=default_sandbox_replay,
        history_root=tmp_path,
        m=128,
    )
    elapsed = time.perf_counter() - start
    print(f"\nM=128 基准实测（{backend_name} 数据源 + 本地沙箱）：{elapsed:.1f}s")

    assert result.status in ("completed", "failed_all_unknown")
    assert len(result.candidates) <= 128
    assert result.diagnostics["generation_api_calls"] == 0
    assert elapsed < TIME_BUDGET_SECONDS, f"一轮 M=128 做梦 {elapsed:.1f}s 超过 30 分钟预算"


@pytest.fixture(scope="module")
def docker_available():
    from core.sandbox.backends import NoBackendAvailableError, select_backend

    try:
        select_backend()
    except NoBackendAvailableError:
        pytest.skip("Docker 不可用，跳过做梦基准（SC-001 需真实沙箱执行）")
