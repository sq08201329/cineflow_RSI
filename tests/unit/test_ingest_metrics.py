"""回流管道单测（补充 T218：校验 → 一次性完整节点 INSERT 冻结）。

越界指标拒绝（不写树、运营表记 failed）、正常回流三段明细合成、
重复回流幂等、写入后修改被拒（immutable 冻结）。
"""

import pytest

from agents.promo.loop import run_round
from agents.promo.platform.base import MetricSnapshot
from core.tree.models import NodeStatus
from ops.ingest_metrics import ingest_round


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


@pytest.fixture()
def round_done(
    tree_store, campaigns_engine, tmp_path, simulated_platform, mock_gateway, promo_config
):
    """跑完一轮（全部 delivered），返回 (round_result, 环境 dict)。"""
    from core.tree.artifacts import LocalArtifactStore

    result = run_round(
        "r-ingest",
        StubPromoPolicy([_brief(2.0), _brief(2.0, temperature=0.31)]),
        tree_store,
        LocalArtifactStore(tmp_path / "artifacts"),
        simulated_platform,
        mock_gateway,
        promo_config,
        engine=campaigns_engine,
    )
    return result, {
        "store": tree_store,
        "engine": campaigns_engine,
        "adapter": simulated_platform,
        "config": promo_config,
    }


class Test回流落盘:
    def test_一次性完整节点_INSERT_冻结(self, round_done):
        result, env = round_done
        report = ingest_round(
            "r-ingest", env["store"], env["adapter"], env["engine"], env["config"]
        )
        assert len(report["ingested"]) == 2
        assert report["rejected"] == []

        nodes = [n for n in env["store"].nodes_of(result.tree_id) if n.parent_id is not None]
        assert len(nodes) == 2
        for node in nodes:
            assert node.status is NodeStatus.EVALUATED
            # 三段明细：合规 + CTR + 平台真值（human 锚点）
            assert set(node.eval_breakdown) == {
                "rule.material_compliance@1.0.0",
                next(k for k in node.eval_breakdown if k.startswith("proxy.ctr_history@")),
                "human.platform_metrics@1.0.0",
            }
            assert "human.platform_metrics@1.0.0" in node.eval_breakdown
            human = node.eval_breakdown["human.platform_metrics@1.0.0"]
            assert "ctr" in human["diagnostics"]  # 原始指标保留
            assert 0.0 <= node.score <= 1.0
            assert node.cost.generation_api_cost_usd > 0  # 生成+投放成本完整

    def test_写入即冻结_后续修改被拒(self, round_done):
        result, env = round_done
        ingest_round("r-ingest", env["store"], env["adapter"], env["engine"], env["config"])
        from sqlalchemy import update

        from core.tree.db import tree_nodes

        with pytest.raises(Exception, match="immutable"):
            with env["store"]._engine.begin() as conn:  # SQLite 触发器直验
                conn.execute(
                    update(tree_nodes)
                    .where(tree_nodes.c.tree_id == result.tree_id)
                    .values(score=0.0)
                )

    def test_重复回流幂等(self, round_done):
        _, env = round_done
        first = ingest_round("r-ingest", env["store"], env["adapter"], env["engine"], env["config"])
        second = ingest_round(
            "r-ingest", env["store"], env["adapter"], env["engine"], env["config"]
        )
        assert len(first["ingested"]) == 2
        assert second["ingested"] == []  # 已 ingested 的行不再处理
        assert second["rejected"] == []

    def test_越界指标拒绝_不写树(
        self, tree_store, campaigns_engine, tmp_path, promo_config, mock_gateway
    ):
        """FR-006：平台返回 CTR>1 → 校验拒绝、运营表记 failed、告警入报告。"""
        from core.tree.artifacts import LocalArtifactStore

        class BadMetricsAdapter:
            def __init__(self, inner):
                self._inner = inner

            @property
            def campaign_count(self):
                return self._inner.campaign_count

            @property
            def total_spent(self):
                return self._inner.total_spent

            def create_campaign(self, material, budget_usd, *, idempotency_key):
                return self._inner.create_campaign(
                    material, budget_usd, idempotency_key=idempotency_key
                )

            def get_status(self, external_id):
                return self._inner.get_status(external_id)

            def fetch_metrics(self, external_id):
                return MetricSnapshot(
                    ctr=1.5,  # 越界
                    completion_rate=0.5,
                    conversions=1,
                    impressions=100,
                    clicks=150,
                    platform_timestamp=0.0,
                    data_version="bad",
                )

            def pause(self, external_id):
                return self._inner.pause(external_id)

        adapter = BadMetricsAdapter(
            __import__(
                "agents.promo.platform.simulated", fromlist=["SimulatedPlatform"]
            ).SimulatedPlatform(promo_config.simulated_platform)
        )
        result = run_round(
            "r-badmetrics",
            StubPromoPolicy([_brief(2.0)]),
            tree_store,
            LocalArtifactStore(tmp_path / "artifacts"),
            adapter,
            mock_gateway,
            promo_config,
            engine=campaigns_engine,
        )
        report = ingest_round("r-badmetrics", tree_store, adapter, campaigns_engine, promo_config)
        assert report["ingested"] == []
        assert len(report["rejected"]) == 1
        assert "越界" in report["rejected"][0]["reason"]
        # 树上只有锚点根节点（越界指标未写入）
        assert len(tree_store.nodes_of(result.tree_id)) == 1
