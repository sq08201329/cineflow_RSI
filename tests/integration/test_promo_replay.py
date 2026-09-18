"""宣发树冻结入池与真值回放验证（US2 / T220）。

冻结校验、真值回放得分即冻结常数、生成调用恒 0、τ 报告对接 002 门禁语义。
优先 PG（CINEFLOW_PG_TEST_DSN），不可用退 SQLite 内存库——本地可跑。
"""

import pytest
from sqlalchemy import create_engine, text

from agents.promo.db import create_campaigns_schema
from agents.promo.db import metadata as campaigns_metadata
from agents.promo.loop import PromoLoopError, freeze_round_tree, run_round
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.db import create_schema
from core.tree.db import metadata as tree_metadata
from core.tree.models import NodeStatus
from core.tree.store import create_tree_store
from ops.ingest_metrics import ingest_round
from tests.integration.conftest import PG_DSN

pytestmark = pytest.mark.integration


class StubPromoPolicy:
    policy_version = "a1b2c3d4e5f6"

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _brief(budget=2.0, temperature=0.3):
    return {
        "prompt": "写一句宣发文案",
        "gen_params": {"temperature": temperature},
        "kind": "copy",
        "tags": ["剧情"],
        "budget_usd": budget,
    }


@pytest.fixture(scope="module")
def backend():
    """优先 PG，不可用退 SQLite（树表 + 运营表同库）。"""
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
        campaigns_metadata.drop_all(conn)
    create_schema(engine)
    create_campaigns_schema(engine)
    yield engine, name
    with engine.begin() as conn:
        campaigns_metadata.drop_all(conn)
        tree_metadata.drop_all(conn)
    engine.dispose()


@pytest.fixture()
def env(backend, tmp_path, promo_config, mock_gateway, simulated_platform):
    from core.tree.artifacts import LocalArtifactStore

    engine, _ = backend
    store = create_tree_store(engine)
    result = run_round(
        "r-replay",
        StubPromoPolicy([_brief(2.0), _brief(2.0, temperature=0.7)]),
        store,
        LocalArtifactStore(tmp_path / "artifacts"),
        simulated_platform,
        mock_gateway,
        promo_config,
        engine=engine,
    )
    ingest_round("r-replay", store, simulated_platform, engine, promo_config)
    return {
        "result": result,
        "store": store,
        "engine": engine,
        "config": promo_config,
        "adapter": simulated_platform,
    }


class Test冻结入池:
    def test_冻结校验通过_池接受(self, env):
        """US2 场景 1：完成回流的轮次树冻结校验通过、可入池。"""
        from core.replay.pool import SimulatorPool

        tree = freeze_round_tree("r-replay", env["store"], env["engine"])
        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)  # 不抛异常即通过
        assert [t.tree_id for t in pool.trees] == [env["result"].tree_id]

    def test_未回流完成拒绝冻结(
        self, backend, tmp_path, promo_config, mock_gateway, simulated_platform
    ):
        """delivered 未回流的轮次不得冻结入池。"""
        from core.tree.artifacts import LocalArtifactStore

        engine, _ = backend
        store = create_tree_store(engine)
        run_round(
            "r-unfrozen",
            StubPromoPolicy([_brief(2.0)]),
            store,
            LocalArtifactStore(tmp_path / "a2"),
            simulated_platform,
            mock_gateway,
            promo_config,
            engine=engine,
        )
        with pytest.raises(PromoLoopError, match="回流"):
            freeze_round_tree("r-unfrozen", store, engine)

    def test_快照冻结评估器版本组合与权重(self, env):
        """config_snapshot 含评估器版本组合 + 权重 + 观测白名单（冻结可查）。"""
        tree = freeze_round_tree("r-replay", env["store"], env["engine"])
        snapshot = tree.config_snapshot
        assert snapshot["evaluator_weights"] == env["config"].evaluator_weights
        versions = snapshot["evaluator_versions"]
        assert versions["rule.material_compliance"] == "1.0.0"
        assert versions["proxy.ctr_history"].startswith("1.0.0+")
        assert versions["human.platform_metrics"] == "1.0.0"
        assert "gen_params" in snapshot["observation_fields"]


