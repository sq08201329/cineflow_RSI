"""分镜三 gate 评估器单测（功能 008 / T820，先于实现编写）。

C4 rule.shot_grammar：相邻镜头景别跳跃 > max_size_jump 判 0（档位有序枚举序号差）、
同景别连续 > max_same_size_run 判 0；规则库配置驱动（与执行前校验共用同一规则库）。
C5 rule.coverage（澄清 Q1）：每场景 ≥1 镜（硬要求）+ key=True 行逐条被 covers 承接；
普通台词合并（一镜多行）/拆分（一行多镜）不违规；必覆盖清单为空 → 降级纯场景级并在
diagnostics 注明（不伪造关键行要求）。
C6 rule.axis_rule：机位侧别（A|B）跳变需过渡镜头（allowed_transition_shots 额度），
无过渡的硬跳判 0；同侧连续/合法过渡通过；场景切换重置轴线（剧本 axis_base）。

边界两条（analyze 修订）：
① 单场景行数超镜头数上限——ShotList 仍合法（C1 不因行数不足拒绝）且缺口由 coverage
   门禁逐行暴露（不静默截断剧本、不悄悄补镜）；
② 景别/轴规则与覆盖率冲突——覆盖率门禁优先（其判定标准不被其他门禁改写），冲突在
   diagnostics 如实说明，越轴违规不被自动豁免（axis 仍判 0）。
"""

import pytest

from agents.storyboard.config import StoryboardConfigError
from agents.storyboard.evaluators.axis_rule import AxisRuleEvaluator, axis_excursions
from agents.storyboard.evaluators.coverage import CoverageEvaluator
from agents.storyboard.evaluators.shot_grammar import ShotGrammarEvaluator
from agents.storyboard.shotlist import ShotList, validate_shotlist
from core.evaluators.base import ArtifactRef, EvaluatorKind

_SIZES = ["extreme_close_up", "close_up", "medium", "full", "wide"]
_GRAMMAR = {
    "shot_sizes": _SIZES,
    "max_size_jump": 2,
    "max_same_size_run": 2,
    "camera_positions": ["eye_level", "low_angle", "high_angle", "over_shoulder", "side"],
    "movements": ["static", "pan", "tilt", "dolly", "handheld"],
}
_AXIS = {
    "require_transition_on_cross": True,
    "allowed_transition_shots": 1,
    "side_field": "side",
}


def _shot(
    shot_id: str,
    *,
    scene_id: str = "scene-1",
    covers: tuple[str, ...] = ("s1-l1",),
    shot_size: str = "medium",
    camera: str = "eye_level",
    side: str = "A",
    movement: str = "static",
    est_duration_ms: int = 1000,
) -> dict:
    return {
        "shot_id": shot_id,
        "scene_id": scene_id,
        "covers": list(covers),
        "shot_size": shot_size,
        "camera": camera,
        "side": side,
        "movement": movement,
        "est_duration_ms": est_duration_ms,
        "alternatives": 1,
    }


def _artifact() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


@pytest.fixture()
def script(make_script_segment):
    return make_script_segment()


@pytest.fixture()
def ctx(make_shotlist, script):
    return {"shotlist": make_shotlist(), "script": script}


@pytest.fixture()
def grammar():
    return ShotGrammarEvaluator(_GRAMMAR)


@pytest.fixture()
def coverage():
    return CoverageEvaluator(_AXIS)


@pytest.fixture()
def axis():
    return AxisRuleEvaluator(_AXIS)


