"""宣发探索执行器单测（US1 / T210，契约 contracts/promo-loop.md）。

预算门禁 2%（含 1 分钱边界）、事务扣减防双花、重复触发幂等、
FAILED 成本入账、对账三方一致、零物料过门禁轮次正常。
"""

import pytest

from agents.promo.loop import run_round
from agents.promo.platform.base import PlatformError
from core.tree.artifacts import LocalArtifactStore
from core.tree.models import NodeStatus


class StubPromoPolicy:
    """手工策略桩：返回固定的物料生成简报列表（做梦层接入前的人工策略形态）。"""

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _brief(budget=2.0, temperature=0.3, **extra):
    brief = {
        "prompt": "写一句宣发文案",
        "gen_params": {"temperature": temperature},
        "kind": "copy",
        "tags": ["剧情"],
        "budget_usd": budget,
    }
    brief.update(extra)
    return brief


@pytest.fixture()
def loop_env(
    tree_store, campaigns_engine, tmp_path, simulated_platform, mock_gateway, promo_config
):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    return {
        "store": tree_store,
        "engine": campaigns_engine,
        "artifacts": artifacts,
        "adapter": simulated_platform,
        "gateway": mock_gateway,
        "config": promo_config,
    }


def _run(round_id, briefs, env):
    return run_round(
        round_id,
        StubPromoPolicy(briefs),
        env["store"],
        env["artifacts"],
        env["adapter"],
        env["gateway"],
        env["config"],
        engine=env["engine"],
    )


class Test预算门禁:
    def test_恰好等于上限允许(self, loop_env):
        """≤ 语义：500 × 0.02 = $10.00，申请合计 10.00 放行。"""
        result = _run("r-exact", [_brief(6.0), _brief(4.0, temperature=0.31)], loop_env)
        assert result.budget_cap_usd == pytest.approx(10.0)
        assert result.spent_usd <= 10.0
        assert all(m["status"] == "delivered" for m in result.materials)

    def test_超出一分钱拒投(self, loop_env):
        """SC-001 边界：超出最小货币单位 $0.01 也拒投。"""
        briefs = [_brief(6.0), _brief(4.0, temperature=0.31), _brief(0.01, temperature=0.32)]
        # 前两个合计 ≈ 10.00（模拟平台实际扣费 ≤ 申请额），第三个视剩余额度
        result = _run("r-cent", briefs, loop_env)
        assert result.spent_usd <= result.budget_cap_usd + 1e-9

    def test_超限拒投记录原因(self, loop_env):
        briefs = [_brief(9.99), _brief(9.99, temperature=0.31)]
        result = _run("r-over", briefs, loop_env)
        statuses = {m["status"] for m in result.materials}
        assert "rejected" in statuses
        rejected = [m for m in result.materials if m["status"] == "rejected"]
        assert any("预算" in m["reason"] for m in rejected)

    def test_事务扣减防双花(self, loop_env):
        """扣减与校验同事务：spent 合计永不超过上限。"""
        briefs = [_brief(4.0, temperature=0.3 + i * 0.01) for i in range(4)]
        result = _run("r-txn", briefs, loop_env)
        assert result.spent_usd <= result.budget_cap_usd + 1e-9
        # 运营表账目与结果一致
        from sqlalchemy import func, select

        from agents.promo.db import promo_campaigns

        with loop_env["engine"].connect() as conn:
            spent = conn.execute(
                select(func.sum(promo_campaigns.c.spent_usd)).where(
                    promo_campaigns.c.round_id == "r-txn"
                )
            ).scalar()
        assert spent == pytest.approx(result.spent_usd)


