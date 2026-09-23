"""开发 Agent 轮次循环单测（功能 017 / US1，SQLite 内存库）。

契约 C11~C13：输入预检先于一切副作用（缺题材边界/受众 → 拒绝且 0 落库）、计划执行前校验
（缺结构标记 → 0 网关调用 0 成本）、单一产出落树与成本对账、幂等重建（0 重复生成/扣费/节点）、
网关失败成本照计（原则二）、`slate_match_key` 只含策略可复现结构键（不含生成产物摘要）、
配置快照冻结权重与阈值、评估器必须可注入（`None` → 真实装配尚未落地即明确报错，不静默降级）。
真实 PostgreSQL 侧的字段/约束/两段式断言见 tests/integration/test_dev_loop.py。
"""

import json

import blake3
import pytest
from sqlalchemy import func, select

from agents.dev.artifact import TopicSlate
from agents.dev.db import dev_jobs
from agents.dev.loop import (
    AGENT_ID,
    DevLoopError,
    round_tree_id,
    run_dev_round,
    slate_match_key,
)
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway, PermanentBackendError
from core.tree.artifacts import LocalArtifactStore
from core.tree.db import create_schema
from core.tree.models import NodeStatus
from core.tree.store import create_tree_store

INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}


@pytest.fixture()
def dev_round_engine(dev_jobs_engine):
    """轮次引擎：dev_jobs 运营表 + 发现树表同库（内存 SQLite，含 immutable 触发器）。"""
    create_schema(dev_jobs_engine)
    return dev_jobs_engine


@pytest.fixture()
def dev_gateway(dev_config):
    """确定性 Mock 网关 + dev 形态价目（sleep 注入以消除真实退避）。"""
    return LLMGateway(MockBackend(), price_book=dev_config.model_prices, sleep=lambda _: None)


@pytest.fixture()
def dev_artifacts(dev_data_dir):
    return LocalArtifactStore(dev_data_dir / "artifacts")


def _policy_of(source_text: str, version: str):
    namespace: dict = {"__name__": "dev_unit_policy"}
    exec(compile(source_text, "<dev-unit-policy>", "exec"), namespace)  # noqa: S102 - 夹具源码
    policy = namespace["Policy"]()
    policy.policy_version = version
    return policy


def _run(
    dev_round_engine,
    dev_artifacts,
    dev_gateway,
    config,
    policy_source,
    *,
    round_id="unit-r1",
    inputs=INPUTS,
    evaluators=None,
    version="unit-v1",
):
    store = create_tree_store(dev_round_engine)
    return run_dev_round(
        round_id=round_id,
        policy=_policy_of(policy_source, version),
        store=store,
        artifacts=dev_artifacts,
        engine=dev_round_engine,
        gateway=dev_gateway,
        config=config,
        inputs=inputs,
        evaluators=evaluators,
    )


def _product_node(store, tree_id):
    return next(item for item in store.nodes_of(tree_id) if item.parent_id is not None)