class Test景别语法门禁:
    def test_合法序列通过(self, grammar, ctx):
        """C4 场景 1：合规景别序列（夹具）通过门禁。"""
        result = grammar.evaluate(_artifact(), ctx)
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []
        assert result.diagnostics["shot_count"] == 9

    def test_景别跳跃超限判0(self, grammar, script):
        """相邻景别跳跃（序号差）> max_size_jump → 判 0。"""
        shotlist = ShotList(
            shots=[_shot("shot-01", shot_size="close_up"), _shot("shot-02", shot_size="wide")]
        )
        result = grammar.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0
        assert any("wide" in v for v in result.diagnostics["violations"])

    def test_跳跃达上限通过(self, grammar, script):
        """边界：跳跃恰为 max_size_jump=2（close_up→full）通过。"""
        shotlist = ShotList(
            shots=[_shot("shot-01", shot_size="close_up"), _shot("shot-02", shot_size="full")]
        )
        result = grammar.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 1.0

    def test_同景别连续超限判0(self, grammar, script):
        """C4 场景 1：同景别连续超过 max_same_size_run=2 → 判 0。"""
        shotlist = ShotList(
            shots=[
                _shot("shot-01", shot_size="close_up"),
                _shot("shot-02", shot_size="close_up"),
                _shot("shot-03", shot_size="close_up"),
            ]
        )
        result = grammar.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0
        assert any("连续" in v for v in result.diagnostics["violations"])

    def test_同景别连续达上限通过(self, grammar, script):
        shotlist = ShotList(
            shots=[_shot("shot-01", shot_size="close_up"), _shot("shot-02", shot_size="close_up")]
        )
        result = grammar.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 1.0

    def test_跳跃上限收紧判0(self, script):
        """规则库配置驱动（与执行前校验共用）：跳跃上限收紧到 1 → 原合法的 2 档跳跃判 0。"""
        evaluator = ShotGrammarEvaluator({**_GRAMMAR, "max_size_jump": 1})
        shotlist = ShotList(
            shots=[_shot("shot-01", shot_size="close_up"), _shot("shot-02", shot_size="full")]
        )
        result = evaluator.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0

    def test_同景别上限收紧判0(self, script):
        evaluator = ShotGrammarEvaluator({**_GRAMMAR, "max_same_size_run": 1})
        shotlist = ShotList(shots=[_shot("shot-01"), _shot("shot-02")])
        result = evaluator.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0

    def test_规则库缺失拒绝启动(self):
        """缺规则库即报错——不允许静默放过门禁（原则五）。"""
        with pytest.raises(StoryboardConfigError, match="shot_sizes"):
            ShotGrammarEvaluator({})

    def test_诊断携带规则库口径(self, grammar, ctx):
        result = grammar.evaluate(_artifact(), ctx)
        assert result.diagnostics["max_size_jump"] == 2
        assert result.diagnostics["max_same_size_run"] == 2

    def test_注册元数据(self, grammar):
        """宪章三件套之注册元数据：RULE / deterministic / 零成本显式 / 实现哈希版本。"""
        spec = grammar.spec
        assert spec.evaluator_id == "rule.shot_grammar"
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert len(spec.version.rsplit("+", 1)[1]) == 12


