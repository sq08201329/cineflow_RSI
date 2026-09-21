"""四处 Agent loop 的漂移门禁接线单测（功能 012 / T1117）。

接线口径（各 loop **合成前**一行）：`weights = apply_gate(weights, drift_gate)`。

- 未接线（drift_gate=None）→ 合成权重与配置原值一致（既有回归口径零扰动）；
- 接线且 judge 处于 suspect → 合成权重为降权 + 归一后的口径；
- 接线且 judge 处于 confirmed_drift → judge 权重归零（不参与合成）；
- promo / sound 无 judge 层 → 不接线（本特性范围外）。

断言方式：spy 包裹各包 loop 内引用的合成函数，捕获实际入参权重（比"总分是否恰好
变化"更稳）；visual 另加端到端节点总分差异断言，佐证三态合成结果可区分（SC-004）。
"""

import copy
from pathlib import Path

import pytest
import yaml

from core.calibration.drift_gate import DriftGate, gate_weights
from core.calibration.drift_models import (
    DriftAction,
    DriftConclusion,
    DriftMetrics,
    DriftVerdict,
)
from core.calibration.drift_status import dispose, register_suspect

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
_AT = "2026-09-21T10:00:00+00:00"


def _metrics(evaluator_key: str, agent_id: str) -> DriftMetrics:
    return DriftMetrics(
        evaluator_key=evaluator_key,
        agent_id=agent_id,
        period="2026-W39",
        verdict=DriftVerdict.DRIFT,
        samples=12,
        detector_version="drift_detector@1.0.0+0123456789ab",
        psi=0.31,
        quantile_shifts={"p25": 0.2, "p50": 0.2, "p75": 0.2, "p90": 0.2},
        baseline_ref="2026-W34..2026-W38",
        thresholds={"psi": 0.2, "quantile": 0.1, "min_samples": 3, "window": 5},
        note="PSI 0.3100 > 0.2",
    )


