"""剪辑线上探索执行器单测（功能 007 / T716，先于实现编写）。

C1/C2 全场景：非法 EDL 五类执行前拒绝（0 渲染 0 成本）；一轮 3 组 EDL 落树、
成本入账（预估时长 × 价目，实际 ≤ 预估）；预算超界拒绝且已执行照常入账；
同 round_id 二次触发幂等重建（0 重复渲染 0 重复扣费）；渲染失败条目 failed +
成本照计；素材不足（不足最小镜头数执行前拒绝 / 总长 < 目标下限预检 FAILED 注明）。
评估器桩注入（loop 面向评估器协议编程，US2 才接真实五评估器）。
"""

import copy
from pathlib import Path

import pytest
import yaml
from sqlalchemy import insert, select

from agents.editing.config import EditingConfig
from agents.editing.db import edit_render_jobs
from agents.editing.loop import (
    EditingLoopError,
    freeze_round_tree,
    round_tree_id,
    run_editing_round,
)
from agents.editing.platform.simulated import SimulatedEditRenderer
from agents.editing.shots import ShotEntry, ShotLibrary
from core.tree.models import NodeStatus
from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _config_dict(**overrides) -> dict:
    """合法配置字典：时长目标缩小到夹具素材可行域（5s ± 2s），渲染尺寸缩小提速。"""
    config = copy.deepcopy(_REAL_CONFIG)
    config["editing"]["target_duration_s"] = 5
    config["editing"]["duration_tolerance_s"] = 2
    config["editing"]["render"].update(width=64, height=48)
    for key, value in overrides.items():
        config["editing"][key] = value
    return config


def _config(**overrides) -> EditingConfig:
    return EditingConfig.from_dict(_config_dict(**overrides))


class _StubPolicy:
    """策略桩：产出给定 EDL 组合（做梦层接入前的手工策略形态）。"""

    policy_version = "stub-editing-v1"

    def __init__(self, edls):
        self._edls = list(edls)

    def plan(self, config, inputs):
        return list(self._edls)


def _stubs():
    """评估器桩三件套（gate/proxy/judge 各一）。"""
    return [
        StubRuleEvaluator("rule.duration_compliance"),
        StubProxyEvaluator("proxy.pacing_curve", score=0.8),
        StubJudgeEvaluator("judge.narrative_flow", score=0.7),
    ]


@pytest.fixture()
def library(make_shot_library):
    return make_shot_library()


@pytest.fixture()
def structure(make_scene_structure, library):
    return make_scene_structure(library=library)


@pytest.fixture()
def adapter():
    return SimulatedEditRenderer(_config().render)