class Test覆盖率门禁:
    def test_合法全覆盖通过(self, coverage, ctx):
        """C5 场景 2：场景级 + 必覆盖清单全部满足 → 通过（普通台词合并/拆分不违规）。"""
        result = coverage.evaluate(_artifact(), ctx)
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []
        assert result.diagnostics["uncovered_scenes"] == []
        assert result.diagnostics["uncovered_key_lines"] == []
        assert result.diagnostics["must_cover_degraded"] is False

    def test_场景无镜头判0(self, coverage, script, make_shotlist):
        result = coverage.evaluate(
            _artifact(), {"shotlist": make_shotlist("scene_uncovered"), "script": script}
        )
        assert result.score == 0.0
        assert result.diagnostics["uncovered_scenes"] == ["scene-2"]
        assert any("scene-2" in v for v in result.diagnostics["violations"])

    def test_关键行未承接判0(self, coverage, script, make_shotlist):
        """必覆盖清单逐条承接（澄清 Q1）：普通台词不判违规，关键行逐条判。"""
        result = coverage.evaluate(
            _artifact(), {"shotlist": make_shotlist("key_line_uncovered"), "script": script}
        )
        assert result.score == 0.0
        assert result.diagnostics["uncovered_key_lines"] == ["s2-l1"]
        assert any("s2-l1" in v for v in result.diagnostics["violations"])

    def test_合并与拆分手法不违规(self, coverage, script, make_shotlist):
        """澄清 Q1：一镜覆盖多行（合并）、一行被多镜覆盖（拆分）均不判违规。"""
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "covers": ["s1-l1"]}  # 拆分：s1-l1 由多镜覆盖
        shots[1] = {**shots[1], "covers": ["s1-l2", "s1-l3"]}  # 合并：一镜承接两行
        shots[2] = {**shots[2], "covers": ["s1-l1"]}
        result = coverage.evaluate(
            _artifact(), {"shotlist": ShotList(shots=shots), "script": script}
        )
        assert result.score == 1.0
        assert result.diagnostics["uncovered_lines"] == []

    def test_必覆盖清单为空降级纯场景级(self, coverage, make_shotlist):
        """剧本未标注关键行 → 降级纯场景级并在 diagnostics 注明（不伪造关键行要求）。"""
        from agents.storyboard.script import ScriptSegment

        script = ScriptSegment(
            scenes=[
                {
                    "scene_id": "scene-1",
                    "axis_base": "A",
                    "lines": [
                        {"line_id": "s1-l1", "kind": "dialogue", "text": "一"},
                        {"line_id": "s1-l2", "kind": "dialogue", "text": "二"},
                    ],
                }
            ]
        )
        result = coverage.evaluate(_artifact(), {"shotlist": make_shotlist(), "script": script})
        assert result.diagnostics["must_cover_degraded"] is True
        assert "必覆盖清单为空" in result.diagnostics["note"]

    def test_逐行缺口如实暴露不静默截断(self, coverage, make_script_segment, make_shotlist):
        """边界①：单场景行数超镜头数上限——ShotList 仍合法，缺口由 coverage 逐行暴露。"""
        script = make_script_segment(
            scenes=[
                {
                    "scene_id": "scene-1",
                    "axis_base": "A",
                    "lines": [
                        {"line_id": "s1-l1", "kind": "dialogue", "text": "一", "key": True},
                        {"line_id": "s1-l2", "kind": "dialogue", "text": "二"},
                        {"line_id": "s1-l3", "kind": "action", "text": "三"},
                        {"line_id": "s1-l4", "kind": "action", "text": "四"},
                        {"line_id": "s1-l5", "kind": "dialogue", "text": "五"},
                    ],
                }
            ]
        )
        shotlist = ShotList(shots=[_shot("shot-01", covers=("s1-l1", "s1-l2"))])
        # ShotList 仍合法（C1 不要求逐行全覆盖，也不静默截断剧本行）
        validate_shotlist(shotlist, script, _GRAMMAR)
        assert len(script.line_ids()) == 5
        result = coverage.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        # 关键行已承接 → 门禁通过；未承接的行逐条暴露（不静默假设已覆盖）
        assert result.score == 1.0
        assert result.diagnostics["uncovered_lines"] == ["s1-l3", "s1-l4", "s1-l5"]

    def test_缺口含关键行则门禁判0(self, coverage, make_script_segment, make_shotlist):
        script = make_script_segment(
            scenes=[
                {
                    "scene_id": "scene-1",
                    "axis_base": "A",
                    "lines": [
                        {"line_id": "s1-l1", "kind": "dialogue", "text": "一"},
                        {"line_id": "s1-l2", "kind": "dialogue", "text": "二", "key": True},
                        {"line_id": "s1-l3", "kind": "action", "text": "三"},
                    ],
                }
            ]
        )
        shotlist = ShotList(shots=[_shot("shot-01", covers=("s1-l1", "s1-l3"))])
        result = coverage.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0
        assert result.diagnostics["uncovered_key_lines"] == ["s1-l2"]
        assert result.diagnostics["uncovered_lines"] == ["s1-l2"]

    def test_覆盖率与轴规则冲突如实暴露(self, coverage, axis, script, make_shotlist):
        """边界②：覆盖率达成依赖越轴 → 覆盖率门禁优先（自身标准不被他者改写），
        冲突如实写入 diagnostics；越轴违规不被自动豁免（axis 仍判 0）。"""
        shots = make_shotlist().to_dict()["shots"]
        # scene-1 轴线基准 A：把该场景全部镜头放到 B 侧（越轴 3 镜 > 过渡额度 1）
        shots[0] = {**shots[0], "side": "B"}
        shots[1] = {**shots[1], "side": "B"}
        shots[2] = {**shots[2], "side": "B"}
        shotlist = ShotList(shots=shots)
        coverage_result = coverage.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert coverage_result.score == 1.0  # 覆盖率口径：场景级 + 必覆盖清单仍满足
        assert coverage_result.diagnostics["conflicts"]
        conflicts = coverage_result.diagnostics["conflicts"]
        assert any("轴规则" in c and "scene-1" in c for c in conflicts)
        axis_result = axis.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert axis_result.score == 0.0  # 不自动豁免

    def test_注册元数据(self, coverage):
        spec = coverage.spec
        assert spec.evaluator_id == "rule.coverage"
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert len(spec.version.rsplit("+", 1)[1]) == 12


