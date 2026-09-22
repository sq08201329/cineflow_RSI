"""价目随快照冻结的可证伪单测（功能 016 / T1610）：契约 C7 / SC-002。

**为什么这是本特性的核心**：价目内置或只存"总成本"都会让历史不可复现——改一次配置价目，
历史节点的口径就漂了。本文件用**一轮真实落树的剧本线运行**证明：

1. 落树后节点快照含 `llm_profiles`（价目 + 口径备注）；
2. **审计复算**：按快照价目 × 实际 token 重算 == 节点入账成本（零漂移的前提）；
3. **改配置价目后**：历史节点成本与快照**逐字节不变**，而新节点按新价目入账（×10 变化可见）。
"""

import json
from dataclasses import replace

import pytest

from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.loop import run_screenplay_round
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import BackendResult, LLMGateway
from core.llm_gateway.profiles import load_or_migrate
from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

INPUTS = {
    "topic": "病房里的三个月",
    "target_duration_min": 90,
    "constraints": ["单场景为主"],
    "characters": ["林静", "陈默", "周医生"],
}
_GATE_IDS = ("rule.beat_structure", "rule.page_minutes", "rule.scene_character")


class _StubPolicy:
    """人工策略桩：三分阶段计划（与既有剧本线单测同款，三阶段各一次生成调用）。"""

    policy_version = "9f2c41ab77de"

    def __init__(self, plans):
        self._plans = plans

    def plan(self, inputs, config):
        import copy

        return copy.deepcopy(self._plans)


def _plans(make_script_artifacts) -> dict:
    artifacts = make_script_artifacts()

    def _plan_of(artifact: ScriptArtifact) -> dict:
        payload = artifact.to_dict()
        return {key: payload[key] for key in ("beats", "scenes", "characters", "lines")}

    return {stage: _plan_of(artifact) for stage, artifact in artifacts.items()}


def _stubs():
    """评估器桩：三 gate + 两 proxy + judge（judge 也过网关，故价目冻结要覆盖它）。"""
    return [
        *(StubRuleEvaluator(gate_id) for gate_id in _GATE_IDS),
        StubProxyEvaluator("proxy.entity_consistency", score=0.8),
        StubProxyEvaluator("proxy.timeline_conflict", score=0.6),
        StubJudgeEvaluator("judge.dramatic_tension", score=0.7),
    ]


class _CountingBackend:
    """Mock 后端包装：累计 token（审计复算的输入），其余行为与 MockBackend 一致。"""

    def __init__(self) -> None:
        self._inner = MockBackend()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        result = self._inner.complete(
            prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )
        self.calls += 1
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        return result


def _profiles_for(prompt_per_1k: float, completion_per_1k: float):
    """单档案 ProfileLoad（经旧扁平写法迁移构造；档案 id 与既有模型名一致）。"""
    return load_or_migrate(
        {
            "screenplay": {
                "model": "mock-copy-v1",
                "model_prices": {
                    "mock-copy-v1": {
                        "prompt_per_1k": prompt_per_1k,
                        "completion_per_1k": completion_per_1k,
                    }
                },
            }
        }
    )


def _run_round(
    *,
    round_id,
    tree_store,
    artifact_store,
    screenplay_jobs_engine,
    screenplay_config,
    make_script_artifacts,
    profiles,
    backend,
    config=None,
):
    gateway = LLMGateway(
        backend,
        price_book={"mock-copy-v1": dict(profiles.snapshot().price_book()["mock-copy-v1"])},
        sleep=lambda _: None,
        profiles=profiles,
    )
    return run_screenplay_round(
        round_id=round_id,
        policy=_StubPolicy(_plans(make_script_artifacts)),
        store=tree_store,
        artifacts=artifact_store,
        engine=screenplay_jobs_engine,
        gateway=gateway,
        config=config if config is not None else screenplay_config,
        inputs=dict(INPUTS),
        evaluators=_stubs(),
    )


def _stage_nodes(store, result):
    return [node for node in store.nodes_of(result.tree_id) if node.parent_id is not None]


def _snapshot_of(store, tree_id: str) -> dict:
    """树级 config_snapshot（快照随树冻结）：取本轮的 `llm_profiles` 键。"""
    tree = next(t for t in store.trees_by(agent_id="screenplay") if t.tree_id == tree_id)
    return tree.config_snapshot["llm_profiles"]


