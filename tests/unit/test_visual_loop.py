"""视觉探索执行器单测（US1 / T315，契约 contracts/visual-loop.md）。

预算门禁含边界、幂等零重复、单评估器崩溃隔离、三方对账、
合规 0 分短路不跑 judge（省 LLM 成本）。
"""

import pytest

from agents.visual.loop import run_round
from agents.visual.platform.base import VideoGenError
from core.tree.artifacts import LocalArtifactStore
from core.tree.models import NodeStatus


class StubVisualPolicy:
    """手工视觉策略桩：返回固定 gen_params 组合。"""

    policy_version = "bbccdd112233"

    def __init__(self, clips):
        self._clips = clips

    def plan_clips(self, config):
        return list(self._clips)


def _clip(seed_tier=1, style="史诗", shots=2):
    return {"gen_params": {"style": style, "shots": shots, "seed_tier": seed_tier}}


@pytest.fixture()
def loop_env(tree_store, gen_jobs_engine, tmp_path, visual_config, mock_gateway):
    from agents.visual.platform.simulated import SimulatedVideoGen

    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    adapter = SimulatedVideoGen(visual_config.simulated_gen)
    return {
        "store": tree_store,
        "engine": gen_jobs_engine,
        "artifacts": artifacts,
        "adapter": adapter,
        "gateway": mock_gateway,
        "config": visual_config,
    }


def _run(round_id, clips, env):
    return run_round(
        round_id,
        StubVisualPolicy(clips),
        env["store"],
        env["artifacts"],
        env["adapter"],
        env["gateway"],
        env["engine"],
        env["config"],
    )


class Test预算门禁:
    def test_恰好等于上限允许(self, loop_env):
        """≤ 语义：上限恰为 2×预估（1.2 = 2×0.6）→ 前两片放行、第三片拒投。"""
        from dataclasses import replace

        config = replace(loop_env["config"], exploration_per_round_usd=1.2)
        env = dict(loop_env, config=config)
        result = _run("v-exact", [_clip(1), _clip(2), _clip(3)], env)
        assert result.budget_cap_usd == pytest.approx(1.2)
        statuses = [c["status"] for c in result.clips]
        assert statuses == ["ingested", "ingested", "rejected"]
        assert result.spent_usd <= result.budget_cap_usd + 1e-9

    def test_超限拒绝并记录原因(self, loop_env):
        from dataclasses import replace

        config = replace(loop_env["config"], exploration_per_round_usd=1.0)
        env = dict(loop_env, config=config)
        # 上限 $1.0 < 单片预估 $0.6 × 2 → 第二片拒投
        result = _run("v-over", [_clip(1), _clip(2)], env)
        statuses = {c["status"] for c in result.clips}
        assert "rejected" in statuses
        rejected = [c for c in result.clips if c["status"] == "rejected"]
        assert any("预算" in c["reason"] for c in rejected)

    def test_运营表账目与结果一致(self, loop_env):
        from sqlalchemy import func, select

        from agents.visual.db import visual_gen_jobs

        result = _run("v-txn", [_clip(1), _clip(2)], loop_env)
        with loop_env["engine"].connect() as conn:
            spent = conn.execute(
                select(func.sum(visual_gen_jobs.c.cost_usd)).where(
                    visual_gen_jobs.c.round_id == "v-txn"
                )
            ).scalar()
        assert spent == pytest.approx(result.spent_usd)


class Test幂等:
    def test_重复触发零重复生成零重复扣费(self, loop_env):
        clips = [_clip(1), _clip(2)]
        first = _run("v-idem", clips, loop_env)
        gateway_calls = loop_env["gateway"].call_count
        jobs_before = loop_env["adapter"].job_count

        second = _run("v-idem", clips, loop_env)
        assert second.tree_id == first.tree_id
        assert second.spent_usd == first.spent_usd
        assert loop_env["gateway"].call_count == gateway_calls  # 零重复 judge 调用
        assert loop_env["adapter"].job_count == jobs_before  # 零重复生成
        assert [c["clip_id"] for c in second.clips] == [c["clip_id"] for c in first.clips]


