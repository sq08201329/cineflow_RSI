"""集成测试：角色 × 档案成本分解落盘一致性（功能 022 / US1 / SC-001、SC-002）。

固定种子（MockBackend 逐字节可复现）+ 真实档案路由（configs/movie.yaml 的 llm 段）
多 Agent（screenplay + promo）共享同一网关运行：

1) 逐节点：Σ 分解 calls/tokens ≤ 六字段总量；本场景全程零缓存命中 ⇒ 取等（FR-002）；
2) 全部新节点分解经 merge 汇总，与运行内网关 `cost_breakdown()` 逐格一致（SC-002）——
   judge 格比对 calls / tokens 合计 / cost_usd：judge 计费用量只有聚合口径
   （`last_usage` 不拆 prompt/completion，既有评估器实现零改动红线），
   generation / copywriting 为逐调用归集 ⇒ 四分量全等；
3) 缺 `llm_breakdown` 键的旧行读回：不报错、默认空映射，且原始 JSON 键缺席可判
   展示三态之「未标定」（FR-003 / plan D3，禁回填）。
"""

import copy
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, insert, select

from agents.promo.config import PromoConfig
from agents.promo.db import create_campaigns_schema
from agents.promo.loop import run_round as run_promo_round
from agents.promo.platform.simulated import SimulatedPlatform
from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.db import create_jobs_schema
from agents.screenplay.loop import run_screenplay_round
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.llm_gateway.profiles import load_or_migrate
from core.tree.artifacts import LocalArtifactStore
from core.tree.attribution import merge
from core.tree.db import create_schema, tree_nodes
from core.tree.store import create_tree_store
from ops.ingest_metrics import ingest_round
from tests.plugin_fixtures import resync_plugin_versions

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_INPUTS = {
    "topic": "病房里的三个月",
    "target_duration_min": 3,
    "constraints": ["单场景为主"],
    "characters": ["林静", "陈默", "周医生"],
}


class _StubScreenplayPolicy:
    """三阶段计划桩（结构化标记齐备，真实七评估器全过门禁 ⇒ judge 真实计费）。"""

    policy_version = "9f2c41ab77de"

    def __init__(self, plans):
        self._plans = plans

    def plan(self, inputs, config):
        return copy.deepcopy(self._plans)


