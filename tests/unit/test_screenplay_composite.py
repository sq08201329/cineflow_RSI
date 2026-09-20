"""剧本合成评分与执行器接线单测（功能 009 / T919，先于实现编写）。

C11：四 gate 短路（gate 违规不跑 judge——网关调用计数不增）+ 适用权重归一
（judge 非大纲阶段"不适用"跳过，分母不含其权重，不伪造 0 分拖底）+ quantize 6 位定点；
注册元数据三件套（七评估器 id ↔ 权重键一一对应、kind 正确、deterministic=True、
cost_per_call 显式 ≥ 0 且 judge > 0）+ 与 004/006/007/008 既有评估器对比样本
（同工件各评一次：得分域 [0,1] 与 diagnostics dict 结构一致——宪章测试纪律三件套）。

T923 接线：loop 以真实七评估器替换 US1 临时口径（evaluators=None 默认装配真实七评估器，
需网关；显式注入路径保留供 US3 回放/无偏性；空列表仍拒绝）。
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.evaluators import build_screenplay_evaluators
from agents.screenplay.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_screenplay,
    evaluate_screenplay,
)
from agents.screenplay.loop import ScreenplayLoopError, run_screenplay_round
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.quantize import quantize_score
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.tree.models import NodeStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_WEIGHTS = _REAL_CONFIG["evaluator_weights"]["screenplay"]
_GATE_IDS = (
    "rule.beat_structure",
    "rule.page_minutes",
    "rule.scene_character",
    "rule.dialogue_action_ratio",
)
_PROXY_IDS = ("proxy.entity_consistency", "proxy.timeline_conflict")
_JUDGE_ID = "judge.dramatic_tension"

_INPUTS = {
    "topic": "病房里的三个月",
    "target_duration_min": 3,
    "constraints": ["单场景为主"],
    "characters": ["林静", "陈默", "周医生"],
}


def _config(**overrides) -> ScreenplayConfig:
    """夹具配置：真实 movie.yaml + 页数窗口缩放（默认 9 行夹具 → 3 页）+ 定向覆盖。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    raw["screenplay"].update({"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3})
    raw["screenplay"].update(overrides)
    return ScreenplayConfig.from_dict(raw)


def _plan_of(artifact) -> dict:
    payload = artifact.to_dict()
    return {key: payload[key] for key in ("beats", "scenes", "characters", "lines")}


class _StubPolicy:
    """策略桩：产分阶段计划（三段工件派生；缺关键节拍变体用于 gate 短路用例）。"""

    policy_version = "9f2c41ab77de"

    def __init__(self, plans):
        self._plans = plans

    def plan(self, inputs, config):
        import copy as _copy

        return _copy.deepcopy(self._plans)


def _plans(make_script_artifact, *, variant: str = "valid") -> dict:
    return {
        stage: _plan_of(make_script_artifact(variant, stage=stage))
        for stage in ("outline", "scenes", "script")
    }


@pytest.fixture()
def config():
    return _config()


@pytest.fixture()
def gateway(config):
    """剧本网关：价目取自 ScreenplayConfig.model_prices（缺价目即报错），退避置零。"""
    return LLMGateway(MockBackend(), price_book=config.model_prices, sleep=lambda _: None)


@pytest.fixture()
def evaluators(gateway, config):
    return build_screenplay_evaluators(config, gateway)


@pytest.fixture()
def ctx(script_artifact):
    return {"artifact": script_artifact}


def _artifact() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


class Test合成函数:
    def test_gate判零总分零(self):
        """C11：任一 gate 判 0 → 总分 0（不可行解，无视 proxy/judge 得分）。"""
        breakdown = {
            "rule.page_minutes@1": {"score": 0.0, "diagnostics": {}},
            "proxy.entity_consistency@1": {"score": 0.9, "diagnostics": {}},
            "judge.dramatic_tension@1": {"score": 0.9, "diagnostics": {}},
        }
        assert composite_screenplay(breakdown, _WEIGHTS) == 0.0

    def test_适用分量加权归一(self):
        """(0.5×0.8 + 0.5×0.6 + 0.5×0.7) / 1.5 = 0.7；gate 分量不参与加权和。"""
        breakdown = {
            "rule.beat_structure@1": {"score": 1.0, "diagnostics": {}},
            "rule.page_minutes@1": {"score": 1.0, "diagnostics": {}},
            "rule.scene_character@1": {"score": 1.0, "diagnostics": {}},
            "rule.dialogue_action_ratio@1": {"score": 1.0, "diagnostics": {}},
            "proxy.entity_consistency@1": {"score": 0.8, "diagnostics": {}},
            "proxy.timeline_conflict@1": {"score": 0.6, "diagnostics": {}},
            "judge.dramatic_tension@1": {"score": 0.7, "diagnostics": {}},
        }
        assert composite_screenplay(breakdown, _WEIGHTS) == pytest.approx(0.7)

    def test_不适用分量跳过并按适用权重归一(self):
        """judge 非大纲阶段"不适用"→ 跳过且分母不含其权重（不伪造 0 分拖底）。"""
        breakdown = {
            "rule.beat_structure@1": {"score": 1.0, "diagnostics": {}},
            "rule.page_minutes@1": {"score": 1.0, "diagnostics": {}},
            "rule.scene_character@1": {"score": 1.0, "diagnostics": {}},
            "rule.dialogue_action_ratio@1": {"score": 1.0, "diagnostics": {}},
            "proxy.entity_consistency@1": {"score": 0.8, "diagnostics": {}},
            "proxy.timeline_conflict@1": {"score": 0.6, "diagnostics": {}},
            "judge.dramatic_tension@1": {
                "score": 0.0,
                "diagnostics": {"applicable": False, "note": "仅大纲阶段"},
            },
        }
        assert composite_screenplay(breakdown, _WEIGHTS) == pytest.approx(0.7)

    def test_judge缺席按适用权重归一(self):
        """gate 短路时 judge 分量缺席：分母不含其权重，但 gate 短路优先判 0。"""
        breakdown = {
            "rule.beat_structure@1": {"score": 0.0, "diagnostics": {}},
            "proxy.entity_consistency@1": {"score": 0.8, "diagnostics": {}},
        }
        assert composite_screenplay(breakdown, _WEIGHTS) == 0.0

    def test_无适用分量退化0(self):
        breakdown = {
            "rule.beat_structure@1": {"score": 1.0, "diagnostics": {}},
            "proxy.timeline_conflict@1": {"score": 0.0, "diagnostics": {"applicable": False}},
        }
        assert composite_screenplay(breakdown, _WEIGHTS) == 0.0

    def test_quantize定点收口(self):
        breakdown = {"proxy.timeline_conflict@1": {"score": 1 / 3, "diagnostics": {}}}
        assert quantize_score(composite_screenplay(breakdown, _WEIGHTS)) == round(1 / 3, 6)


class Test合成编排:
    def test_全过则七分量齐全且_judge_计费(self, evaluators, gateway, ctx, config):
        before = gateway.call_count
        breakdown, score, judge_usage = evaluate_screenplay(evaluators, _artifact(), ctx, _WEIGHTS)
        assert len(breakdown) == 7  # 四 gate + 两 proxy + judge
        assert gateway.call_count - before == 6  # 3 提示词 × 2 锚点
        assert judge_usage["llm_calls"] == 6
        assert judge_usage["cost_usd"] > 0
        assert 0.0 < score <= 1.0
        assert score == round(score, 6)
        assert {key.split("@")[0] for key in breakdown} == set(_WEIGHTS)

    @pytest.mark.parametrize("stage", ["scenes", "script"])
    def test_非大纲阶段_judge_不适用且零调用(
        self, evaluators, gateway, config, make_script_artifact, stage
    ):
        """C11：非大纲阶段 judge 分量"不适用"（键仍在 breakdown，合成按适用归一）。"""
        before = gateway.call_count
        breakdown, score, judge_usage = evaluate_screenplay(
            evaluators,
            _artifact(),
            {"artifact": make_script_artifact(stage=stage)},
            _WEIGHTS,
        )
        assert gateway.call_count == before
        assert judge_usage["llm_calls"] == 0
        judge_fragment = next(
            fragment for key, fragment in breakdown.items() if key.startswith("judge.")
        )
        assert judge_fragment["diagnostics"]["applicable"] is False
        # 合成 = 两 proxy 加权归一（judge 与 gate 不入分母）
        assert score == pytest.approx(round((0.5 * 1.0 + 0.5 * 1.0) / 1.0, 6))

    def test_gate违规短路不跑_judge(self, evaluators, gateway, config, make_script_artifact):
        """C11 场景 8：任一 gate 判 0 → judge 未调用（网关计数不增）、总分 0。"""
        payload = make_script_artifact().to_dict()
        payload["beats"] = [beat for beat in payload["beats"] if beat["beat_id"] != "climax"]
        from agents.screenplay.artifact import ScriptArtifact

        artifact = ScriptArtifact.from_dict(payload)
        before = gateway.call_count
        breakdown, score, judge_usage = evaluate_screenplay(
            evaluators, _artifact(), {"artifact": artifact}, _WEIGHTS
        )
        assert score == 0.0
        assert gateway.call_count == before  # judge 未被调用（省 LLM 成本）
        assert judge_usage["llm_calls"] == 0
        assert len(breakdown) == 6  # 四 gate + 两 proxy；judge 缺席
        assert any(
            fragment["score"] == 0.0
            for key, fragment in breakdown.items()
            if key.startswith("rule.")
        )


class Test注册元数据:
    def test_七评估器元数据(self, evaluators):
        """宪章三件套之注册元数据：id 与权重键一一对应、kind/deterministic/cost 显式。"""
        specs = [evaluator.spec for evaluator in evaluators["all"]]
        assert {spec.evaluator_id for spec in specs} == set(_WEIGHTS)
        kinds = {spec.evaluator_id: spec.kind for spec in specs}
        for gate_id in _GATE_IDS:
            assert kinds[gate_id] is EvaluatorKind.RULE
        for proxy_id in _PROXY_IDS:
            assert kinds[proxy_id] is EvaluatorKind.PROXY_MODEL
        assert kinds[_JUDGE_ID] is EvaluatorKind.JUDGE
        for spec in specs:
            assert spec.deterministic is True
            assert spec.cost_per_call >= 0.0  # 显式声明（judge > 0）
        assert next(s for s in specs if s.evaluator_id == _JUDGE_ID).cost_per_call > 0

    def test_装配与权重节一一对应(self, config, gateway):
        """权重节与装配的评估器集合必须一致（缺项/多项即拒绝装配，防配置漂移）。"""
        from agents.screenplay.config import ScreenplayConfigError

        raw = copy.deepcopy(_REAL_CONFIG)
        raw["screenplay"].update(
            {"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3}
        )
        del raw["evaluator_weights"]["screenplay"]["proxy.timeline_conflict"]
        with pytest.raises(ScreenplayConfigError, match="evaluator_weights"):
            build_screenplay_evaluators(ScreenplayConfig.from_dict(raw), gateway)

    def test_对比样本_与既有形态评估器同构(
        self,
        evaluators,
        script_artifact,
        visual_config,
        storyboard_config,
        make_shotlist,
        make_script_segment,
    ):
        """同工件经 004/006/007/008 既有评估器各评一次：得分域与 diagnostics 结构一致。"""
        from agents.editing.evaluators.duration import DurationComplianceEvaluator
        from agents.sound.evaluators.emotion import EmotionMusicMatchEvaluator
        from agents.storyboard.evaluators.shot_grammar import ShotGrammarEvaluator
        from agents.visual.evaluators.aesthetic import AestheticEvaluator

        frames = np.zeros((8, 64, 64, 3), dtype=np.uint8)
        script = make_script_segment()
        ref = ArtifactRef(artifact_hash="ab" * 32)
        screenplay_ctx = {"artifact": script_artifact}
        results = [
            AestheticEvaluator(visual_config.frame_sampling).evaluate(
                ref,
                {"samples": SimpleNamespace(frames_rgb=frames, frames_gray=frames[..., 0])},
            ),
            EmotionMusicMatchEvaluator(0.5).evaluate(
                ref,
                {
                    "gen_type": "music",
                    "metadata": {"emotion_vector": [0.5, 0.5]},
                    "gen_params": {"seed": 3},
                },
            ),
            DurationComplianceEvaluator(120.0, 10.0).evaluate(
                ArtifactRef(artifact_hash="ef" * 32, metadata={"duration_ms": 120000}), {}
            ),
            ShotGrammarEvaluator(storyboard_config.shot_grammar).evaluate(
                ref, {"shotlist": make_shotlist(), "script": script}
            ),
            evaluators["gates"][0].evaluate(ref, screenplay_ctx),
            evaluators["proxies"][0].evaluate(ref, screenplay_ctx),
        ]
        for result in results:
            assert 0.0 <= result.score <= 1.0
            assert isinstance(result.diagnostics, dict)


class Test执行器接线:
    """T923：evaluators=None 默认装配真实七评估器（gateway 必传）。"""

    def _run(
        self,
        round_id,
        plans,
        tree_store,
        artifact_store,
        engine,
        config,
        gateway,
        evaluators=None,
    ):
        return run_screenplay_round(
            round_id=round_id,
            policy=_StubPolicy(plans),
            store=tree_store,
            artifacts=artifact_store,
            engine=engine,
            gateway=gateway,
            config=config,
            inputs=dict(_INPUTS),
            evaluators=evaluators,
        )

    def _nodes(self, tree_store, result):
        return {
            node.observation_context["stage"]: node
            for node in tree_store.nodes_of(result.tree_id)
            if node.parent_id is not None
        }

    def test_真实七评估器落树(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
        gateway,
    ):
        result = self._run(
            "us2-1",
            _plans(make_script_artifact),
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            config,
            gateway,
        )
        assert [job["status"] for job in result.jobs] == ["inserted"] * 3
        nodes = self._nodes(tree_store, result)
        outline = nodes["outline"]
        assert len(outline.eval_breakdown) == 7  # 七分量齐全
        assert {key.split("@")[0] for key in outline.eval_breakdown} == set(_WEIGHTS)
        assert 0.0 < outline.score <= 1.0  # 合法夹具全过门禁 → 正分
        assert outline.score == round(outline.score, 6)
        assert outline.cost.llm_calls == 1 + 6  # 生成 1 + judge 6（仅大纲阶段）
        for stage in ("scenes", "script"):
            judge_key = next(key for key in nodes[stage].eval_breakdown if key.startswith("judge."))
            assert nodes[stage].eval_breakdown[judge_key]["diagnostics"]["applicable"] is False
            assert nodes[stage].cost.llm_calls == 1  # judge 不适用 → 零 judge 调用
        assert result.cost_reconciliation["consistent"] is True
        assert result.cost_reconciliation["evaluator_cost_usd"] > 0  # judge 计费入对账
        tree = next(
            t for t in tree_store.trees_by(agent_id="screenplay") if t.tree_id == result.tree_id
        )
        assert tree.config_snapshot["composite_policy"] == COMPOSITE_POLICY
        assert set(tree.config_snapshot["evaluator_versions"]) == set(_WEIGHTS)

    def test_gate违规节点_judge_未调用(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
        gateway,
    ):
        """缺关键节拍 → gate 判 0：总分 0、judge 零调用（网关计数只增生成 3 次）。"""
        before = gateway.call_count
        result = self._run(
            "us2-2",
            _plans(make_script_artifact, variant="missing_beat"),
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            config,
            gateway,
        )
        assert gateway.call_count - before == 3  # 仅三阶段生成，judge 0 调用
        nodes = self._nodes(tree_store, result)
        for node in nodes.values():
            assert node.score == 0.0
            assert node.status is NodeStatus.EVALUATED  # 非法≠失败：如实落树判 0
            assert node.cost.llm_calls == 1
            assert len(node.eval_breakdown) == 6  # judge 缺席

    def test_显式注入路径保留(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
        gateway,
    ):
        """US3 回放复用：显式注入评估器列表走桩路径（不触发真实 judge 计费）。

        剧本生成本身即网关调用（每阶段 1 次），断言的是**无 judge 追加调用**。
        """
        from tests.stubs import StubProxyEvaluator, StubRuleEvaluator

        before = gateway.call_count
        result = self._run(
            "us2-3",
            _plans(make_script_artifact),
            tree_store,
            artifact_store,
            screenplay_jobs_engine,
            config,
            gateway,
            evaluators=[
                StubRuleEvaluator("rule.beat_structure"),
                StubProxyEvaluator("proxy.entity_consistency", score=0.8),
            ],
        )
        assert gateway.call_count - before == 3  # 仅三阶段生成，judge 零调用
        nodes = self._nodes(tree_store, result)
        for node in nodes.values():
            assert node.score == pytest.approx(0.8)
            assert len(node.eval_breakdown) == 2

    def test_缺网关拒绝装配(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
    ):
        """evaluators=None 且无网关 → 明确报错（不静默无打分），0 副作用。"""
        with pytest.raises(ScreenplayLoopError, match="网关"):
            self._run(
                "us2-4",
                _plans(make_script_artifact),
                tree_store,
                artifact_store,
                screenplay_jobs_engine,
                config,
                None,
            )
        assert tree_store.trees_by(agent_id="screenplay") == []

    def test_空评估器列表拒绝(
        self,
        make_script_artifact,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        config,
        gateway,
    ):
        """空列表不视为"默认装配"（显式空 = 调用方缺陷），拒绝且 0 副作用。"""
        with pytest.raises(ScreenplayLoopError, match="评估器"):
            self._run(
                "us2-5",
                _plans(make_script_artifact),
                tree_store,
                artifact_store,
                screenplay_jobs_engine,
                config,
                gateway,
                evaluators=[],
            )
        assert tree_store.trees_by(agent_id="screenplay") == []