class Test真值回放:
    def test_probe_命中真实回流节点_得分即冻结常数(self, env):
        """US2 场景 2：probe 命中的得分 = 落盘冻结的平台真值合成，零生成。"""
        from core.replay.pool import SimulatorPool
        from policies.base import Budget

        tree = freeze_round_tree("r-replay", env["store"], env["engine"])
        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)
        simulator = pool.build(worker_count=2, budget=Budget(max_probes=4), latency_quantum_ms=0)
        assert simulator.budget.max_generation_calls == 0  # 生成调用恒 0

        root_id = tree.root_id
        result = simulator.probe(root_id, {"temperature": 0.3})
        assert result.status == "ok"
        assert len(result.nodes) == 1
        # 得分即冻结常数：与树内节点落盘值逐字节一致
        node = env["store"].get_node(result.nodes[0].node_id)
        assert result.nodes[0].score == node.score
        human = node.eval_breakdown["human.platform_metrics@1.0.0"]
        assert "ctr" in human["diagnostics"]

    def test_未知走法_UNKNOWN(self, env):
        from core.replay.pool import SimulatorPool
        from policies.base import Budget

        tree = freeze_round_tree("r-replay", env["store"], env["engine"])
        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)
        simulator = pool.build(worker_count=2, budget=Budget(4), latency_quantum_ms=0)
        result = simulator.probe(tree.root_id, {"temperature": 9.9})
        assert result.status == "unknown"

    def test_线上轨迹与回放轨迹_τ报告对接(self, env):
        """US2 场景 3：复用 002 无偏性门禁语义输出 τ 与判定。"""
        from core.replay.pool import SimulatorPool
        from policies.base import Budget

        tree = freeze_round_tree("r-replay", env["store"], env["engine"])
        nodes = [
            n
            for n in env["store"].nodes_of(tree.tree_id)
            if n.status is NodeStatus.EVALUATED and n.parent_id is not None
        ]
        real_scores = [n.score for n in nodes]

        pool = SimulatorPool(env["store"])
        pool.add_tree(tree)
        simulator = pool.build(worker_count=2, budget=Budget(8), latency_quantum_ms=0)
        replay_scores = []
        for gen_params in ({"temperature": 0.3}, {"temperature": 0.7}):
            hit = simulator.probe(tree.root_id, gen_params)
            replay_scores.extend(n.score for n in hit.nodes if n.score is not None)

        report = verify_unbiasedness(real_scores, replay_scores)
        assert report.verdict == "pass"  # 同一份冻结真值 → τ=1
        assert report.tau == pytest.approx(1.0)


class TestCTR历史汇聚:
    def test_已冻结树的human明细汇聚为CTR历史(self, env):
        """遗留项接线：CTR 评估器历史从冻结树自动汇聚，不再只靠调用方传入。"""
        from agents.promo.loop import collect_ctr_history

        history = collect_ctr_history(env["store"])
        assert len(history) == 2  # 两个 ingested 物料
        record = history[0]
        assert record["kind"] == "copy"
        assert record["tags"] == ["剧情"]
        assert record["impressions"] > 0 and record["clicks"] >= 0

        # 汇聚的历史可驱动 CTR 评估器（分桶命中而非先验回退）
        from agents.promo.evaluators.ctr import CtrHistoryEvaluator
        from core.evaluators.base import ArtifactRef

        ctr = CtrHistoryEvaluator(
            history, ctr_prior=env["config"].ctr_prior, ctr_cap=env["config"].ctr_cap
        )
        result = ctr.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {
                "material": {
                    "content": {},
                    "kind": "copy",
                    "platform": "simulated",
                    "tags": ["剧情"],
                }
            },
        )
        assert result.diagnostics["fallback"] is None  # 命中真实历史桶