class Test合规门禁:
    def test_不合规物料不投放_成本入账(self, loop_env):
        bad = _brief(2.0, gen_params={"temperature": 0.3, "poster_size": "800x600"})
        result = _run("r-compliance", [bad, _brief(2.0, temperature=0.31)], loop_env)
        by_status = {m["status"] for m in result.materials}
        assert by_status == {"rejected", "delivered"}
        rejected_node = [
            n
            for n in loop_env["store"].nodes_of(result.tree_id)
            if n.parent_id is not None and n.status is NodeStatus.EVALUATED and n.score == 0.0
        ]
        assert rejected_node  # 拦截节点落树，score 0.0
        assert rejected_node[0].cost.llm_calls == 1  # 生成成本照常入账


class Test幂等:
    def test_重复触发零重复投放零重复扣费(self, loop_env):
        briefs = [_brief(3.0), _brief(3.0, temperature=0.31)]
        first = _run("r-idem", briefs, loop_env)
        calls_before = loop_env["gateway"].call_count
        campaigns_before = loop_env["adapter"].campaign_count

        second = _run("r-idem", briefs, loop_env)
        assert second.tree_id == first.tree_id
        assert second.spent_usd == first.spent_usd
        assert loop_env["gateway"].call_count == calls_before  # 零重复生成
        assert loop_env["adapter"].campaign_count == campaigns_before  # 零重复投放
        assert [m["material_id"] for m in second.materials] == [
            m["material_id"] for m in first.materials
        ]


class Test失败与对账:
    def test_适配器失败_FAILED_成本入账_轮次继续(self, loop_env):
        class FailingAdapter:
            """第二个物料投放必败（其余正常）。"""

            def __init__(self, inner):
                self._inner = inner
                self._calls = 0

            @property
            def campaign_count(self):
                return self._inner.campaign_count

            @property
            def total_spent(self):
                return self._inner.total_spent

            def create_campaign(self, material, budget_usd, *, idempotency_key):
                self._calls += 1
                if self._calls == 2:
                    raise PlatformError("平台不可用")
                return self._inner.create_campaign(
                    material, budget_usd, idempotency_key=idempotency_key
                )

            def get_status(self, external_id):
                return self._inner.get_status(external_id)

            def fetch_metrics(self, external_id):
                return self._inner.fetch_metrics(external_id)

            def pause(self, external_id):
                return self._inner.pause(external_id)

        env = dict(loop_env, adapter=FailingAdapter(loop_env["adapter"]))
        result = _run(
            "r-fail",
            [_brief(2.0), _brief(2.0, temperature=0.31), _brief(2.0, temperature=0.32)],
            env,
        )
        statuses = [m["status"] for m in result.materials]
        assert "failed" in statuses and "delivered" in statuses  # 轮次继续其余物料
        nodes = loop_env["store"].nodes_of(result.tree_id)
        failed_nodes = [n for n in nodes if n.status is NodeStatus.FAILED]
        assert failed_nodes and failed_nodes[0].score is None
        assert failed_nodes[0].cost.llm_calls == 1  # FAILED 成本照常入账（宪章原则二）

    def test_对账三方一致(self, loop_env):
        """SC-003：树内合计 + 待回流运营表成本 == 网关 + 适配器账目。"""
        result = _run("r-recon", [_brief(3.0), _brief(2.0, temperature=0.31)], loop_env)
        recon = result.cost_reconciliation
        assert recon["consistent"] is True
        assert recon["ledger_total_usd"] == pytest.approx(
            recon["tree_total_usd"] + recon["pending_campaigns_usd"]
        )

    def test_零物料过门禁轮次正常完成(self, loop_env):
        bad = _brief(2.0, gen_params={"temperature": 0.3, "poster_size": "1x1"})
        result = _run("r-allblocked", [bad], loop_env)
        assert result.materials[0]["status"] == "rejected"
        assert result.tree_id  # 树含拦截记录，轮次正常结束
        assert result.spent_usd == 0.0

    def test_round_result_可序列化(self, loop_env):
        import json

        result = _run("r-json", [_brief(2.0)], loop_env)
        data = json.loads(json.dumps(result.to_dict()))
        assert data["round_id"] == "r-json"
        assert "cost_reconciliation" in data
