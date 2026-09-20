"""剪辑三 gate 评估器单测（功能 007 / T720，先于实现编写）。

C4~C6：rule.duration_compliance（总时长 ∈ target ± tolerance，130s 过/150s 判 0）、
rule.shot_distribution（逐镜头时长 ∈ [min_shot_ms, max_shot_ms]，含 300ms 判 0）、
rule.transition_rules（转场序列经规则库复核——与 edl.py 执行前校验同一配置库，
单一事实源）。注册元数据：deterministic=True、kind=RULE、cost_per_call=0 显式。
"""

import pytest

from agents.editing.evaluators.duration import DurationComplianceEvaluator
from agents.editing.evaluators.shot_distribution import ShotDistributionEvaluator
from agents.editing.evaluators.transitions import TransitionRulesEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind

# T701 配置口径：target 120s ± 10s；镜头 [500, 20000]ms；转场规则库
_TARGET = {"target_duration_s": 120.0, "duration_tolerance_s": 10.0}
_SHOT_LIMITS = {"min_shot_ms": 500, "max_shot_ms": 20000}
_RULES = {
    "allowed": ["cut", "dissolve", "fade"],
    "dissolve_max_ms": 2000,
    "forbid_jump_cut_within_scene": True,
}


def _artifact(**metadata) -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32, metadata=metadata)


@pytest.fixture()
def ctx(make_shot_library, make_scene_structure, make_edl):
    library = make_shot_library()
    return {
        "edl": make_edl(),
        "shot_library": library,
        "scene_structure": make_scene_structure(library=library),
    }


class Test时长门禁:
    @pytest.mark.parametrize("duration_ms", [110000, 120000, 130000])
    def test_容差内通过(self, duration_ms):
        """C4 场景 1：130s（120±10）过；边界 110s/130s 含端点。"""
        evaluator = DurationComplianceEvaluator(**_TARGET)
        result = evaluator.evaluate(_artifact(duration_ms=duration_ms), {})
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []

    @pytest.mark.parametrize("duration_ms", [109999, 150000])
    def test_越界判0(self, duration_ms):
        """C4 场景 1：150s 超上限 gate 判 0；109.999s 破下限同样判 0。"""
        evaluator = DurationComplianceEvaluator(**_TARGET)
        result = evaluator.evaluate(_artifact(duration_ms=duration_ms), {})
        assert result.score == 0.0
        assert result.diagnostics["violations"]

    def test_诊断口径(self):
        evaluator = DurationComplianceEvaluator(**_TARGET)
        result = evaluator.evaluate(_artifact(duration_ms=150000), {})
        assert result.diagnostics["lower_ms"] == 110000
        assert result.diagnostics["upper_ms"] == 130000
        assert result.diagnostics["duration_ms"] == 150000


class Test镜头分布门禁:
    def test_合法分布通过(self):
        evaluator = ShotDistributionEvaluator(_SHOT_LIMITS)
        result = evaluator.evaluate(
            _artifact(shot_durations_ms=[500, 3000, 20000]),
            {},  # 端点含边界
        )
        assert result.score == 1.0

    def test_含300ms镜头判0(self):
        """C5 场景 2：300ms < min_shot_ms 500 → gate 判 0（防碎片化）。"""
        evaluator = ShotDistributionEvaluator(_SHOT_LIMITS)
        result = evaluator.evaluate(_artifact(shot_durations_ms=[3000, 300, 5000]), {})
        assert result.score == 0.0
        assert any("300" in v for v in result.diagnostics["violations"])

    def test_超上限判0(self):
        evaluator = ShotDistributionEvaluator(_SHOT_LIMITS)
        result = evaluator.evaluate(_artifact(shot_durations_ms=[21000]), {})
        assert result.score == 0.0


class Test转场门禁:
    def test_合法序列通过(self, ctx):
        """C6 场景 3：合法转场序列过门禁。"""
        evaluator = TransitionRulesEvaluator(_RULES)
        assert evaluator.evaluate(_artifact(), ctx).score == 1.0

    def test_非法转场判0(self, ctx, make_edl):
        """C6 场景 3：非法转场组合（wipe 不在规则库）→ gate 判 0。"""
        evaluator = TransitionRulesEvaluator(_RULES)
        result = evaluator.evaluate(_artifact(), {**ctx, "edl": make_edl("illegal_transition")})
        assert result.score == 0.0
        assert result.diagnostics["violations"]

    def test_同区跳切判0(self, ctx, make_edl):
        clips = [
            {
                "shot_id": "shot-1",
                "in_ms": 0,
                "out_ms": 2000,
                "transition": {"type": "cut", "duration_ms": 0},
            },
            {
                "shot_id": "shot-2",
                "in_ms": 0,
                "out_ms": 2000,
                "transition": {"type": "cut", "duration_ms": 0},
            },
        ]
        evaluator = TransitionRulesEvaluator(_RULES)
        result = evaluator.evaluate(_artifact(), {**ctx, "edl": make_edl(clips=clips)})
        assert result.score == 0.0

    def test_与执行前校验同一规则库(self, ctx, editing_config):
        """单一事实源：门禁与 edl.validate_edl 共用配置规则库（决策 3）。"""
        evaluator = TransitionRulesEvaluator(editing_config.transition_rules)
        assert evaluator.evaluate(_artifact(), ctx).score == 1.0


class Test注册元数据:
    @pytest.mark.parametrize(
        "evaluator",
        [
            DurationComplianceEvaluator(**_TARGET),
            ShotDistributionEvaluator(_SHOT_LIMITS),
            TransitionRulesEvaluator(_RULES),
        ],
        ids=["duration", "shot_distribution", "transitions"],
    )
    def test_gate_注册元数据(self, evaluator):
        """宪章三件套之注册元数据：RULE / deterministic / 零成本显式 / 实现哈希版本。"""
        spec = evaluator.spec
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert "+" in spec.version
        assert len(spec.version.rsplit("+", 1)[1]) == 12
