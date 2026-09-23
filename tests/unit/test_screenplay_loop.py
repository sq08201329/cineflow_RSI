"""剧本分阶段产出执行器单测（功能 009 / T913，先于实现编写）。

C1 场景 1~4：
① 一轮三阶段（outline → scenes → script）→ 3 个节点，stage 齐备、policy_version =
   人工策略版本、工件内容寻址可回读、成本按 token 入账（网关价目）；
② 同 round_id 二次触发幂等重建（唯一键 (round_id, stage, params_hash)）——0 重复生成
   0 重复扣费 0 重复节点/行；
③ 网关失败 → FAILED 节点 + 成本照计（原则二）+ 运营表 failed + 轮次继续；
④ 节点可回溯策略版本（人工版本同样版本化）。
另断言网关缓存键与响应哈希落盘（回放核对依据）、分阶段输入脉络（前一阶段工件哈希进
后一阶段参数）、执行前拒绝（策略计划非法 → 0 网关调用 0 成本）。
评估器桩注入（loop 面向评估器协议编程；真实七评估器在 T923 接线）。
"""

import json

import blake3
import pytest
from sqlalchemy import select

from agents.screenplay.artifact import STAGES, ScriptArtifact
from agents.screenplay.db import screenplay_jobs
from agents.screenplay.loop import (
    ScreenplayLoopError,
    round_tree_id,
    run_screenplay_round,
    stage_cache_key,
)
from core.evaluators.base import EvalResult
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import BackendResult, LLMGateway, PermanentBackendError
from core.tree.models import NodeStatus
from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

INPUTS = {
    "topic": "病房里的三个月",
    "target_duration_min": 90,
    "constraints": ["单场景为主", "不出现旁白"],
    "characters": ["林静", "陈默", "周医生"],
}
_GATE_IDS = (
    "rule.beat_structure",
    "rule.page_minutes",
    "rule.scene_character",
    "rule.dialogue_action_ratio",
)


class _StubPolicy:
    """人工策略桩：产分阶段计划（节拍/场景/角色/行标记），版本号形如 BLAKE3 前 12 位。"""

    policy_version = "9f2c41ab77de"

    def __init__(self, plans):
        self._plans = plans

    def plan(self, inputs, config):
        import copy

        return copy.deepcopy(self._plans)


def _plan_of(artifact: ScriptArtifact) -> dict:
    payload = artifact.to_dict()
    return {key: payload[key] for key in ("beats", "scenes", "characters", "lines")}


def _plans(make_script_artifacts) -> dict:
    """三阶段计划：三段工件派生（结构化标记与工件同 schema）。"""
    return {stage: _plan_of(artifact) for stage, artifact in make_script_artifacts().items()}


def _stubs():
    """评估器桩七件套（四 gate + 两 proxy + judge；权重键取 config 内键名）。

    期望合成 = (0.5×0.8 + 0.5×0.6 + 0.5×0.7) / 1.5 = 0.7（gate 不参与加权）。
    """
    return [
        *(StubRuleEvaluator(gate_id) for gate_id in _GATE_IDS),
        StubProxyEvaluator("proxy.entity_consistency", score=0.8),
        StubProxyEvaluator("proxy.timeline_conflict", score=0.6),
        StubJudgeEvaluator("judge.dramatic_tension", score=0.7),
    ]


@pytest.fixture()
def config(screenplay_config):
    return screenplay_config


@pytest.fixture()
def gateway(config):
    """Mock 网关：价目取自 ScreenplayConfig.model_prices；退避置零加速测试。"""
    return LLMGateway(MockBackend(), price_book=config.model_prices, sleep=lambda _: None)


@pytest.fixture()
def policy(make_script_artifacts):
    return _StubPolicy(_plans(make_script_artifacts))


_DEFAULT_STUBS = object()


def _run(
    round_id,
    policy,
    tree_store,
    artifact_store,
    screenplay_jobs_engine,
    gateway,
    config,
    inputs=None,
    evaluators=_DEFAULT_STUBS,
):
    return run_screenplay_round(
        round_id=round_id,
        policy=policy,
        store=tree_store,
        artifacts=artifact_store,
        engine=screenplay_jobs_engine,
        gateway=gateway,
        config=config,
        inputs=dict(INPUTS) if inputs is None else inputs,
        evaluators=_stubs() if evaluators is _DEFAULT_STUBS else evaluators,
    )


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(screenplay_jobs)).all()