def _gate(tmp_path, drift_config, evaluator_key, agent_id, *, confirmed=False):
    """登记漂移状态（suspect / confirmed_drift）并装配门禁。"""
    data_dir = tmp_path / "calibration"
    register_suspect(data_dir, evaluator_key, _metrics(evaluator_key, agent_id), at=_AT)
    if confirmed:
        dispose(
            data_dir,
            evaluator_key,
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="分布持续右移",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
    return DriftGate.load(data_dir, drift_config)


def _spy(monkeypatch, module: str, name: str) -> list:
    """包裹模块内被 loop 引用的合成函数：捕获权重入参（原函数照常执行）。"""
    import importlib

    target = importlib.import_module(module)
    original = getattr(target, name)
    captured: list = []

    def spy(breakdown, weights, *args, **kwargs):
        captured.append(dict(weights))
        return original(breakdown, weights, *args, **kwargs)

    monkeypatch.setattr(target, name, spy)
    return captured


def _numeric_weights(weights: dict) -> dict:
    """config 权重 → 数值口径（gate 字面量 → 0.0，与 visual loop 的 `_weights` 一致）。"""
    return {
        key: (0.0 if str(value).lower() == "gate" else float(value))
        for key, value in weights.items()
    }


class TestVisual接线:
    _KEY = "judge.cinematic@1.0.0"
    _AGENT = "visual"

    @pytest.fixture()
    def visual_env(self, tree_store, gen_jobs_engine, tmp_path, visual_config, mock_gateway):
        from agents.visual.platform.simulated import SimulatedVideoGen
        from core.tree.artifacts import LocalArtifactStore

        return {
            "store": tree_store,
            "engine": gen_jobs_engine,
            "artifacts": LocalArtifactStore(tmp_path / "artifacts"),
            "adapter": SimulatedVideoGen(visual_config.simulated_gen),
            "gateway": mock_gateway,
            "config": visual_config,
        }

    def _policy(self):
        class _Policy:
            policy_version = "drift-wiring-1"

            def plan_clips(self, config):
                return [{"gen_params": {"style": "史诗", "shots": 2, "seed_tier": 1}}]

        return _Policy()

    def _run(self, round_id, env, drift_gate=None):
        from agents.visual.loop import run_round

        return run_round(
            round_id,
            self._policy(),
            env["store"],
            env["artifacts"],
            env["adapter"],
            env["gateway"],
            env["engine"],
            env["config"],
            drift_gate=drift_gate,
        )

    def _node_scores(self, env, tree_id):
        return {node.node_id: node.score for node in env["store"].nodes_of(tree_id)}

    def test_未接线权重原值(self, monkeypatch, visual_env):
        captured = _spy(monkeypatch, "agents.visual.loop", "composite_score_versioned")
        self._run("drift-wiring-plain", visual_env)
        assert captured  # 合成确实发生（合规未短路）
        assert captured[0] == _numeric_weights(visual_env["config"].evaluator_weights)

    def test_未接线与_suspect_三态端到端差异(self, monkeypatch, tmp_path, visual_env, drift_config):
        """SC-004 端到端：normal / suspect / confirmed_drift 三态节点总分互相可区分。"""
        plain = _spy(monkeypatch, "agents.visual.loop", "composite_score_versioned")
        result_normal = self._run("drift-wiring-normal", visual_env)
        suspect_gate = _gate(tmp_path, drift_config, self._KEY, self._AGENT)
        raw = _numeric_weights(visual_env["config"].evaluator_weights)
        expected_suspect = gate_weights(raw, suspect_gate.registry, drift_config)
        result_suspect = self._run("drift-wiring-suspect", visual_env, suspect_gate)
        confirmed_gate = _gate(tmp_path, drift_config, self._KEY, self._AGENT, confirmed=True)
        result_confirmed = self._run("drift-wiring-confirmed", visual_env, confirmed_gate)

        judge_weights = [entry["judge.cinematic"] for entry in plain]
        assert judge_weights[0] > judge_weights[1] > judge_weights[2] == 0.0
        assert plain[1] == expected_suspect  # suspect：降权 + 归一（逐字段一致）

        # 合成入参权重三态互不相同（normal 原值 / suspect 降权 / confirmed 归零）
        # 节点总分三态互相可区分（同候选同工件，仅处置口径不同）
        scores = [
            self._node_scores(visual_env, result.tree_id)
            for result in (result_normal, result_suspect, result_confirmed)
        ]
        assert scores[0] != scores[1]
        assert scores[1] != scores[2]
        assert scores[0] != scores[2]


class TestEditing接线:
    _KEY = "judge.narrative_flow@1.0.0"
    _AGENT = "editing"

    def _config(self):
        from agents.editing.config import EditingConfig

        raw = copy.deepcopy(_REAL_CONFIG)
        raw["editing"]["target_duration_s"] = 5
        raw["editing"]["duration_tolerance_s"] = 2
        raw["editing"]["render"].update(width=64, height=48)
        return EditingConfig.from_dict(raw)

    def _run(
        self, round_id, make_edl, make_shot_library, make_scene_structure, env, drift_gate=None
    ):
        from agents.editing.loop import run_editing_round
        from agents.editing.platform.simulated import SimulatedEditRenderer
        from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

        class _Policy:
            policy_version = "drift-wiring-1"

            def plan(self, config, inputs):
                return [make_edl(), make_edl(clips=make_edl().clips[:2], audio=[])]

        library = make_shot_library()
        return run_editing_round(
            round_id=round_id,
            policy=_Policy(),
            store=env["store"],
            artifacts=env["artifacts"],
            adapter=SimulatedEditRenderer(self._config().render),
            engine=env["engine"],
            config=self._config(),
            inputs={
                "shot_library": library,
                "scene_structure": make_scene_structure(library=library),
            },
            evaluators=[
                StubRuleEvaluator("rule.duration_compliance"),
                StubProxyEvaluator("proxy.pacing_curve", score=0.8),
                StubJudgeEvaluator("judge.narrative_flow", score=0.7),
            ],
            drift_gate=drift_gate,
        )

    @pytest.fixture()
    def env(self, tree_store, artifact_store, editing_jobs_engine):
        return {
            "store": tree_store,
            "artifacts": artifact_store,
            "engine": editing_jobs_engine,
        }

    def test_未接线与_suspect_权重口径(
        self,
        monkeypatch,
        tmp_path,
        env,
        drift_config,
        make_edl,
        make_shot_library,
        make_scene_structure,
    ):
        captured = _spy(monkeypatch, "agents.editing.loop", "composite_editing")
        self._run(
            "drift-wiring-editing-plain", make_edl, make_shot_library, make_scene_structure, env
        )
        gate = _gate(tmp_path, drift_config, self._KEY, self._AGENT)
        self._run(
            "drift-wiring-editing-suspect",
            make_edl,
            make_shot_library,
            make_scene_structure,
            env,
            gate,
        )
        assert captured[0] == self._config().evaluator_weights  # 未接线：配置原值
        expected = gate_weights(self._config().evaluator_weights, gate.registry, drift_config)
        assert captured[-1] == expected
        assert captured[-1]["judge.narrative_flow"] < float(
            self._config().evaluator_weights["judge.narrative_flow"]
        )


class TestStoryboard接线:
    _KEY = "judge.script_fit@1.0.0"
    _AGENT = "storyboard"

    def _config(self):
        from agents.storyboard.config import StoryboardConfig

        raw = copy.deepcopy(_REAL_CONFIG)
        raw["storyboard"]["render"].update(width=64, height=48)
        return StoryboardConfig.from_dict(raw)

    @pytest.fixture()
    def env(self, tree_store, artifact_store, storyboard_jobs_engine):
        return {
            "store": tree_store,
            "artifacts": artifact_store,
            "engine": storyboard_jobs_engine,
        }

    def _run(self, round_id, make_shotlist, make_script_segment, env, drift_gate=None):
        from agents.storyboard.loop import run_storyboard_round
        from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
        from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

        class _Policy:
            policy_version = "drift-wiring-1"

            def plan(self, config, inputs):
                return [make_shotlist()]

        return run_storyboard_round(
            round_id=round_id,
            policy=_Policy(),
            store=env["store"],
            artifacts=env["artifacts"],
            adapter=SimulatedStoryboardRenderer(),
            engine=env["engine"],
            config=self._config(),
            inputs={"script": make_script_segment()},
            evaluators=[
                StubRuleEvaluator("rule.shot_grammar"),
                StubProxyEvaluator("proxy.emotion_alignment", score=0.8),
                StubJudgeEvaluator("judge.script_fit", score=0.7),
            ],
            drift_gate=drift_gate,
        )

    def test_未接线与_confirmed_权重口径(
        self, monkeypatch, tmp_path, env, drift_config, make_shotlist, make_script_segment
    ):
        captured = _spy(monkeypatch, "agents.storyboard.loop", "composite_storyboard")
        self._run("drift-wiring-board-plain", make_shotlist, make_script_segment, env)
        gate = _gate(tmp_path, drift_config, self._KEY, self._AGENT, confirmed=True)
        self._run("drift-wiring-board-gated", make_shotlist, make_script_segment, env, gate)
        assert captured[0] == self._config().evaluator_weights
        assert captured[-1]["judge.script_fit"] == 0.0  # confirmed_drift → 排除


class TestScreenplay接线:
    _KEY = "judge.dramatic_tension@1.0.0"
    _AGENT = "screenplay"
    _INPUTS = {
        "topic": "病房里的三个月",
        "target_duration_min": 90,
        "constraints": ["单场景为主"],
        "characters": ["林静", "陈默"],
    }

    @pytest.fixture()
    def env(self, tree_store, artifact_store, screenplay_jobs_engine, screenplay_config):
        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        return {
            "store": tree_store,
            "artifacts": artifact_store,
            "engine": screenplay_jobs_engine,
            "gateway": LLMGateway(
                MockBackend(), price_book=screenplay_config.model_prices, sleep=lambda _: None
            ),
        }

    def _policy(self, make_script_artifacts):
        plans = {}
        for stage, artifact in make_script_artifacts().items():
            payload = artifact.to_dict()
            plans[stage] = {key: payload[key] for key in ("beats", "scenes", "characters", "lines")}

        class _Policy:
            policy_version = "drift-wiring-1"

            def plan(self, inputs, config):
                return copy.deepcopy(plans)

        return _Policy()

    def _run(self, round_id, env, screenplay_config, make_script_artifacts, drift_gate=None):
        from agents.screenplay.loop import run_screenplay_round
        from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

        return run_screenplay_round(
            round_id=round_id,
            policy=self._policy(make_script_artifacts),
            store=env["store"],
            artifacts=env["artifacts"],
            engine=env["engine"],
            gateway=env["gateway"],
            config=screenplay_config,
            inputs=dict(self._INPUTS),
            evaluators=[
                StubRuleEvaluator("rule.beat_structure"),
                StubRuleEvaluator("rule.page_minutes"),
                StubRuleEvaluator("rule.scene_character"),
                StubRuleEvaluator("rule.dialogue_action_ratio"),
                StubProxyEvaluator("proxy.entity_consistency", score=0.8),
                StubProxyEvaluator("proxy.timeline_conflict", score=0.6),
                StubJudgeEvaluator("judge.dramatic_tension", score=0.7),
            ],
            drift_gate=drift_gate,
        )

    def test_未接线与_suspect_权重口径(
        self, monkeypatch, tmp_path, env, drift_config, screenplay_config, make_script_artifacts
    ):
        captured = _spy(monkeypatch, "agents.screenplay.loop", "composite_screenplay")
        self._run("drift-wiring-script-plain", env, screenplay_config, make_script_artifacts)
        gate = _gate(tmp_path, drift_config, self._KEY, self._AGENT)
        self._run(
            "drift-wiring-script-suspect",
            env,
            screenplay_config,
            make_script_artifacts,
            gate,
        )
        assert captured[0] == screenplay_config.evaluator_weights
        expected = gate_weights(screenplay_config.evaluator_weights, gate.registry, drift_config)
        assert captured[-1] == expected
        assert captured[-1]["judge.dramatic_tension"] < float(
            screenplay_config.evaluator_weights["judge.dramatic_tension"]
        )
