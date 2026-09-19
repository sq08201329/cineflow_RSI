"""视觉树冻结入池与真值回放（US2 / T326）。

冻结校验（GenJob 未终态拒绝）、真值回放得分即冻结常数、生成调用恒 0、
工件哈希引用可回取。PG 优先（CINEFLOW_PG_TEST_DSN）退 SQLite——本地可跑。
"""

import pytest
from sqlalchemy import create_engine, text

from agents.visual.db import create_gen_jobs_schema
from agents.visual.db import metadata as jobs_metadata
from agents.visual.loop import VisualLoopError, freeze_round_tree, run_round
from core.tree.db import create_schema
from core.tree.db import metadata as tree_metadata
from core.tree.store import create_tree_store
from tests.integration.conftest import PG_DSN

pytestmark = pytest.mark.integration


class StubVisualPolicy:
    policy_version = "bbccdd112233"

    def __init__(self, clips):
        self._clips = clips

    def plan_clips(self, config):
        return list(self._clips)


CLIPS = [
    {"gen_params": {"style": "史诗", "shots": 2, "seed_tier": 1}},
    {"gen_params": {"style": "纪实", "shots": 1, "seed_tier": 2}},
]


@pytest.fixture(scope="module")
def backend():
    try:
        engine = create_engine(PG_DSN)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        name = "postgresql"
    except Exception:  # noqa: BLE001 - 回退 SQLite 保证本地可跑
        engine = create_engine("sqlite+pysqlite:///:memory:")
        name = "sqlite"
    with engine.begin() as conn:
        tree_metadata.drop_all(conn)
        jobs_metadata.drop_all(conn)
    create_schema(engine)
    create_gen_jobs_schema(engine)
    yield engine, name
    with engine.begin() as conn:
        jobs_metadata.drop_all(conn)
        tree_metadata.drop_all(conn)
    engine.dispose()


@pytest.fixture()
def env(backend, tmp_path, visual_config, mock_gateway):
    from agents.visual.platform.simulated import SimulatedVideoGen
    from core.tree.artifacts import LocalArtifactStore

    engine, _ = backend
    store = create_tree_store(engine)
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    adapter = SimulatedVideoGen(visual_config.simulated_gen)
    result = run_round(
        "v-replay",
        StubVisualPolicy(CLIPS),
        store,
        artifacts,
        adapter,
        mock_gateway,
        engine,
        visual_config,
    )
    return {
        "result": result,
        "store": store,
        "engine": engine,
        "config": visual_config,
        "artifacts": artifacts,
    }


class Test冻结入池:
    def test_冻结校验通过_池接受(self, env):
        """US2 场景 1：冻结校验通过，快照含五评估器版本组合与权重。"""
        from core.replay.pool import SimulatorPool

        tree = freeze_round_tree("v-replay", env["store"], env["engine"])
        snapshot = tree.config_snapshot
        versions = snapshot["evaluator_versions"]
        assert set(versions) == {
            "rule.format_compliance",
            "proxy.aesthetic",
            "proxy.identity_consistency",
            "proxy.flicker",
            "judge.cinematic",
        }
        assert versions["proxy.aesthetic"].startswith("1.0.0+")
        assert snapshot["evaluator_weights"] == env["config"].evaluator_weights
        assert "gen_params" in snapshot["observation_fields"]

        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)  # 冻结校验通过
        assert [t.tree_id for t in pool.trees] == [tree.tree_id]

    def test_GenJob未终态拒绝冻结(self, backend, tmp_path, visual_config, mock_gateway):
        from sqlalchemy import update

        engine, _ = backend
        store = create_tree_store(engine)
        from agents.visual.platform.simulated import SimulatedVideoGen
        from core.tree.artifacts import LocalArtifactStore

        run_round(
            "v-pending",
            StubVisualPolicy(CLIPS[:1]),
            store,
            LocalArtifactStore(tmp_path / "a2"),
            SimulatedVideoGen(visual_config.simulated_gen),
            mock_gateway,
            engine,
            visual_config,
        )
        # 人为把一个任务拨回 generating（模拟未终态）
        from agents.visual.db import visual_gen_jobs

        with engine.begin() as conn:
            conn.execute(
                update(visual_gen_jobs)
                .where(visual_gen_jobs.c.round_id == "v-pending")
                .values(status="generating")
            )
        with pytest.raises(VisualLoopError, match="终态"):
            freeze_round_tree("v-pending", store, engine)


class Test真值回放:
    def test_probe命中得分即冻结常数_零生成(self, env):
        """US2 场景 2：probe 命中真实生成节点，返回冻结得分，生成调用恒 0。"""
        from core.replay.pool import SimulatorPool
        from policies.base import Budget

        tree = freeze_round_tree("v-replay", env["store"], env["engine"])
        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)
        simulator = pool.build(worker_count=2, budget=Budget(max_probes=4), latency_quantum_ms=0)
        assert simulator.budget.max_generation_calls == 0  # 零生成断言

        hit = simulator.probe(tree.root_id, {"style": "史诗", "shots": 2, "seed_tier": 1})
        assert hit.status == "ok"
        node = env["store"].get_node(hit.nodes[0].node_id)
        assert hit.nodes[0].score == node.score  # 逐字节一致（冻结常数）
        assert len(node.eval_breakdown) == 5  # 五评估器明细齐备

    def test_未知走法_UNKNOWN(self, env):
        from core.replay.pool import SimulatorPool
        from policies.base import Budget

        tree = freeze_round_tree("v-replay", env["store"], env["engine"])
        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)
        simulator = pool.build(worker_count=2, budget=Budget(4), latency_quantum_ms=0)
        result = simulator.probe(tree.root_id, {"style": "不存在", "shots": 9})
        assert result.status == "unknown"

    def test_节点只持工件哈希引用_可回取(self, env):
        """US2 场景 3：节点 artifact_hash 内容寻址，工件可从存储回取且可解码。"""

        nodes = env["store"].nodes_of(env["result"].tree_id)
        clip_nodes = [
            n
            for n in nodes
            if n.parent_id is not None and n.status.value == "evaluated" and n.score and n.score > 0
        ]
        assert clip_nodes
        for node in clip_nodes:
            blob = env["artifacts"].get(node.artifact_hash)  # 哈希引用回取
            assert blob.startswith(b"\x00\x00\x00")  # mp4 魔数（ftyp box）
        # 内容寻址去重：同参数重放不产生新对象已由 001/contract 覆盖