class _StubPromoPolicy:
    policy_version = "a1b2c3d4e5f6"

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _screenplay_config() -> ScreenplayConfig:
    """真实 movie.yaml + 页数窗口缩放（9 行夹具 → 3 页），版本按本夹具取值重新钉住。"""
    raw = copy.deepcopy(_RAW_CONFIG)
    raw["screenplay"].update({"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3})
    return ScreenplayConfig.from_dict(resync_plugin_versions(raw, agents=("screenplay",)))


def _plans(make_script_artifact) -> dict:
    return {
        stage: {
            key: make_script_artifact("valid", stage=stage).to_dict()[key]
            for key in ("beats", "scenes", "characters", "lines")
        }
        for stage in ("outline", "scenes", "script")
    }


def _brief(prompt: str, temperature: float) -> dict:
    return {
        "prompt": prompt,
        "gen_params": {"temperature": temperature},
        "kind": "copy",
        "tags": ["剧情"],
        "budget_usd": 2.0,
    }


def _sum_breakdown(breakdown: dict) -> tuple[int, int, float]:
    """分解映射的 Σ（calls, tokens, cost_usd）——逐节点 FR-002 一致性读数。"""
    calls = 0
    tokens = 0
    cost = 0.0
    for profiles in breakdown.values():
        for entry in profiles.values():
            calls += entry["calls"]
            tokens += entry["prompt_tokens"] + entry["completion_tokens"]
            cost += entry["cost_usd"]
    return calls, tokens, cost


@pytest.fixture()
def run(tmp_path, make_script_artifact):
    """固定种子多 Agent 运行：screenplay（真实七评估器）+ promo（两物料 + 回流），共享网关。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    create_jobs_schema(engine)
    create_campaigns_schema(engine)
    store = create_tree_store(engine)
    loaded = load_or_migrate(copy.deepcopy(_RAW_CONFIG))
    gateway = LLMGateway(
        MockBackend(),
        price_book=loaded.snapshot().price_book(),
        sleep=lambda _: None,
        profiles=loaded,
    )
    screenplay_result = run_screenplay_round(
        round_id="attr-screenplay",
        policy=_StubScreenplayPolicy(_plans(make_script_artifact)),
        store=store,
        artifacts=LocalArtifactStore(tmp_path / "sp-artifacts"),
        engine=engine,
        gateway=gateway,
        config=_screenplay_config(),
        inputs=dict(_INPUTS),
    )
    promo_config = PromoConfig.from_dict(copy.deepcopy(_RAW_CONFIG))
    platform = SimulatedPlatform(promo_config.simulated_platform)
    promo_result = run_promo_round(
        "attr-promo",
        _StubPromoPolicy(
            [_brief("写一句宣发文案：雨夜重逢", 0.3), _brief("写一句宣发文案：站台告别", 0.7)]
        ),
        store,
        LocalArtifactStore(tmp_path / "pm-artifacts"),
        platform,
        gateway,
        promo_config,
        engine=engine,
    )
    ingest_round("attr-promo", store, platform, engine, promo_config)
    return {
        "engine": engine,
        "store": store,
        "gateway": gateway,
        "tree_ids": (screenplay_result.tree_id, promo_result.tree_id),
        "screenplay_result": screenplay_result,
        "promo_result": promo_result,
    }


def _all_nodes(run):
    return [node for tree_id in run["tree_ids"] for node in run["store"].nodes_of(tree_id)]


class Test逐节点分解一致性:
    def test_场景前提_全部job插入且物料送达(self, run):
        """场景钉住：三阶段全 inserted、两物料全 delivered（无失败节点混入取等断言）。"""
        assert [job["status"] for job in run["screenplay_result"].jobs] == ["inserted"] * 3
        materials = run["promo_result"].materials
        assert [m["status"] for m in materials] == ["delivered"] * 2

    def test_逐节点分解不超总量且零缓存命中取等(self, run):
        """FR-002 / SC-001：Σ 分解 ≤ 六字段总量；本场景零缓存命中 ⇒ 逐节点取等。"""
        assert run["gateway"].cache_hits == 0  # 全新提示词：零命中是"取等"前提
        nodes = _all_nodes(run)
        assert nodes  # 两棵树均有节点（防空跑假绿）
        for node in nodes:
            calls, tokens, _ = _sum_breakdown(node.cost.llm_breakdown)
            assert calls <= node.cost.llm_calls, node.node_id
            assert tokens <= node.cost.llm_tokens, node.node_id
            assert calls == node.cost.llm_calls, node.node_id
            assert tokens == node.cost.llm_tokens, node.node_id


class Test汇总与网关分解逐格一致:
    def test_新节点分解汇总_等于网关cost_breakdown(self, run):
        """SC-002：全部新节点分解经 merge 汇总 == 运行内网关 cost_breakdown() 逐格一致。

        generation / copywriting 逐调用归集 ⇒ 四分量全等；judge 只有聚合计费口径
        （last_usage 不拆 prompt/completion，评估器实现零改动红线）⇒ 比对
        calls / tokens 合计 / cost_usd 三项。
        """
        merged: dict = {}
        for node in _all_nodes(run):
            merged = merge(merged, node.cost.llm_breakdown)
        gateway_breakdown = run["gateway"].cost_breakdown()
        # 三角色均在运行内真实发生（覆盖生成 / 判决 / 宣发文案三类归属）
        assert set(gateway_breakdown) == {"generation", "judge", "copywriting"}
        assert set(merged) == set(gateway_breakdown)
        for role, profiles in gateway_breakdown.items():
            assert set(merged[role]) == set(profiles), role
            for profile_id, cell in profiles.items():
                got = merged[role][profile_id]
                assert got["calls"] == cell["calls"], (role, profile_id)
                assert (
                    got["prompt_tokens"] + got["completion_tokens"]
                    == cell["prompt_tokens"] + cell["completion_tokens"]
                ), (role, profile_id)
                assert got["cost_usd"] == pytest.approx(cell["cost_usd"]), (role, profile_id)
                if role in ("generation", "copywriting"):
                    # 逐调用归集：prompt/completion 拆分同样逐格一致
                    assert got["prompt_tokens"] == cell["prompt_tokens"], (role, profile_id)
                    assert got["completion_tokens"] == cell["completion_tokens"], (
                        role,
                        profile_id,
                    )


class Test缺键旧行读回:
    def test_旧行无llm_breakdown键_读回默认空且未标定信号可判(self, run):
        """FR-003 / plan D3：历史行（cost JSON 无 llm_breakdown 键）读回不报错、默认空映射；
        原始 JSON 键缺席即展示三态之「未标定」信号（禁回填、不摊入任何分组）。"""
        engine = run["engine"]
        store = run["store"]
        tree_id = run["tree_ids"][0]
        root_id = next(
            node.node_id for node in store.nodes_of(tree_id) if node.parent_id is None
        )
        legacy_cost = {  # 旧六字段形状：022 之前落盘的 cost JSON（无扩展键）
            "llm_calls": 2,
            "llm_tokens": 120,
            "generation_api_calls": 0,
            "generation_api_cost_usd": 0.01,
            "human_review_minutes": 0.0,
            "wall_clock_seconds": 1.5,
        }
        with engine.begin() as conn:
            conn.execute(
                insert(tree_nodes).values(
                    node_id="attr-legacy-node",
                    tree_id=tree_id,
                    parent_id=root_id,
                    depth=1,
                    agent_id="screenplay",
                    policy_version="legacy-v1",
                    prompt="",
                    observation_context={},
                    artifact_hash="00" * 32,
                    eval_breakdown={},
                    score=0.5,
                    status="evaluated",
                    cost=legacy_cost,
                    created_at=1_700_000_000.0,
                )
            )
        node = store.get_node("attr-legacy-node")  # 缺键读回不报错（CostRecord 默认值）
        assert node.cost.llm_breakdown == {}
        assert node.cost.llm_calls == 2  # 六字段读回逐字不变
        with engine.connect() as conn:
            raw_cost = conn.execute(
                select(tree_nodes.c.cost).where(tree_nodes.c.node_id == "attr-legacy-node")
            ).scalar_one()
        # 「未标定」判据：原始 JSON 键缺席（与"键在场且空 = 缓存命中零计费"严格区分）
        assert "llm_breakdown" not in raw_cost