class Test匹配键只含结构键:
    def test_键集锁定(self, dev_config):
        key = slate_match_key(policy_version="unit-v1", inputs=INPUTS, config=dev_config)
        assert set(key) == {
            "policy_version",
            "model",
            "temperature",
            "max_tokens",
            "constraint_digest",
            "slate_range",
        }
        # 不含生成产物摘要（产物一次性、不可复现，入键即命中率归零，007 教训）
        for forbidden in ("prompt_digest", "response_hash", "artifact_hash"):
            assert forbidden not in key

    def test_同输入同键且约束摘要可复算(self, dev_config):
        first = slate_match_key(policy_version="unit-v1", inputs=INPUTS, config=dev_config)
        second = slate_match_key(policy_version="unit-v1", inputs=dict(INPUTS), config=dev_config)
        assert first == second
        assert (
            first["constraint_digest"]
            == blake3.blake3(
                json.dumps(INPUTS, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        )
        assert first["slate_range"] == list(dev_config.slate_entries)
        assert first["temperature"] == 0.0

    def test_策略版本变更即新键(self, dev_config):
        first = slate_match_key(policy_version="unit-v1", inputs=INPUTS, config=dev_config)
        assert first != slate_match_key(policy_version="unit-v2", inputs=INPUTS, config=dev_config)


class Test执行前拒绝:
    def test_缺输入即拒绝且零副作用(
        self,
        dev_round_engine,
        dev_artifacts,
        dev_gateway,
        dev_config,
        dev_policy_source,
        dev_stub_evaluators,
    ):
        store = create_tree_store(dev_round_engine)
        for inputs in (
            {},
            {"genre_bounds": ["悬疑"]},
            {"audience": "都市女性"},
            {"genre_bounds": []},
        ):
            with pytest.raises(DevLoopError):
                _run(
                    dev_round_engine,
                    dev_artifacts,
                    dev_gateway,
                    dev_config,
                    dev_policy_source("compliant"),
                    round_id="unit-bad-inputs",
                    inputs=inputs,
                    evaluators=dev_stub_evaluators,
                )
        assert store.trees_by(agent_id=AGENT_ID) == []
        assert dev_gateway.call_count == 0

    def test_计划缺结构标记即拒绝零成本(
        self, dev_round_engine, dev_artifacts, dev_gateway, dev_config, dev_stub_evaluators
    ):
        """缺条目/条目缺方向标识 → 执行前拒绝：0 网关调用 0 成本，原因如实落节点。"""
        store = create_tree_store(dev_round_engine)
        for source in (
            "class Policy:\n    def plan(self, inputs, config):\n        return {}\n",
            'class Policy:\n    def plan(self, inputs, config):\n        return {"entries": []}\n',
            "class Policy:\n    def plan(self, inputs, config):\n"
            '        return {"entries": [{"genre": "悬疑"}]}\n',
        ):
            result = _run(
                dev_round_engine,
                dev_artifacts,
                dev_gateway,
                dev_config,
                source,
                round_id=f"unit-reject-{len(source)}",
                evaluators=dev_stub_evaluators,
            )
            assert result.job["status"] == "rejected"
            assert "执行前拒绝" in result.job["reason"]
            assert result.spent_usd == 0.0
            node = _product_node(store, result.tree_id)
            assert node.status is NodeStatus.EVALUATED
            assert node.score == 0.0 and node.eval_breakdown == {}
            assert node.artifact_hash == "00" * 32
            assert node.observation_context["reject_reason"] == result.job["reason"]
            with dev_round_engine.connect() as conn:
                rows = conn.execute(
                    select(func.count())
                    .select_from(dev_jobs)
                    .where(dev_jobs.c.round_id == result.round_id)
                ).scalar()
            assert rows == 0  # 拒绝轮次不占运营表行（无成本可计）
        assert dev_gateway.call_count == 0

    def test_评估器装配未落地即明确报错(
        self, dev_round_engine, dev_artifacts, dev_gateway, dev_config, dev_policy_source
    ):
        """US2 尚未落地：真实装配不可用即报错（不静默降级为"无评估器"）。"""
        with pytest.raises(DevLoopError, match="尚未落地"):
            _run(
                dev_round_engine,
                dev_artifacts,
                dev_gateway,
                dev_config,
                dev_policy_source("compliant"),
                round_id="unit-no-evaluators",
                evaluators=None,
            )
        with pytest.raises(DevLoopError, match="不能为空列表"):
            _run(
                dev_round_engine,
                dev_artifacts,
                dev_gateway,
                dev_config,
                dev_policy_source("compliant"),
                round_id="unit-empty-evaluators",
                evaluators=[],
            )


class Test产出落树与成本:
    def test_合规产出(
        self,
        dev_round_engine,
        dev_artifacts,
        dev_gateway,
        dev_config,
        dev_policy_source,
        dev_stub_evaluators,
    ):
        store = create_tree_store(dev_round_engine)
        result = _run(
            dev_round_engine,
            dev_artifacts,
            dev_gateway,
            dev_config,
            dev_policy_source("compliant"),
            evaluators=dev_stub_evaluators,
        )
        assert result.job["status"] == "inserted"
        assert result.tree_id == round_tree_id("unit-r1")
        # 逐条目一次生成（6 条 ∈ movie 区间 [3, 6]）
        assert dev_gateway.call_count == dev_config.slate_entries[1]

        node = _product_node(store, result.tree_id)
        assert node.status is NodeStatus.EVALUATED
        assert node.policy_version == "unit-v1"
        assert node.score == 0.6  # 桩代理 0.6 → 加权归一点定
        assert {key.rsplit("@", 1)[0] for key in node.eval_breakdown} == set(
            dev_config.evaluator_weights
        )
        # 观测：回放匹配槽（仅结构键）+ 网关核对键 + 逐调用明细
        assert node.observation_context["gen_params"] == slate_match_key(
            policy_version="unit-v1", inputs=INPUTS, config=dev_config
        )
        assert len(node.observation_context["calls"]) == dev_config.slate_entries[1]
        calls = node.observation_context["calls"]
        assert all(call["cache_key"] and call["response_hash"] for call in calls)
        assert node.observation_context["cache_key"] == result.job["cache_key"]
        assert node.observation_context["response_hash"] == result.job["response_hash"]
        assert node.cost.generation_api_cost_usd == pytest.approx(result.spent_usd)
        assert node.cost.llm_calls == dev_config.slate_entries[1]

        # 工件内容寻址：哈希 = canonical JSON 的 BLAKE3，落盘可读回且标注可机读
        slate = TopicSlate.from_dict(json.loads(dev_artifacts.get(node.artifact_hash)))
        assert node.artifact_hash == slate.slate_hash() == result.job["artifact_hash"]
        assert slate.slate_hash() == blake3.blake3(slate.canonical_json().encode()).hexdigest()
        assert len(slate.direction_ids()) == dev_config.slate_entries[1]
        marks_interval = dev_config.production_marks
        assert marks_interval[0] <= len(slate.produce_ids()) <= marks_interval[1]
        assert all(entry.rationale for entry in slate.entries)  # 论证要点 = 网关正文
        assert all(source["simulated"] for source in slate.to_dict()["signal_sources"])

        # 成本对账：树内 == 运营表 + 评估器增量（评估器增量字段必落，不省略）
        reconciliation = result.cost_reconciliation
        assert reconciliation["consistent"] is True
        assert reconciliation["evaluator_cost_usd"] == 0.0
        assert result.spent_usd == pytest.approx(reconciliation["ledger_total_usd"])
        with dev_round_engine.connect() as conn:
            row = conn.execute(select(dev_jobs).where(dev_jobs.c.round_id == "unit-r1")).one()
        assert row.status == "inserted"
        assert row.policy_version == "unit-v1"
        assert row.actual_cost_usd <= row.estimated_cost_usd

    def test_配置快照冻结权重与阈值(
        self,
        dev_round_engine,
        dev_artifacts,
        dev_gateway,
        dev_config,
        dev_policy_source,
        dev_stub_evaluators,
    ):
        store = create_tree_store(dev_round_engine)
        _run(
            dev_round_engine,
            dev_artifacts,
            dev_gateway,
            dev_config,
            dev_policy_source("compliant"),
            evaluators=dev_stub_evaluators,
        )
        tree = store.trees_by(agent_id=AGENT_ID)[0]
        snapshot = tree.config_snapshot
        assert snapshot["evaluator_weights"] == dev_config.evaluator_weights
        assert snapshot["upgrade_criteria"] == dev_config.upgrade_criteria
        assert snapshot["signals"] == dev_config.signals
        assert snapshot["model"] == dev_config.model
        assert snapshot["max_tokens"] == dev_config.max_tokens
        assert snapshot["min_comparable_trees"] == dev_config.min_comparable_trees
        assert snapshot["production_marks"] == {
            "min": dev_config.production_marks[0],
            "max": dev_config.production_marks[1],
        }

    def test_幂等重建零重复(
        self,
        dev_round_engine,
        dev_artifacts,
        dev_gateway,
        dev_config,
        dev_policy_source,
        dev_stub_evaluators,
    ):
        store = create_tree_store(dev_round_engine)
        first = _run(
            dev_round_engine,
            dev_artifacts,
            dev_gateway,
            dev_config,
            dev_policy_source("compliant"),
            evaluators=dev_stub_evaluators,
        )
        calls = dev_gateway.call_count
        second = _run(
            dev_round_engine,
            dev_artifacts,
            dev_gateway,
            dev_config,
            dev_policy_source("compliant"),
            evaluators=dev_stub_evaluators,
        )
        assert second.job == first.job
        assert dev_gateway.call_count == calls  # 0 重复生成（撞树锚点即重建）
        assert second.spent_usd == first.spent_usd  # 0 重复扣费
        assert len(store.nodes_of(first.tree_id)) == 2  # 0 重复节点
        assert second.cost_reconciliation == {}  # 重建路径对账字段留空（不伪造"一致"）


class Test网关失败成本照计:
    def test_失败即_failed_节点且成本照计(
        self,
        dev_round_engine,
        dev_artifacts,
        dev_config,
        dev_policy_source,
        dev_stub_evaluators,
    ):
        class _FailingBackend:
            def __init__(self, inner) -> None:
                self.call_count = 0
                self._inner = inner

            def complete(self, prompt, *, model, temperature, max_tokens):
                self.call_count += 1
                if "dir-return" in prompt:
                    raise PermanentBackendError("后端 4xx（单测注入）")
                return self._inner.complete(
                    prompt, model=model, temperature=temperature, max_tokens=max_tokens
                )

        gateway = LLMGateway(
            _FailingBackend(MockBackend()),
            price_book=dev_config.model_prices,
            sleep=lambda _: None,
        )
        store = create_tree_store(dev_round_engine)
        result = run_dev_round(
            round_id="unit-fail",
            policy=_policy_of(dev_policy_source("compliant"), "unit-v1"),
            store=store,
            artifacts=dev_artifacts,
            engine=dev_round_engine,
            gateway=gateway,
            config=dev_config,
            inputs=INPUTS,
            evaluators=dev_stub_evaluators,
        )
        assert result.job["status"] == "failed"
        assert "网关失败" in result.job["reason"]
        node = _product_node(store, result.tree_id)
        assert node.status is NodeStatus.FAILED
        assert node.score is None and node.eval_breakdown == {}
        with dev_round_engine.connect() as conn:
            row = conn.execute(select(dev_jobs).where(dev_jobs.c.round_id == "unit-fail")).one()
        assert row.status == "failed" and row.error and "网关失败" in row.error
        assert row.actual_cost_usd == pytest.approx(row.estimated_cost_usd)  # 失败照计（原则二）
        assert node.cost.generation_api_cost_usd == pytest.approx(row.estimated_cost_usd)
        assert result.cost_reconciliation["consistent"] is True
        # 幂等重建：失败轮次二次触发同样命中锚点（0 重复失败行）
        again = run_dev_round(
            round_id="unit-fail",
            policy=_policy_of(dev_policy_source("compliant"), "unit-v1"),
            store=store,
            artifacts=dev_artifacts,
            engine=dev_round_engine,
            gateway=gateway,
            config=dev_config,
            inputs=INPUTS,
            evaluators=dev_stub_evaluators,
        )
        assert again.job == result.job and again.cost_reconciliation == {}