class Test崩溃隔离:
    def test_单评估器崩溃_节点FAILED_轮次继续(self, loop_env, monkeypatch):
        """SC-006：任一评估器崩溃 → 该节点 FAILED（成本入账），其余照常。"""
        from agents.visual.evaluators import aesthetic

        original = aesthetic.AestheticEvaluator.evaluate

        def crash_on_second(self, artifact, context):
            if context["gen_params"].get("seed_tier") == 2:
                raise RuntimeError("评估器崩溃模拟")
            return original(self, artifact, context)

        monkeypatch.setattr(aesthetic.AestheticEvaluator, "evaluate", crash_on_second)
        result = _run("v-crash", [_clip(1), _clip(2), _clip(3)], loop_env)
        statuses = [c["status"] for c in result.clips]
        assert statuses.count("failed") == 1
        assert statuses.count("ingested") == 2
        nodes = loop_env["store"].nodes_of(result.tree_id)
        failed = [n for n in nodes if n.status is NodeStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].score is None
        assert failed[0].cost.generation_api_cost_usd > 0  # 生成成本照常入账


class Test门禁短路与对账:
    def test_合规零分_不调用judge(self, loop_env, monkeypatch):
        """FR 门禁语义：合规 0 → 合成 0 且 judge 未被调用（省 LLM 成本）。"""
        # 构造不合规片段：让模拟生成器产出后 probe_meta 不符规格——
        # 通过让策略申请与 clip_spec 不同的分辨率实现
        clips = [
            {
                "gen_params": {
                    "style": "史诗",
                    "shots": 2,
                    "seed_tier": 1,
                    "width": 640,
                    "height": 480,
                }
            }
        ]
        gateway_calls_before = loop_env["gateway"].call_count
        result = _run("v-gate", clips, loop_env)
        node = [n for n in loop_env["store"].nodes_of(result.tree_id) if n.parent_id is not None][0]
        assert node.score == 0.0
        assert node.eval_breakdown["rule.format_compliance@1.0.0"]["score"] == 0.0
        assert not any(k.startswith("judge.cinematic") for k in node.eval_breakdown)
        assert loop_env["gateway"].call_count == gateway_calls_before  # judge 零调用

    def test_三方对账一致(self, loop_env):
        result = _run("v-recon", [_clip(1), _clip(2)], loop_env)
        recon = result.cost_reconciliation
        assert recon["consistent"] is True
        assert recon["tree_total_usd"] == pytest.approx(recon["ledger_total_usd"])

    def test_零片段轮次正常完成(self, loop_env):
        result = _run("v-empty", [], loop_env)
        assert result.clips == []
        assert result.tree_id
        assert result.spent_usd == 0.0

    def test_round_result_可序列化(self, loop_env):
        import json

        result = _run("v-json", [_clip(1)], loop_env)
        data = json.loads(json.dumps(result.to_dict()))
        assert data["round_id"] == "v-json"
        assert data["clips"][0]["status"] == "ingested"


class Test适配器失败:
    def test_生成失败_FAILED_轮次继续(self, loop_env):
        class FailingAdapter:
            def __init__(self, inner):
                self._inner = inner
                self._calls = 0

            @property
            def job_count(self):
                return self._inner.job_count

            @property
            def total_spent(self):
                return self._inner.total_spent

            def job_actual_cost(self, external_id):
                return self._inner.job_actual_cost(external_id)

            def submit(self, gen_params, *, idempotency_key):
                self._calls += 1
                if self._calls == 2:
                    raise VideoGenError("生成服务故障")
                return self._inner.submit(gen_params, idempotency_key=idempotency_key)

            def get_status(self, external_id):
                return self._inner.get_status(external_id)

            def fetch_artifact(self, external_id):
                return self._inner.fetch_artifact(external_id)

            def cancel(self, external_id):
                return self._inner.cancel(external_id)

        env = dict(loop_env, adapter=FailingAdapter(loop_env["adapter"]))
        result = _run("v-fail", [_clip(1), _clip(2), _clip(3)], env)
        statuses = [c["status"] for c in result.clips]
        assert "failed" in statuses and "ingested" in statuses
