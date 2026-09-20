"""分镜合成评分与执行器接线单测（功能 008 / T823，先于实现编写）。

C9：三 gate 短路（gate 违规不跑 judge——网关调用计数不增）+ alignment/judge
适用权重归一（不适用分量跳过，分母不含其权重）+ quantize 6 位定点；
注册元数据断言（五评估器 cost_per_call 显式 ≥ 0、deterministic=True、kind 正确——
judge cost_per_call > 0）；与 004/006/007 既有评估器对比样本（同工件各评一次，
得分域 [0,1] 与 diagnostics dict 结构一致——宪章测试纪律三件套）。

T827 接线：loop 以真实五评估器替换 US1 桩（evaluators=None 默认装配真实五评估器，
保留显式注入路径供 US3 回放）。
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.evaluators import build_storyboard_evaluators
from agents.storyboard.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_storyboard,
    evaluate_storyboard,
)
from agents.storyboard.loop import run_storyboard_round
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.storyboard.shotlist import ShotList
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.quantize import quantize_score
from core.tree.models import NodeStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_WEIGHTS = {
    "rule.shot_grammar": "gate",
    "rule.coverage": "gate",
    "rule.axis_rule": "gate",
    "proxy.emotion_alignment": 0.5,
    "judge.script_fit": 0.5,
}


def _config() -> StoryboardConfig:
    """接线用配置：渲染尺寸缩小提速（64x48）。"""
    config = copy.deepcopy(_REAL_CONFIG)
    config["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(config)


@pytest.fixture()
def config():
    return _config()


@pytest.fixture()
def evaluators(mock_gateway, config):
    return build_storyboard_evaluators(config, mock_gateway)


@pytest.fixture()
def ctx(make_shotlist, make_script_segment):
    return {"shotlist": make_shotlist(), "script": make_script_segment()}


def _artifact(shotlist, script, config) -> ArtifactRef:
    animatic = SimulatedStoryboardRenderer().render(shotlist, script, config)
    return ArtifactRef(artifact_hash="ab" * 32, metadata=animatic.metadata)


class Test合成函数:
    def test_gate判0总分0(self):
        """C9 场景 1：任一 gate 判 0 → 总分 0（不可行解，无视 alignment/judge 得分）。"""
        breakdown = {
            "rule.coverage@1": {"score": 0.0, "diagnostics": {}},
            "proxy.emotion_alignment@1": {"score": 0.99, "diagnostics": {}},
            "judge.script_fit@1": {"score": 0.9, "diagnostics": {}},
        }
        assert composite_storyboard(breakdown, _WEIGHTS) == 0.0

    def test_适用分量加权归一(self):
        breakdown = {
            "rule.shot_grammar@1": {"score": 1.0, "diagnostics": {}},
            "rule.coverage@1": {"score": 1.0, "diagnostics": {}},
            "rule.axis_rule@1": {"score": 1.0, "diagnostics": {}},
            "proxy.emotion_alignment@1": {"score": 0.8, "diagnostics": {}},
            "judge.script_fit@1": {"score": 0.6, "diagnostics": {}},
        }
        # (0.5×0.8 + 0.5×0.6) / (0.5 + 0.5) = 0.7；gate 分量不参与加权和
        assert composite_storyboard(breakdown, _WEIGHTS) == pytest.approx(0.7)

    def test_judge缺席按适用权重归一(self):
        """gate 短路时 judge 分量缺席：归一分母不含其权重（不伪造 0 分拖底）。"""
        breakdown = {
            "rule.shot_grammar@1": {"score": 0.0, "diagnostics": {}},
            "proxy.emotion_alignment@1": {"score": 0.8, "diagnostics": {}},
        }
        assert composite_storyboard(breakdown, _WEIGHTS) == 0.0  # gate 短路优先

    def test_不适用分量跳过按适用权重归一(self):
        """情绪缺失（不适用）→ alignment 跳过，分母不含其权重（规格边界情况）。"""
        breakdown = {
            "rule.shot_grammar@1": {"score": 1.0, "diagnostics": {}},
            "rule.coverage@1": {"score": 1.0, "diagnostics": {}},
            "rule.axis_rule@1": {"score": 1.0, "diagnostics": {}},
            "proxy.emotion_alignment@1": {
                "score": 0.0,
                "diagnostics": {"applicable": False, "note": "情绪缺失"},
            },
            "judge.script_fit@1": {"score": 0.7, "diagnostics": {}},
        }
        assert composite_storyboard(breakdown, _WEIGHTS) == pytest.approx(0.7)

    def test_无适用分量退化0(self):
        breakdown = {
            "rule.shot_grammar@1": {"score": 1.0, "diagnostics": {}},
            "proxy.emotion_alignment@1": {"score": 0.0, "diagnostics": {"applicable": False}},
        }
        assert composite_storyboard(breakdown, _WEIGHTS) == 0.0

    def test_quantize定点收口(self):
        breakdown = {"proxy.emotion_alignment@1": {"score": 1 / 3, "diagnostics": {}}}
        score = quantize_score(composite_storyboard(breakdown, _WEIGHTS))
        assert score == round(1 / 3, 6)

    def test_gate与覆盖率冲突不自动豁免(self):
        """边界②闭环：coverage 通过但越轴冲突 → axis 判 0 → 合成 0（不自动豁免）。"""
        breakdown = {
            "rule.shot_grammar@1": {"score": 1.0, "diagnostics": {}},
            "rule.coverage@1": {
                "score": 1.0,
                "diagnostics": {"conflicts": ["覆盖率与轴规则冲突"]},
            },
            "rule.axis_rule@1": {"score": 0.0, "diagnostics": {"violations": ["侧别硬跳"]}},
            "proxy.emotion_alignment@1": {"score": 0.99, "diagnostics": {}},
        }
        assert composite_storyboard(breakdown, _WEIGHTS) == 0.0


class Test合成编排:
    def test_全过则五分量齐全且_judge_计费(self, evaluators, mock_gateway, ctx, config):
        script, shotlist = ctx["script"], ctx["shotlist"]
        before = mock_gateway.call_count
        breakdown, score, judge_usage = evaluate_storyboard(
            evaluators, _artifact(shotlist, script, config), ctx, _WEIGHTS
        )
        assert len(breakdown) == 5  # 三 gate + alignment + judge
        assert mock_gateway.call_count - before == 6  # 3 提示词 × 2 锚点
        assert judge_usage["llm_calls"] == 6
        assert judge_usage["cost_usd"] > 0
        assert 0.0 <= score <= 1.0
        assert score == round(score, 6)

    def test_gate违规短路不跑judge(self, evaluators, mock_gateway, ctx, config, make_shotlist):
        """C9 场景 6：景别语法 gate 判 0 → judge 未调用（网关计数不增），总分 0。"""
        shots = make_shotlist().to_dict()["shots"]
        shots[1] = {**shots[1], "shot_size": "wide"}  # close_up→wide 跳跃 3 > 2
        shotlist = ShotList(shots=shots)
        script = ctx["script"]
        before = mock_gateway.call_count
        breakdown, score, judge_usage = evaluate_storyboard(
            evaluators,
            _artifact(shotlist, script, config),
            {"shotlist": shotlist, "script": script},
            _WEIGHTS,
        )
        assert score == 0.0
        assert mock_gateway.call_count == before  # judge 未被调用（省 LLM 成本）
        assert judge_usage["llm_calls"] == 0
        assert len(breakdown) == 4  # 三 gate + alignment；judge 缺席
        gate_keys = [k for k in breakdown if k.startswith("rule.")]
        assert any(breakdown[k]["score"] == 0.0 for k in gate_keys)


class Test注册元数据:
    def test_五评估器元数据(self, evaluators):
        """宪章三件套之注册元数据：id 与权重键一一对应、kind/deterministic/cost 显式。"""
        specs = [e.spec for e in evaluators["all"]]
        assert {s.evaluator_id for s in specs} == set(_WEIGHTS)
        kinds = {s.evaluator_id: s.kind for s in specs}
        assert kinds["rule.shot_grammar"] is EvaluatorKind.RULE
        assert kinds["rule.coverage"] is EvaluatorKind.RULE
        assert kinds["rule.axis_rule"] is EvaluatorKind.RULE
        assert kinds["proxy.emotion_alignment"] is EvaluatorKind.PROXY_MODEL
        assert kinds["judge.script_fit"] is EvaluatorKind.JUDGE
        for spec in specs:
            assert spec.deterministic is True
            assert spec.cost_per_call >= 0.0  # 显式声明（judge > 0）
        assert next(s for s in specs if s.evaluator_id == "judge.script_fit").cost_per_call > 0

    def test_对比样本_与既有形态评估器同构(self, evaluators, ctx, config, visual_config):
        """同工件经 004/006/007/008 评估器各评一次：得分域与 diagnostics 结构一致。"""
        from agents.editing.evaluators.duration import DurationComplianceEvaluator
        from agents.sound.evaluators.emotion import EmotionMusicMatchEvaluator
        from agents.visual.evaluators.aesthetic import AestheticEvaluator

        script, shotlist = ctx["script"], ctx["shotlist"]
        artifact = _artifact(shotlist, script, config)
        frames = np.zeros((8, 64, 64, 3), dtype=np.uint8)
        results = [
            AestheticEvaluator(visual_config.frame_sampling).evaluate(
                artifact,
                {"samples": SimpleNamespace(frames_rgb=frames, frames_gray=frames[..., 0])},
            ),
            EmotionMusicMatchEvaluator(0.5).evaluate(
                artifact,
                {
                    "gen_type": "music",
                    "metadata": {"emotion_vector": [0.5, 0.5]},
                    "gen_params": {"seed": 3},
                },
            ),
            DurationComplianceEvaluator(120.0, 10.0).evaluate(
                ArtifactRef(artifact_hash="ef" * 32, metadata={"duration_ms": 120000}), {}
            ),
            evaluators["gates"][0].evaluate(artifact, ctx),
        ]
        for result in results:
            assert 0.0 <= result.score <= 1.0
            assert isinstance(result.diagnostics, dict)


class Test执行器接线:
    """T827：evaluators=None 默认装配真实五评估器（gateway 必传）。"""

    @pytest.fixture()
    def adapter(self):
        return SimulatedStoryboardRenderer()

    def _run(
        self,
        round_id,
        shotlists,
        tree_store,
        artifact_store,
        adapter,
        engine,
        cfg,
        script,
        gateway,
        evaluators=None,
    ):
        class _Policy:
            policy_version = "stub-storyboard-v1"

            def plan(self, config, inputs):
                return list(shotlists)

        return run_storyboard_round(
            round_id=round_id,
            policy=_Policy(),
            store=tree_store,
            artifacts=artifact_store,
            adapter=adapter,
            engine=engine,
            config=cfg,
            inputs={"script": script},
            evaluators=evaluators,  # None → 默认装配真实五评估器
            gateway=gateway,
        )

    def test_真实五评估器落树(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
        mock_gateway,
    ):
        result = self._run(
            "us2-1",
            [make_shotlist()],
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            make_script_segment(),
            mock_gateway,
        )
        assert result.jobs[0]["status"] == "inserted"
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        breakdown = nodes[0].eval_breakdown
        assert len(breakdown) == 5  # 五分量齐全
        assert {key.split("@")[0] for key in breakdown} == set(_WEIGHTS)
        assert nodes[0].score == round(nodes[0].score, 6)
        assert 0.0 < nodes[0].score <= 1.0  # 合法夹具全过门禁 → 正分
        assert nodes[0].cost.llm_calls == 6  # judge 计费用量入节点成本
        assert nodes[0].cost.llm_tokens > 0
        # 合成口径版本元信息进快照（US1 桩口径已替换为正式编排）
        trees = tree_store.trees_by(agent_id="storyboard")
        tree = next(t for t in trees if t.tree_id == result.tree_id)
        assert tree.config_snapshot["composite_policy"] == COMPOSITE_POLICY
        assert set(tree.config_snapshot["evaluator_versions"]) == set(_WEIGHTS)

    def test_gate违规节点judge未调用(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
        mock_gateway,
    ):
        """景别跳跃超限（执行前校验只查枚举，语法由 gate 判）→ 总分 0 且网关 0 调用。"""
        shots = make_shotlist().to_dict()["shots"]
        shots[1] = {**shots[1], "shot_size": "wide"}  # 相邻景别跳跃 3 > max_size_jump=2
        before = mock_gateway.call_count
        result = self._run(
            "us2-2",
            [ShotList(shots=shots)],
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            make_script_segment(),
            mock_gateway,
        )
        assert result.jobs[0]["status"] == "inserted"  # 非法≠失败：如实落树判 0
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert nodes[0].score == 0.0
        assert nodes[0].status is NodeStatus.EVALUATED
        assert mock_gateway.call_count == before  # gate 短路：judge 0 调用
        assert nodes[0].cost.llm_calls == 0

    def test_显式注入路径保留(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
        mock_gateway,
    ):
        """US3 回放复用：显式注入评估器列表时走桩路径（不触发真实 judge 计费）。"""
        from tests.stubs import StubProxyEvaluator, StubRuleEvaluator

        before = mock_gateway.call_count
        result = self._run(
            "us2-3",
            [make_shotlist()],
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            make_script_segment(),
            mock_gateway,
            evaluators=[
                StubRuleEvaluator("rule.coverage"),
                StubProxyEvaluator("proxy.emotion_alignment", score=0.8),
            ],
        )
        assert result.jobs[0]["status"] == "inserted"
        assert mock_gateway.call_count == before
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert nodes[0].score == pytest.approx(0.8)
        assert len(nodes[0].eval_breakdown) == 2

    def test_缺网关拒绝装配(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """evaluators=None 且无网关 → 明确报错（不静默无打分）。"""
        from agents.storyboard.loop import StoryboardLoopError

        with pytest.raises(StoryboardLoopError, match="网关"):
            self._run(
                "us2-4",
                [make_shotlist()],
                tree_store,
                artifact_store,
                adapter,
                storyboard_jobs_engine,
                config,
                make_script_segment(),
                None,
            )