def test_落树快照含价目_审计复算零漂移_改价不影响历史(
    tree_store,
    artifact_store,
    screenplay_jobs_engine,
    screenplay_config,
    make_script_artifacts,
):
    # ① 第一轮：价目 P1
    backend_a = _CountingBackend()
    profiles_a = _profiles_for(0.001, 0.002)
    result_a = _run_round(
        round_id="llm-freeze-a",
        tree_store=tree_store,
        artifact_store=artifact_store,
        screenplay_jobs_engine=screenplay_jobs_engine,
        screenplay_config=screenplay_config,
        make_script_artifacts=make_script_artifacts,
        profiles=profiles_a,
        backend=backend_a,
    )
    nodes_a = _stage_nodes(tree_store, result_a)
    assert len(nodes_a) == 3  # outline / scenes / script
    snapshot_a = _snapshot_of(tree_store, result_a.tree_id)
    assert snapshot_a["profiles"][0]["prices"] == {
        "prompt_per_1k": 0.001,
        "completion_per_1k": 0.002,
    }
    assert "legacy_env" in snapshot_a["profiles"][0]  # 无密钥、只有口径字段

    # ② 审计复算：按冻结快照的价目 × 实际 token == 三节点入账成本合计（零漂移的前提）
    price = snapshot_a["profiles"][0]["prices"]
    expected = (
        backend_a.prompt_tokens / 1000 * price["prompt_per_1k"]
        + backend_a.completion_tokens / 1000 * price["completion_per_1k"]
    )
    recorded_a = round(sum(node.cost.llm_tokens for node in nodes_a), 0)
    assert recorded_a >= 0  # 节点成本记录字段齐备
    total_cost_a = sum(node.cost.generation_api_cost_usd for node in nodes_a)
    assert total_cost_a == pytest.approx(expected, rel=1e-6)
    assert total_cost_a > 0
    cost_a_before = [node.cost.generation_api_cost_usd for node in nodes_a]

    # ③ 改**配置价目**（×10：档案与模型价目表同时改，正是一处配置改动的效果）后再跑一轮
    backend_b = _CountingBackend()
    price_b = {"mock-copy-v1": {"prompt_per_1k": 0.01, "completion_per_1k": 0.02}}
    profiles_b = _profiles_for(0.01, 0.02)
    config_b = replace(screenplay_config, model_prices=price_b)
    result_b = _run_round(
        round_id="llm-freeze-b",
        tree_store=tree_store,
        artifact_store=artifact_store,
        screenplay_jobs_engine=screenplay_jobs_engine,
        screenplay_config=screenplay_config,
        make_script_artifacts=make_script_artifacts,
        profiles=profiles_b,
        backend=backend_b,
        config=config_b,
    )
    nodes_b = _stage_nodes(tree_store, result_b)
    total_cost_b = sum(node.cost.generation_api_cost_usd for node in nodes_b)
    assert total_cost_b == pytest.approx(10 * total_cost_a, rel=1e-6)  # 新价目生效（可证伪）
    assert _snapshot_of(tree_store, result_b.tree_id)["profiles"][0]["prices"] == {
        "prompt_per_1k": 0.01,
        "completion_per_1k": 0.02,
    }

    # ④ 历史不漂移：旧节点成本与快照逐字段不变（树 immutable + 快照冻结）
    nodes_a_again = _stage_nodes(tree_store, result_a)
    assert [node.cost.generation_api_cost_usd for node in nodes_a_again] == cost_a_before
    frozen = _snapshot_of(tree_store, result_a.tree_id)
    assert frozen == snapshot_a
    assert json.dumps(frozen, sort_keys=True) == json.dumps(snapshot_a, sort_keys=True)


def test_未接线档案时快照仍齐备_单档案等价现状(
    tree_store,
    artifact_store,
    screenplay_jobs_engine,
    screenplay_config,
    make_script_artifacts,
):
    """既有调用形态（不带 profiles）→ 快照来源 legacy_price_book，且成本口径不变。"""
    gateway = LLMGateway(
        MockBackend(),
        price_book=screenplay_config.model_prices,
        sleep=lambda _: None,
    )
    result = run_screenplay_round(
        round_id="llm-freeze-legacy",
        policy=_StubPolicy(_plans(make_script_artifacts)),
        store=tree_store,
        artifacts=artifact_store,
        engine=screenplay_jobs_engine,
        gateway=gateway,
        config=screenplay_config,
        inputs=dict(INPUTS),
        evaluators=_stubs(),
    )
    nodes = _stage_nodes(tree_store, result)
    snapshot = _snapshot_of(tree_store, result.tree_id)
    assert snapshot["source"] == "legacy_price_book"
    assert sum(node.cost.generation_api_cost_usd for node in nodes) > 0