def _three_edls(make_edl):
    """三组哈希不同的合法 EDL。"""
    return [
        make_edl(),
        make_edl(clips=[
            {"shot_id": "shot-3", "in_ms": 0, "out_ms": 4000,
             "transition": {"type": "dissolve", "duration_ms": 500}},
            {"shot_id": "shot-4", "in_ms": 0, "out_ms": 3000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-5", "in_ms": 0, "out_ms": 5000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ]),
        make_edl(clips=[
            {"shot_id": "shot-1", "in_ms": 0, "out_ms": 3000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-6", "in_ms": 1000, "out_ms": 4000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ], audio=[]),
    ]


def _run(round_id, policy, store, artifacts, adapter, engine, config, library, structure):
    return run_editing_round(
        round_id=round_id,
        policy=policy,
        store=store,
        artifacts=artifacts,
        adapter=adapter,
        engine=engine,
        config=config,
        inputs={"shot_library": library, "scene_structure": structure},
        evaluators=_stubs(),
    )


class Test一轮剪辑落树:
    def test_三组_EDL_落树且成本入账(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        cfg = _config()
        result = _run(
            "r1", _StubPolicy(_three_edls(make_edl)), tree_store, artifact_store,
            adapter, editing_jobs_engine, cfg, library, structure,
        )
        assert [j["status"] for j in result.jobs] == ["inserted"] * 3
        assert result.budget_cap_usd == cfg.exploration_per_round_usd
        assert result.spent_usd == pytest.approx(adapter.total_spent)
        assert result.spent_usd > 0
        assert result.cost_reconciliation["consistent"] is True
        # 树：根 + 3 个已评估剪辑节点（成片内容寻址可回读）
        nodes = tree_store.nodes_of(result.tree_id)
        film_nodes = [n for n in nodes if n.parent_id is not None]
        assert len(film_nodes) == 3
        for node in film_nodes:
            assert node.status is NodeStatus.EVALUATED
            assert artifact_store.get(node.artifact_hash)  # mp4 可回读
            # 评估器桩三分量入 breakdown；合成 = (0.6×0.8 + 0.4×0.7)/1.0
            assert len(node.eval_breakdown) == 3
            assert node.score == pytest.approx(0.76)
            assert node.cost.generation_api_cost_usd > 0

    def test_观测上下文带_EDL_与哈希(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        """回放匹配键落观测：edl 规范化结构 + edl_hash + job_id。"""
        edls = _three_edls(make_edl)
        result = _run(
            "r1b", _StubPolicy(edls), tree_store, artifact_store, adapter,
            editing_jobs_engine, _config(), library, structure,
        )
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        by_hash = {n.observation_context["edl_hash"]: n for n in nodes}
        assert set(by_hash) == {e.edl_hash() for e in edls}


class Test非法EDL执行前拒绝:
    @pytest.mark.parametrize(
        "variant",
        ["unknown_ref", "out_of_bounds", "cross_partition", "scene_disorder", "illegal_transition"],
    )
    def test_五类非法变体零渲染零成本(
        self, variant, make_edl, tree_store, artifact_store, adapter,
        editing_jobs_engine, library, structure,
    ):
        """C1：违规一律执行前拒绝——适配器 0 调用、运营表 0 行、0 成本。"""
        result = _run(
            f"bad-{variant}", _StubPolicy([make_edl(variant)]), tree_store, artifact_store,
            adapter, editing_jobs_engine, _config(), library, structure,
        )
        assert result.jobs[0]["status"] == "rejected"
        assert result.jobs[0]["reason"]
        assert adapter.render_calls == 0
        assert result.spent_usd == 0.0
        with editing_jobs_engine.connect() as conn:
            assert conn.execute(select(edit_render_jobs)).all() == []
        # 拒绝节点如实落盘（score=0，注明原因）
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert len(nodes) == 1 and nodes[0].score == 0.0
        assert nodes[0].observation_context["reject_reason"]


class Test预算门禁:
    def test_超界拒绝且已执行照常入账(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        """C2 场景 2：预算只够一组——后续拒绝，已渲染的照常入账。"""
        edls = _three_edls(make_edl)
        first_estimate = adapter.estimate(edls[0], library)
        cfg = _config(exploration_per_round_usd=first_estimate * 1.5)
        result = _run(
            "r2", _StubPolicy(edls), tree_store, artifact_store, adapter,
            editing_jobs_engine, cfg, library, structure,
        )
        assert result.jobs[0]["status"] == "inserted"
        assert [j["status"] for j in result.jobs[1:]] == ["rejected", "rejected"]
        assert "预算门禁" in result.jobs[1]["reason"]
        assert adapter.render_calls == 1
        assert result.spent_usd == pytest.approx(adapter.total_spent)
        assert result.cost_reconciliation["consistent"] is True


class Test幂等重建:
    def test_同_round_id_二次触发零重复(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        """C2 场景 3：唯一键 (round_id, edl_hash) + 确定性 id 派生——重建首轮结果。"""
        edls = _three_edls(make_edl)
        cfg = _config()
        first = _run(
            "r3", _StubPolicy(edls), tree_store, artifact_store, adapter,
            editing_jobs_engine, cfg, library, structure,
        )
        second = _run(
            "r3", _StubPolicy(edls), tree_store, artifact_store, adapter,
            editing_jobs_engine, cfg, library, structure,
        )
        assert second.jobs == first.jobs
        assert second.spent_usd == pytest.approx(first.spent_usd)
        assert adapter.render_calls == 3  # 0 重复渲染
        assert len(tree_store.nodes_of(first.tree_id)) == 4  # 0 重复节点


class Test渲染失败:
    def test_失败条目_failed_且成本照计(
        self, make_edl, tree_store, artifact_store, editing_jobs_engine, library, structure,
    ):
        """C2 场景 4：渲染失败 → status=failed + 预估成本照常入账，轮次继续。"""
        adapter = SimulatedEditRenderer(_config().render, fail_on_shots={"shot-5"})
        edls = _three_edls(make_edl)  # 第 1/2 组引用 shot-5，第 3 组不引用
        result = _run(
            "r4", _StubPolicy(edls), tree_store, artifact_store, adapter,
            editing_jobs_engine, _config(), library, structure,
        )
        assert [j["status"] for j in result.jobs] == ["failed", "failed", "inserted"]
        assert "渲染失败" in result.jobs[0]["reason"]
        # 失败成本照计：spent = 两条失败的预估 + 一条成功的实际
        expected = (
            adapter.estimate(edls[0], library)
            + adapter.estimate(edls[1], library)
            + adapter.render(edls[2], library).actual_cost_usd
        )
        assert result.spent_usd == pytest.approx(expected)
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        failed = [n for n in nodes if n.status is NodeStatus.FAILED]
        assert len(failed) == 2 and all(n.score is None for n in failed)


class Test素材可行性预检:
    def test_不足最小镜头数执行前拒绝(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine, structure,
    ):
        """C2 场景 5：镜头数不足最小可行数 → 执行前拒绝（树都不建，0 副作用）。"""
        tiny = ShotLibrary(
            shots=[
                ShotEntry(
                    shot_id="shot-only", artifact_hash="ff" * 32,
                    duration_ms=4000, scene_id="scene-a",
                )
            ]
        )
        # 目标下限 20000ms / max_shot_ms 5000 → 最少 4 镜头
        cfg = _config(
            target_duration_s=30,
            duration_tolerance_s=10,
            shot_limits={"min_shot_ms": 500, "max_shot_ms": 5000},
        )
        with pytest.raises(EditingLoopError, match="素材不足"):
            _run(
                "r5", _StubPolicy([make_edl()]), tree_store, artifact_store, adapter,
                editing_jobs_engine, cfg, tiny, structure,
            )
        assert tree_store.trees_by(project_id="editing", agent_id="editing") == []

    def test_总长不足预检_FAILED_注明(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        """C2：素材总长 < 目标时长下限 → FAILED 节点如实落盘注明，0 渲染 0 成本。"""
        cfg = _config(target_duration_s=60, duration_tolerance_s=10)  # 下限 50s > 素材 31s
        result = _run(
            "r6", _StubPolicy(_three_edls(make_edl)), tree_store, artifact_store, adapter,
            editing_jobs_engine, cfg, library, structure,
        )
        assert result.jobs == []
        assert "素材" in result.precheck
        assert adapter.render_calls == 0
        assert result.spent_usd == 0.0
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert len(nodes) == 1
        assert nodes[0].status is NodeStatus.FAILED
        assert "素材" in nodes[0].observation_context["reject_reason"]


class Test冻结入池门禁:
    def test_全终态可冻结(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        _run(
            "r7", _StubPolicy(_three_edls(make_edl)), tree_store, artifact_store, adapter,
            editing_jobs_engine, _config(), library, structure,
        )
        tree = freeze_round_tree("r7", tree_store, editing_jobs_engine)
        assert tree.tree_id == round_tree_id("r7")

    def test_未终态拒绝冻结(
        self, make_edl, tree_store, artifact_store, adapter, editing_jobs_engine,
        library, structure,
    ):
        """004/006 同构：job 未到 inserted/failed 终态不得冻结入池。"""
        result = _run(
            "r8", _StubPolicy(_three_edls(make_edl)), tree_store, artifact_store, adapter,
            editing_jobs_engine, _config(), library, structure,
        )
        with editing_jobs_engine.begin() as conn:  # 直接造一个 pending 中间态
            conn.execute(
                insert(edit_render_jobs).values(
                    job_id="r8-jx", round_id="r8", edl_json="{}", edl_hash="ab" * 32,
                    status="pending", estimated_cost_usd=0.1, actual_cost_usd=None,
                    artifact_hash=None, error=None, created_at="2026-09-20T00:00:00Z",
                )
            )
        with pytest.raises(EditingLoopError, match="终态"):
            freeze_round_tree("r8", tree_store, editing_jobs_engine)
        assert result.tree_id == round_tree_id("r8")

    def test_轮次不存在拒绝(self, tree_store, editing_jobs_engine):
        with pytest.raises(EditingLoopError, match="不存在"):
            freeze_round_tree("ghost", tree_store, editing_jobs_engine)