class Test轴规则门禁:
    def test_同侧连续通过(self, axis, script, make_shotlist):
        """C6 场景 3：同侧连续通过（夹具各场景内同侧）。"""
        result = axis.evaluate(_artifact(), {"shotlist": make_shotlist(), "script": script})
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []

    def test_侧别硬跳无过渡判0(self, axis, script):
        """C6 场景 3：侧别硬跳（A→B→B，无过渡回切）判 0。"""
        shotlist = ShotList(
            shots=[
                _shot("shot-01", side="A"),
                _shot("shot-02", side="B"),
                _shot("shot-03", side="B"),
            ]
        )
        result = axis.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0
        assert any("越轴" in v or "侧别" in v for v in result.diagnostics["violations"])

    def test_带过渡镜头通过(self, axis, script):
        """C6 场景 3：带过渡镜头（A→B→A，越轴额度 1）通过。"""
        shotlist = ShotList(
            shots=[
                _shot("shot-01", side="A"),
                _shot("shot-02", side="B"),
                _shot("shot-03", side="A"),
            ]
        )
        result = axis.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 1.0

    def test_场景切换重置轴线(self, axis, script, make_shotlist):
        """场景切换重置 180° 线（剧本 axis_base）：scene-2(A)→scene-3(B) 不判违规。"""
        result = axis.evaluate(_artifact(), {"shotlist": make_shotlist(), "script": script})
        assert result.score == 1.0

    def test_过渡额度配置驱动(self, script):
        """allowed_transition_shots 配置驱动：额度 0 → 带过渡镜头的越轴同样判 0。"""
        evaluator = AxisRuleEvaluator({**_AXIS, "allowed_transition_shots": 0})
        shotlist = ShotList(
            shots=[
                _shot("shot-01", side="A"),
                _shot("shot-02", side="B"),
                _shot("shot-03", side="A"),
            ]
        )
        result = evaluator.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0

    def test_规则关闭则放行(self, script):
        evaluator = AxisRuleEvaluator({**_AXIS, "require_transition_on_cross": False})
        shotlist = ShotList(shots=[_shot("shot-01", side="A"), _shot("shot-02", side="B")])
        result = evaluator.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 1.0
        assert result.diagnostics["require_transition_on_cross"] is False

    def test_无轴向基准时以首镜侧别为基准(self, axis, make_script_segment):
        """剧本未标注 axis_base → 以该场景首镜侧别为基准（如实口径，不臆造）。"""
        script = make_script_segment(
            scenes=[
                {
                    "scene_id": "scene-1",
                    "lines": [
                        {"line_id": "s1-l1", "kind": "dialogue", "text": "一"},
                        {"line_id": "s1-l2", "kind": "dialogue", "text": "二"},
                        {"line_id": "s1-l3", "kind": "dialogue", "text": "三"},
                    ],
                }
            ]
        )
        shotlist = ShotList(
            shots=[
                _shot("shot-01", side="A"),
                _shot("shot-02", side="B"),
                _shot("shot-03", side="B"),
            ]
        )
        result = axis.evaluate(_artifact(), {"shotlist": shotlist, "script": script})
        assert result.score == 0.0
        assert result.diagnostics["excursions"][0]["base"] == "A"

    def test_越轴段清单可复用(self, script):
        """axis_excursions 为覆盖率冲突说明与轴门禁的唯一事实源（同口径）。"""
        shotlist = ShotList(
            shots=[
                _shot("shot-01", side="A"),
                _shot("shot-02", side="B"),
                _shot("shot-03", side="A"),
            ]
        )
        excursions = axis_excursions(shotlist, script, _AXIS)
        assert len(excursions) == 1
        assert excursions[0]["shot_ids"] == ["shot-02"]
        assert excursions[0]["length"] == 1

    def test_规则库缺失拒绝启动(self):
        with pytest.raises(StoryboardConfigError, match="缺规则库配置项"):
            AxisRuleEvaluator({})
        with pytest.raises(StoryboardConfigError, match="allowed_transition_shots"):
            AxisRuleEvaluator({**_AXIS, "allowed_transition_shots": None})

    def test_注册元数据(self, axis):
        spec = axis.spec
        assert spec.evaluator_id == "rule.axis_rule"
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert len(spec.version.rsplit("+", 1)[1]) == 12