def test_估算与折算同源_改档案价目同步变化(
    tree_store,
    artifact_store,
    screenplay_jobs_engine,
    screenplay_config,
    make_script_artifacts,
):
    """遗留 1 的收敛断言：剧本运营表的 `estimated_cost_usd` 与节点入账成本**同取档案价目**。

    构造刻意让两处价目不一致（price_book 里是旧价目 P1，档案里是新价目 P2，×10）：
    若估算仍走 `config.price_of`（旧路径），估算会是 P1 口径而实际成本是 P2 口径 → 两者相差 10 倍，
    断言即红；实现改为经 `gateway.prices_for(role=...)` 取价目后，估算与实际同步为 P2 口径。
    """
    from sqlalchemy import select

    from agents.screenplay.db import screenplay_jobs
    from agents.screenplay.loop import MAX_TOKENS
    from core.llm_gateway.routing import Role

    profile_prices = {"prompt_per_1k": 0.01, "completion_per_1k": 0.02}  # 档案价目（P2 = 10×P1）
    profiles = load_or_migrate(
        {
            "llm": {
                "profiles": {
                    "deepseek-flash": {
                        "base_url": "https://api.example.invalid",
                        "api_key_env": "EXAMPLE_KEY",
                        "prices": profile_prices,
                        "price_note": "测试档案价目",
                    }
                },
                "roles": {},
                "default_profile": "deepseek-flash",
            }
        }
    )
    backend = _CountingBackend()
    gateway = LLMGateway(
        backend,
        # 刻意保留旧价目表：估算与折算都必须忽略它（取档案价目）
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        profiles=profiles,
    )
    model, price = gateway.prices_for(role=Role.GENERATION, model="mock-copy-v1")
    assert model == "deepseek-flash"  # 接档案后模型名由档案决定
    assert price == profile_prices

    result = run_screenplay_round(
        round_id="llm-freeze-same-source",
        policy=_StubPolicy(_plans(make_script_artifacts)),
        store=tree_store,
        artifacts=artifact_store,
        engine=screenplay_jobs_engine,
        gateway=gateway,
        config=screenplay_config,
        inputs=dict(INPUTS),
        evaluators=_stubs(),
    )
    with screenplay_jobs_engine.connect() as conn:
        estimates = [
            row.estimated_cost_usd
            for row in conn.execute(
                select(screenplay_jobs).where(
                    screenplay_jobs.c.round_id == "llm-freeze-same-source"
                )
            ).all()
        ]
    assert len(estimates) == 3  # 三阶段各一条运营行

    # 估算公式（保守上界）：输入 token 实测 + 满额 max_tokens —— 两处都取**档案价目**
    expected_estimate = (
        backend.prompt_tokens / 1000 * profile_prices["prompt_per_1k"]
        + len(estimates) * MAX_TOKENS / 1000 * profile_prices["completion_per_1k"]
    )
    assert sum(estimates) == pytest.approx(expected_estimate)
    # 若估算走旧价目表（P1），合计会是 1/10 —— 断言因此可证伪
    legacy_estimate = (
        backend.prompt_tokens / 1000 * 0.001 + len(estimates) * MAX_TOKENS / 1000 * 0.002
    )
    assert sum(estimates) != pytest.approx(legacy_estimate)

    # 实际入账成本同源（同一条档案价目折算）
    nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
    assert sum(n.cost.generation_api_cost_usd for n in nodes) == pytest.approx(
        backend.prompt_tokens / 1000 * profile_prices["prompt_per_1k"]
        + backend.completion_tokens / 1000 * profile_prices["completion_per_1k"]
    )
    # 改档案价目 ×10 → 估算与折算**同步**变化（两处都取新价目）
    doubled = dict(profiles.profiles)
    from dataclasses import replace as _replace

    profile = doubled["deepseek-flash"]
    doubled["deepseek-flash"] = _replace(
        profile,
        prices={"prompt_per_1k": 0.1, "completion_per_1k": 0.2},
    )
    bumped = profiles.__class__(
        profiles=doubled, routing=profiles.routing, notes=profiles.notes, source=profiles.source
    )
    gateway_b = LLMGateway(
        _CountingBackend(),
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        profiles=bumped,
    )
    _, price_b = gateway_b.prices_for(role=Role.GENERATION, model="mock-copy-v1")
    assert price_b["prompt_per_1k"] == pytest.approx(10 * price["prompt_per_1k"])