def _stage_nodes(store, tree_id):
    return [node for node in store.nodes_of(tree_id) if node.parent_id is not None]


def _node_by_stage(store, tree_id):
    return {node.observation_context["stage"]: node for node in _stage_nodes(store, tree_id)}


def _artifact_of(artifact_store, node) -> ScriptArtifact:
    return ScriptArtifact.from_dict(json.loads(artifact_store.get(node.artifact_hash)))


class Test一轮三阶段落树:
    def test_三阶段节点齐备且成本入账(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        result = _run(
            "r1", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert [job["stage"] for job in result.jobs] == list(STAGES)
        assert [job["status"] for job in result.jobs] == ["inserted"] * 3
        # 树：根 + 3 个已评估阶段节点（工件内容寻址可回读）
        nodes = _stage_nodes(tree_store, result.tree_id)
        assert len(nodes) == 3
        # 按 stage 取节点（节点落盘顺序不作断言：同轮次节点以 stage 标识，不依赖 created_at 排序）
        by_stage = _node_by_stage(tree_store, result.tree_id)
        for job in result.jobs:
            node = by_stage[job["stage"]]
            assert node.status is NodeStatus.EVALUATED
            artifact = _artifact_of(artifact_store, node)
            assert artifact.stage == job["stage"]
            assert artifact.artifact_hash() == node.artifact_hash
            assert artifact.text.startswith(f"[{config.model}] ")  # 网关正文即工件正文
            assert len(node.eval_breakdown) == 7  # 四 gate + 两 proxy + judge
            assert node.score == pytest.approx(0.7)
            assert node.cost.llm_calls == 1
            assert node.cost.llm_tokens > 0
            assert node.cost.generation_api_cost_usd > 0
        # 运营表：三阶段终态 inserted，成本 = 网关折算（实际 ≤ 预估）
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        assert set(rows) == set(STAGES)
        assert {row.status for row in rows.values()} == {"inserted"}
        assert result.spent_usd == pytest.approx(sum(row.actual_cost_usd for row in rows.values()))
        assert result.spent_usd > 0
        by_stage = _node_by_stage(tree_store, result.tree_id)
        for row in rows.values():
            assert 0 < row.actual_cost_usd <= row.estimated_cost_usd
            assert row.artifact_hash == by_stage[row.stage].artifact_hash
        assert result.cost_reconciliation["consistent"] is True

    def test_同_round_id_二次触发幂等重建(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        first = _run(
            "r1b", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        calls_after_first = gateway.call_count
        second = _run(
            "r1b", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert second.jobs == first.jobs
        assert second.tree_id == first.tree_id
        assert second.spent_usd == pytest.approx(first.spent_usd)
        assert gateway.call_count == calls_after_first  # 0 重复生成
        assert len(tree_store.nodes_of(first.tree_id)) == 4  # 0 重复节点
        assert len(_rows(screenplay_jobs_engine)) == 3  # 0 重复行

    def test_快照记录权重与评估器版本(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        result = _run(
            "r1c", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        tree = next(
            t for t in tree_store.trees_by(agent_id="screenplay") if t.tree_id == result.tree_id
        )
        snapshot = tree.config_snapshot
        assert snapshot["evaluator_weights"] == config.evaluator_weights
        assert set(snapshot["evaluator_versions"]) == {
            "rule.beat_structure",
            "rule.page_minutes",
            "rule.scene_character",
            "rule.dialogue_action_ratio",
            "proxy.entity_consistency",
            "proxy.timeline_conflict",
            "judge.dramatic_tension",
        }
        assert "gen_params" in snapshot["observation_fields"]  # 回放匹配槽
        assert snapshot["model"] == config.model
        assert snapshot["lines_per_page"] == config.lines_per_page
        assert snapshot["upgrade_criteria"] == config.upgrade_criteria
        assert len(snapshot["anchor_outlines"]) == len(config.anchor_outlines)


class Test分阶段输入脉络:
    def test_输入脉络与回放匹配槽(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        result = _run(
            "r2", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        nodes = _node_by_stage(tree_store, result.tree_id)
        outline_context = nodes["outline"].observation_context
        # 回放匹配槽 = 策略可复现结构键（不含生成产物摘要：回放无需生成即可匹配）
        assert outline_context["gen_params"]["stage"] == "outline"
        assert set(outline_context["gen_params"]) == {
            "stage",
            "policy_version",
            "model",
            "temperature",
            "max_tokens",
            "target_duration_min",
            "plan_digest",
        }
        # 生成产物摘要另存观测（输入脉络审计）：outline 无上游、scenes/script 逐级承接
        assert outline_context["previous_artifact_hash"] is None
        assert nodes["scenes"].observation_context["previous_artifact_hash"] == (
            nodes["outline"].artifact_hash
        )
        assert nodes["script"].observation_context["previous_artifact_hash"] == (
            nodes["scenes"].artifact_hash
        )
        assert nodes["outline"].observation_context["prompt_digest"]
        assert nodes["outline"].prompt != nodes["script"].prompt
        # 三阶段工件互异（stage 与正文都不同）
        hashes = {node.artifact_hash for node in nodes.values()}
        assert len(hashes) == 3


class Test网关缓存键与响应哈希:
    def test_缓存键与响应哈希落盘且可复核(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """回放核对依据：缓存键 = 模型 + 提示词 + 采样参数；响应哈希 = 响应正文哈希。"""
        result = _run(
            "r3", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        nodes = _node_by_stage(tree_store, result.tree_id)
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        for stage in STAGES:
            row = rows[stage]
            context = nodes[stage].observation_context
            assert row.cache_key == context["cache_key"]
            assert row.response_hash == context["response_hash"]
            assert len(row.cache_key) == 64 and len(row.response_hash) == 64
            artifact = _artifact_of(artifact_store, nodes[stage])
            assert row.response_hash == blake3.blake3(artifact.text.encode()).hexdigest()
        assert len({rows[stage].cache_key for stage in STAGES}) == 3  # 阶段提示词互异

    def test_同输入跨轮次命中网关缓存零成本(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """生成的非确定性由网关缓存收敛（原则三）：同输入复现零成本、调用计数不增。"""
        first = _run(
            "r4", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        calls = gateway.call_count
        second = _run(
            "r5", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert gateway.call_count == calls
        assert first.spent_usd > 0
        assert second.spent_usd == 0.0
        assert second.cost_reconciliation["consistent"] is True
        # 缓存命中仍如实记录调用意图（llm_calls=1）与真实费用 0
        nodes = _node_by_stage(tree_store, second.tree_id)
        assert all(node.cost.llm_calls == 1 for node in nodes.values())
        assert all(node.cost.generation_api_cost_usd == 0.0 for node in nodes.values())

    def test_缓存键构成与网关一致(self):
        """缓存键构成 = 模型 + 提示词 + 采样参数（与网关内部同构成，命中即复现）。"""
        key = stage_cache_key("mock-copy-v1", "提示词", 0.0, 1024)
        expected = blake3.blake3("mock-copy-v1|提示词|0.0|1024".encode()).hexdigest()
        assert key == expected
        assert key == stage_cache_key("mock-copy-v1", "提示词", 0.0, 1024)


class Test网关失败:
    def test_阶段失败节点FAILED且成本照计(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
    ):
        """C1 场景 3：网关失败 → FAILED 节点 + 成本照计（原则二）+ 轮次继续。"""
        gateway = LLMGateway(
            _FailingBackend("scenes 阶段"),
            price_book=config.model_prices,
            sleep=lambda _: None,
        )
        result = _run(
            "r6", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert [job["status"] for job in result.jobs] == ["inserted", "failed", "inserted"]
        assert "网关失败" in result.jobs[1]["reason"]
        nodes = _node_by_stage(tree_store, result.tree_id)
        failed = nodes["scenes"]
        assert failed.status is NodeStatus.FAILED
        assert failed.score is None
        assert failed.cost.generation_api_cost_usd > 0  # 失败照计
        # 后续阶段如实记录上游未产出（不伪造前一阶段工件）
        assert nodes["script"].observation_context["previous_artifact_hash"] is None
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        assert rows["scenes"].status == "failed"
        assert rows["scenes"].error
        assert rows["scenes"].actual_cost_usd == pytest.approx(rows["scenes"].estimated_cost_usd)
        assert rows["outline"].status == "inserted"
        assert result.spent_usd == pytest.approx(sum(row.actual_cost_usd for row in rows.values()))
        assert result.cost_reconciliation["consistent"] is True

    def test_首阶段失败后续阶段继续(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
    ):
        gateway = LLMGateway(
            _FailingBackend("outline 阶段"),
            price_book=config.model_prices,
            sleep=lambda _: None,
        )
        result = _run(
            "r7", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert [job["status"] for job in result.jobs] == ["failed", "inserted", "inserted"]
        assert len(_stage_nodes(tree_store, result.tree_id)) == 3  # 阶段失败隔离，轮次继续


class _FailingBackend:
    """网关失败注入后端：提示词命中标记即抛永久错误（不重试），其余委托 Mock。"""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.call_count = 0
        self._ok = MockBackend()

    def complete(self, prompt, *, model, temperature, max_tokens):
        self.call_count += 1
        if self.marker in prompt:
            raise PermanentBackendError("后端 4xx（注入）")
        return self._ok.complete(
            prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )


class _EmptyTextBackend:
    """空正文注入后端：网关调用成功但正文为空 → 工件构造失败（费用已发生）。"""

    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, prompt, *, model, temperature, max_tokens):
        self.call_count += 1
        return BackendResult(text="", prompt_tokens=len(prompt) // 2 or 1, completion_tokens=1)


class Test工件构造失败:
    def test_空正文构造失败且费用照计(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
    ):
        """网关返回**空正文** → 网关层即拒（指名模型/角色/档案）→ 阶段失败且费用照计（原则二）。

        注：空正文的拦截点在网关（功能 016 收尾修复）——此前空正文会一路走到工件构造
        才报"工件构造失败：text 必须为非空字符串"，且真实跑批里更会先撞上下游
        `artifacts.get(None)` 的裸 TypeError。现在失败原因直接给出"后端返回空文本"。
        """
        gateway = LLMGateway(
            _EmptyTextBackend(), price_book=config.model_prices, sleep=lambda _: None
        )
        result = _run(
            "r13", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert [job["status"] for job in result.jobs] == ["failed"] * 3
        assert all("空文本" in job["reason"] for job in result.jobs)  # 网关层拦截（带模型/角色）
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        assert set(rows) == set(STAGES)
        assert {row.status for row in rows.values()} == {"failed"}
        assert all(row.error and "空文本" in row.error for row in rows.values())
        assert all(row.actual_cost_usd > 0 for row in rows.values())  # 费用照计
        assert all(row.artifact_hash is None for row in rows.values())
        nodes = _node_by_stage(tree_store, result.tree_id)
        assert all(node.status is NodeStatus.FAILED for node in nodes.values())
        assert {node.artifact_hash for node in nodes.values()} == {"00" * 32}
        assert result.spent_usd > 0


class Test执行前拒绝:
    def test_策略计划缺标记阶段拒绝零成本(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """计划缺结构化标记 → 该阶段执行前拒绝（0 网关调用 0 成本），其余阶段照常。"""
        plans = {
            "outline": None,
            "scenes": {key: value for key, value in _plan_of(make_script_artifact()).items()},
            "script": {"beats": [], "scenes": [], "characters": [], "lines": []},
        }
        result = _run(
            "r8",
            _StubPolicy(plans),
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
        )
        assert [job["status"] for job in result.jobs] == ["rejected", "inserted", "rejected"]
        assert "执行前拒绝" in result.jobs[0]["reason"]
        assert gateway.call_count == 1  # 仅合法阶段生成一次
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        assert set(rows) == {"scenes"}
        nodes = _node_by_stage(tree_store, result.tree_id)
        for stage in ("outline", "script"):
            assert nodes[stage].status is NodeStatus.EVALUATED
            assert nodes[stage].score == 0.0
            assert nodes[stage].cost.generation_api_cost_usd == 0.0
            assert "执行前拒绝" in nodes[stage].observation_context["reject_reason"]

    def test_计划标记形状非法阶段拒绝(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """标记存在但形状非法（节拍缺 act/description）→ 该阶段执行前拒绝。"""
        markers = _plan_of(make_script_artifact())
        markers["beats"] = [{"beat_id": "opening_image"}]
        plans = {"outline": markers, "scenes": None, "script": None}
        result = _run(
            "r8b",
            _StubPolicy(plans),
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
        )
        assert [job["status"] for job in result.jobs] == ["rejected"] * 3
        assert "策略计划 outline 阶段非法" in result.jobs[0]["reason"]
        assert gateway.call_count == 0

    def test_计划非映射三阶段全拒绝(
        self,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        result = _run(
            "r9",
            _StubPolicy({"beats": ["opening_image"], "grid": []}),
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
        )
        assert [job["status"] for job in result.jobs] == ["rejected"] * 3
        assert gateway.call_count == 0
        assert _rows(screenplay_jobs_engine) == []
        assert result.spent_usd == 0.0


class Test拒绝阶段与幂等:
    """执行前拒绝的阶段同样进幂等重建（运营表无行、节点留拒绝原因）。"""

    def test_含拒绝阶段轮次幂等重建(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        plans = {
            "outline": None,
            "scenes": _plan_of(make_script_artifact()),
            "script": _plan_of(make_script_artifact()),
        }
        policy = _StubPolicy(plans)
        first = _run(
            "r14", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        calls = gateway.call_count
        second = _run(
            "r14", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        assert [job["status"] for job in first.jobs] == ["rejected", "inserted", "inserted"]
        assert second.jobs == first.jobs  # 拒绝原因与工件哈希逐项还原
        assert second.spent_usd == pytest.approx(first.spent_usd)
        assert gateway.call_count == calls
        assert len(_rows(screenplay_jobs_engine)) == 2


class Test阶段语义与计费:
    """gate 短路、不适用分量归一、judge 计费入节点成本（C11 口径的 US1 提前验证）。"""

    def test_gate_判零总分零且分量齐全(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """任一 gate 判 0 → 总分 0（不可行解），其余分量仍如实落盘。"""
        evaluators = _stubs()
        evaluators[0] = StubRuleEvaluator("rule.beat_structure", score=0.0)
        result = _run(
            "r15",
            policy,
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
            evaluators=evaluators,
        )
        for node in _node_by_stage(tree_store, result.tree_id).values():
            assert node.score == 0.0
            assert len(node.eval_breakdown) == 7
            assert node.eval_breakdown["rule.beat_structure@1.0.0"]["score"] == 0.0
            assert node.eval_breakdown["proxy.entity_consistency@1.0.0"]["score"] == 0.8

    def test_不适用分量跳过并按适用权重归一(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """diagnostics.applicable=False（judge 非大纲阶段"不适用"）→ 跳过且不入分母。"""
        evaluators = _stubs()
        evaluators[4] = _NotApplicableProxy("proxy.entity_consistency", score=0.0)
        result = _run(
            "r16",
            policy,
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
            evaluators=evaluators,
        )
        # (0.5×0.6 + 0.5×0.7) / 1.0 = 0.65（不适用分量不拖底）
        nodes = _node_by_stage(tree_store, result.tree_id)
        assert all(node.score == pytest.approx(0.65) for node in nodes.values())
        assert (
            nodes["script"].eval_breakdown["proxy.entity_consistency@1.0.0"]["diagnostics"][
                "applicable"
            ]
            is False
        )

    def test_judge_用量入节点成本(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """judge 计费（last_usage）入节点成本并与运营表对账（004/007 同口径）。"""
        evaluators = _stubs()
        evaluators[-1] = _UsageJudge("judge.dramatic_tension", score=0.7)
        result = _run(
            "r17",
            policy,
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
            evaluators=evaluators,
        )
        nodes = _node_by_stage(tree_store, result.tree_id)
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        for stage, node in nodes.items():
            assert node.cost.llm_calls == 4  # 1 次生成 + 3 次 judge
            assert node.cost.llm_tokens > 120
            # 节点成本 = 生成实际扣费（运营表）+ judge 计费
            assert node.cost.generation_api_cost_usd == pytest.approx(
                rows[stage].actual_cost_usd + 0.004
            )
        assert result.cost_reconciliation["evaluator_cost_usd"] == pytest.approx(0.012)
        assert result.cost_reconciliation["consistent"] is True


class _NotApplicableProxy(StubProxyEvaluator):
    """不适用代理桩：分数照给但标注 applicable=False（跳过且不入权重分母）。"""

    def evaluate(self, artifact, context):
        result = super().evaluate(artifact, context)
        return EvalResult(
            score=result.score,
            diagnostics={**result.diagnostics, "applicable": False},
        )


class _UsageJudge(StubJudgeEvaluator):
    """judge 计费桩：暴露 last_usage（真实 judge 在 T922 落地）——用量入节点成本。"""

    def evaluate(self, artifact, context):
        self.last_usage = {"llm_calls": 3, "llm_tokens": 120, "cost_usd": 0.004}
        return super().evaluate(artifact, context)


class Test输入预检:
    def test_缺题材零副作用(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        with pytest.raises(ScreenplayLoopError, match="topic"):
            _run(
                "r10",
                policy,
                tree_store,
                artifact_store,
                screenplay_jobs_engine,
                gateway,
                config,
                inputs={"target_duration_min": 90},
            )
        assert gateway.call_count == 0
        assert tree_store.trees_by(agent_id="screenplay") == []
        assert _rows(screenplay_jobs_engine) == []

    @pytest.mark.parametrize(
        "bad_inputs",
        [[], {**INPUTS, "constraints": "单场景"}, {**INPUTS, "characters": [""]}],
        ids=["非映射", "约束非列表", "角色含空串"],
    )
    def test_输入形态非法拒绝(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
        bad_inputs,
    ):
        with pytest.raises(ScreenplayLoopError):
            _run(
                "r10d",
                policy,
                tree_store,
                artifact_store,
                screenplay_jobs_engine,
                gateway,
                config,
                inputs=bad_inputs,
            )
        assert gateway.call_count == 0

    def test_目标时长非法拒绝(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        with pytest.raises(ScreenplayLoopError, match="target_duration_min"):
            _run(
                "r10b",
                policy,
                tree_store,
                artifact_store,
                screenplay_jobs_engine,
                gateway,
                config,
                inputs={**INPUTS, "target_duration_min": 0},
            )

    def test_空评估器列表拒绝(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """显式空列表 = 调用方缺陷：拒绝且 0 副作用（不静默无打分）。

        `evaluators=None` 自 T923 起语义为"默认装配真实七评估器"（见
        tests/unit/test_screenplay_composite.py::Test执行器接线）。
        """
        with pytest.raises(ScreenplayLoopError, match="评估器"):
            _run(
                "r10c",
                policy,
                tree_store,
                artifact_store,
                screenplay_jobs_engine,
                gateway,
                config,
                evaluators=[],
            )
        assert gateway.call_count == 0
        assert tree_store.trees_by(agent_id="screenplay") == []


class Test策略版本可回溯:
    def test_节点携带人工策略版本(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        """C1 场景 4：任一节点可回溯到产出它的策略版本（人工版本同样版本化）。"""
        result = _run(
            "r11", policy, tree_store, artifact_store, screenplay_jobs_engine, gateway, config
        )
        nodes = tree_store.nodes_of(result.tree_id)
        assert {node.policy_version for node in nodes} == {"9f2c41ab77de"}
        rows = _rows(screenplay_jobs_engine)
        assert {row.policy_version for row in rows} == {"9f2c41ab77de"}
        tree = next(
            t for t in tree_store.trees_by(agent_id="screenplay") if t.tree_id == result.tree_id
        )
        assert tree.policy_version == policy.policy_version
        assert result.policy_version == policy.policy_version


class Test评估器崩溃隔离:
    def test_崩溃阶段节点FAILED且生成成本照计(
        self,
        policy,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        gateway,
        config,
    ):
        class _CrashOnScript(StubRuleEvaluator):
            def evaluate(self, artifact, context):
                if context["artifact"].stage == "script":
                    raise RuntimeError("评估器崩溃模拟")
                return super().evaluate(artifact, context)

        evaluators = [*(StubRuleEvaluator(gate_id) for gate_id in _GATE_IDS)]
        evaluators[0] = _CrashOnScript("rule.beat_structure")
        evaluators += [
            StubProxyEvaluator("proxy.entity_consistency", score=0.8),
            StubProxyEvaluator("proxy.timeline_conflict", score=0.6),
            StubJudgeEvaluator("judge.dramatic_tension", score=0.7),
        ]
        result = _run(
            "r12",
            policy,
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            gateway,
            config,
            evaluators=evaluators,
        )
        assert [job["status"] for job in result.jobs] == ["inserted", "inserted", "failed"]
        nodes = _node_by_stage(tree_store, result.tree_id)
        assert nodes["script"].status is NodeStatus.FAILED
        assert nodes["script"].score is None
        assert nodes["script"].cost.generation_api_cost_usd > 0  # 生成成本照常入账
        assert "评估器崩溃" in nodes["script"].observation_context["reject_reason"]
        rows = {row.stage: row for row in _rows(screenplay_jobs_engine)}
        assert rows["script"].status == "inserted"  # 生成已落账，不重复生成
        assert result.cost_reconciliation["consistent"] is True


class Test轮次标识:
    def test_树标识确定性派生(self):
        assert round_tree_id("r1") == round_tree_id("r1")
        assert round_tree_id("r1") != round_tree_id("r2")
