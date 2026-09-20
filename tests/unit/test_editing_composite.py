"""剪辑合成评分与执行器接线单测（功能 007 / T723，先于实现编写）。

C9：三 gate 短路（gate 违规不跑 judge——网关调用计数不增）+ proxy/judge
适用权重归一 + quantize 6 位定点；注册元数据断言（五评估器 cost_per_call
显式 ≥ 0、deterministic=True、kind 正确——judge cost_per_call > 0）；
与 004 视觉系评估器对比样本（同工件各评一次，得分域与 diagnostics 结构一致）。
T727 接线：loop 以真实五评估器替换 US1 桩（evaluators=None 默认装配）。
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from agents.editing.evaluators import build_editing_evaluators
from agents.editing.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_editing,
    evaluate_editing,
)
from agents.editing.loop import run_editing_round
from agents.editing.platform.simulated import SimulatedEditRenderer
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.quantize import quantize_score
from core.tree.models import NodeStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_WEIGHTS = {
    "rule.duration_compliance": "gate",
    "rule.shot_distribution": "gate",
    "rule.transition_rules": "gate",
    "proxy.pacing_curve": 0.6,
    "judge.narrative_flow": 0.4,
}


def _config_dict() -> dict:
    """接线用配置：时长容差放宽覆盖夹具 EDL（11.5s~16.75s），渲染尺寸缩小提速。"""
    config = copy.deepcopy(_REAL_CONFIG)
    config["editing"]["target_duration_s"] = 12
    config["editing"]["duration_tolerance_s"] = 8
    config["editing"]["render"].update(width=64, height=48)
    return config


@pytest.fixture()
def evaluators(mock_gateway):
    """编排测试用评估器：时长窗口放宽覆盖夹具成片（11.5s~16.75s）。"""
    from agents.editing.config import EditingConfig

    return build_editing_evaluators(EditingConfig.from_dict(_config_dict()), mock_gateway)


@pytest.fixture()
def ctx(make_edl, make_shot_library, make_scene_structure):
    library = make_shot_library()
    return {
        "edl": make_edl(),
        "shot_library": library,
        "scene_structure": make_scene_structure(library=library),
    }


def _artifact(duration_ms: int = 16750, shot_durations_ms=(3000, 3000, 4000, 3000, 5000)):
    return ArtifactRef(
        artifact_hash="ab" * 32,
        metadata={"duration_ms": duration_ms, "shot_durations_ms": list(shot_durations_ms)},
    )


class Test合成函数:
    def test_gate判0总分0(self):
        breakdown = {
            "rule.duration_compliance@1": {"score": 0.0, "diagnostics": {}},
            "proxy.pacing_curve@1": {"score": 0.9, "diagnostics": {}},
            "judge.narrative_flow@1": {"score": 0.9, "diagnostics": {}},
        }
        assert composite_editing(breakdown, _WEIGHTS) == 0.0

    def test_适用分量加权归一(self):
        breakdown = {
            "rule.duration_compliance@1": {"score": 1.0, "diagnostics": {}},
            "rule.shot_distribution@1": {"score": 1.0, "diagnostics": {}},
            "rule.transition_rules@1": {"score": 1.0, "diagnostics": {}},
            "proxy.pacing_curve@1": {"score": 0.8, "diagnostics": {}},
            "judge.narrative_flow@1": {"score": 0.6, "diagnostics": {}},
        }
        # (0.6×0.8 + 0.4×0.6) / (0.6 + 0.4) = 0.72
        assert composite_editing(breakdown, _WEIGHTS) == pytest.approx(0.72)

    def test_judge缺席按适用权重归一(self):
        """gate 短路时 judge 分量缺席：归一分母不含其权重（不伪造 0 分拖底）。"""
        breakdown = {
            "rule.duration_compliance@1": {"score": 1.0, "diagnostics": {}},
            "proxy.pacing_curve@1": {"score": 0.8, "diagnostics": {}},
        }
        assert composite_editing(breakdown, _WEIGHTS) == pytest.approx(0.8)

    def test_quantize定点收口(self):
        breakdown = {
            "proxy.pacing_curve@1": {"score": 1 / 3, "diagnostics": {}},
        }
        score = quantize_score(composite_editing(breakdown, _WEIGHTS))
        assert score == round(1 / 3, 6)


class Test合成编排:
    def test_全过则五分量齐全且_judge_计费(self, evaluators, mock_gateway, ctx):
        before = mock_gateway.call_count
        breakdown, score, judge_usage = evaluate_editing(evaluators, _artifact(), ctx, _WEIGHTS)
        assert len(breakdown) == 5  # 三 gate + pacing + judge
        assert mock_gateway.call_count - before == 6  # 3 提示词 × 2 锚点
        assert judge_usage["llm_calls"] == 6
        assert 0.0 <= score <= 1.0
        assert score == round(score, 6)

    def test_gate违规短路不跑judge(self, evaluators, mock_gateway, ctx):
        """C9 场景 6：时长 gate 判 0 → judge 未调用（网关计数不增），总分 0。"""
        before = mock_gateway.call_count
        breakdown, score, judge_usage = evaluate_editing(
            evaluators, _artifact(duration_ms=999999), ctx, _WEIGHTS
        )
        assert score == 0.0
        assert mock_gateway.call_count == before  # judge 未被调用（省 LLM 成本）
        assert judge_usage["llm_calls"] == 0
        assert len(breakdown) == 4  # 三 gate + pacing；judge 缺席
        gate_keys = [k for k in breakdown if k.startswith("rule.")]
        assert any(breakdown[k]["score"] == 0.0 for k in gate_keys)


class Test注册元数据:
    def test_五评估器元数据(self, evaluators):
        """宪章三件套之注册元数据：id 与权重键一一对应、kind/deterministic/cost 显式。"""
        specs = [e.spec for e in evaluators["all"]]
        assert {s.evaluator_id for s in specs} == set(_WEIGHTS)
        kinds = {s.evaluator_id: s.kind for s in specs}
        assert kinds["rule.duration_compliance"] is EvaluatorKind.RULE
        assert kinds["rule.shot_distribution"] is EvaluatorKind.RULE
        assert kinds["rule.transition_rules"] is EvaluatorKind.RULE
        assert kinds["proxy.pacing_curve"] is EvaluatorKind.PROXY_MODEL
        assert kinds["judge.narrative_flow"] is EvaluatorKind.JUDGE
        for spec in specs:
            assert spec.deterministic is True
            assert spec.cost_per_call >= 0.0  # 显式声明（judge > 0）
        assert next(s for s in specs if s.evaluator_id == "judge.narrative_flow").cost_per_call > 0

    def test_对比样本_与004视觉系同构(self, evaluators, visual_config):
        """同工件经新旧评估器各评一次：得分域 [0,1] 与 diagnostics dict 结构一致。"""
        from agents.visual.evaluators.aesthetic import AestheticEvaluator

        artifact = ArtifactRef(artifact_hash="ef" * 32)
        visual_evaluator = AestheticEvaluator(visual_config.frame_sampling)
        frames = np.zeros((8, 64, 64, 3), dtype=np.uint8)
        visual_result = visual_evaluator.evaluate(
            artifact,
            {"samples": SimpleNamespace(frames_rgb=frames, frames_gray=frames[..., 0])},
        )
        editing_result = evaluators["gates"][0].evaluate(
            ArtifactRef(artifact_hash="ef" * 32, metadata={"duration_ms": 120000}), {}
        )
        for result in (visual_result, editing_result):
            assert 0.0 <= result.score <= 1.0
            assert isinstance(result.diagnostics, dict)


class Test执行器接线:
    """T727：evaluators=None 默认装配真实五评估器（gateway 必传）。"""

    @pytest.fixture()
    def editing_config_small(self):
        from agents.editing.config import EditingConfig

        return EditingConfig.from_dict(_config_dict())

    @pytest.fixture()
    def adapter(self, editing_config_small):
        return SimulatedEditRenderer(editing_config_small.render)

    def _run(
        self,
        round_id,
        edls,
        tree_store,
        artifact_store,
        adapter,
        engine,
        cfg,
        library,
        structure,
        gateway,
    ):
        class _Policy:
            policy_version = "stub-editing-v1"

            def plan(self, config, inputs):
                return list(edls)

        return run_editing_round(
            round_id=round_id,
            policy=_Policy(),
            store=tree_store,
            artifacts=artifact_store,
            adapter=adapter,
            engine=engine,
            config=cfg,
            inputs={"shot_library": library, "scene_structure": structure},
            evaluators=None,  # 默认装配真实五评估器
            gateway=gateway,
        )

    def test_真实五评估器落树(
        self,
        make_edl,
        tree_store,
        artifact_store,
        adapter,
        editing_jobs_engine,
        editing_config_small,
        make_shot_library,
        make_scene_structure,
        mock_gateway,
    ):
        library = make_shot_library()
        structure = make_scene_structure(library=library)
        result = self._run(
            "us2-1",
            [make_edl()],
            tree_store,
            artifact_store,
            adapter,
            editing_jobs_engine,
            editing_config_small,
            library,
            structure,
            mock_gateway,
        )
        assert result.jobs[0]["status"] == "inserted"
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        breakdown = nodes[0].eval_breakdown
        assert len(breakdown) == 5  # 五分量齐全
        assert {k.split("@")[0] for k in breakdown} == set(_WEIGHTS)
        assert nodes[0].score == round(nodes[0].score, 6)
        assert nodes[0].cost.llm_calls == 6  # judge 计费用量入节点成本
        # 合成口径版本元信息进快照（US1 桩口径已替换）
        tree = tree_store.trees_by(project_id="editing", agent_id="editing")[0]
        assert tree.config_snapshot["composite_policy"] == COMPOSITE_POLICY

    def test_gate违规节点judge未调用(
        self,
        make_edl,
        tree_store,
        artifact_store,
        adapter,
        editing_jobs_engine,
        editing_config_small,
        make_shot_library,
        make_scene_structure,
        mock_gateway,
    ):
        """含 300ms 镜头（< min_shot_ms 500）→ 分布 gate 判 0：总分 0 且网关 0 调用。"""
        library = make_shot_library()
        structure = make_scene_structure(library=library)
        bad_edl = make_edl(
            clips=[
                {
                    "shot_id": "shot-1",
                    "in_ms": 0,
                    "out_ms": 300,  # 300ms 碎片化镜头
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-5",
                    "in_ms": 0,
                    "out_ms": 6000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ]
        )
        before = mock_gateway.call_count
        result = self._run(
            "us2-2",
            [bad_edl],
            tree_store,
            artifact_store,
            adapter,
            editing_jobs_engine,
            editing_config_small,
            library,
            structure,
            mock_gateway,
        )
        assert result.jobs[0]["status"] == "inserted"  # 非法≠失败：如实落树判 0
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert nodes[0].score == 0.0
        assert nodes[0].status is NodeStatus.EVALUATED
        assert mock_gateway.call_count == before  # gate 短路：judge 0 调用
