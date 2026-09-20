"""剧本两代理评估器单测（功能 009 / T917，先于实现编写）。

C8 proxy.entity_consistency：角色名规范化（别名表）后的指称一致性——**同名异写**
（未登记但与登记名同源的另一种写法）/ **未登记指称**（角色表外写法）/ **指代歧义**
（对白行未标注说话人）逐条诊断并扣分；正确写法（规范名或登记别名）满分；动作行
无归属主体不扣分（群体动作合法）。确定性、实现哈希 + 别名表哈希入版本号。
C9 proxy.timeline_conflict：场景顺序 vs `time_marker` 单调性——回退即冲突，逐条诊断
（含前序场景与差值）；同刻（相等）不算冲突；无冲突满分。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.config import ScreenplayConfig, ScreenplayConfigError
from agents.screenplay.evaluators.entity_consistency import EntityConsistencyEvaluator
from agents.screenplay.evaluators.timeline_conflict import TimelineConflictEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _config() -> ScreenplayConfig:
    return ScreenplayConfig.from_dict(copy.deepcopy(_REAL_CONFIG))


def _ref() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


def _evaluate(evaluator, artifact):
    return evaluator.evaluate(_ref(), {"artifact": artifact})


def _with_line_character(make_script_artifact, line_id: str, character) -> ScriptArtifact:
    payload = make_script_artifact().to_dict()
    for line in payload["lines"]:
        if line["line_id"] == line_id:
            line["character"] = character
    return ScriptArtifact.from_dict(payload)


def _with_time_markers(make_script_artifact, markers: dict) -> ScriptArtifact:
    payload = make_script_artifact().to_dict()
    for scene in payload["scenes"]:
        if scene["scene_id"] in markers:
            scene["time_marker"] = markers[scene["scene_id"]]
    return ScriptArtifact.from_dict(payload)


class TestEntityConsistency:
    """C8：角色名实体一致性（连续分量，诊断驱动）。"""

    @pytest.fixture()
    def evaluator(self):
        return EntityConsistencyEvaluator(_config().character_aliases)

    def test_正确写法满分(self, evaluator, make_script_artifact):
        result = _evaluate(evaluator, make_script_artifact())
        assert result.score == 1.0
        assert result.diagnostics["applicable"] is True
        assert result.diagnostics["same_name_variants"] == []
        assert result.diagnostics["unregistered"] == []
        assert result.diagnostics["pronoun_ambiguities"] == []

    def test_动作行无归属主体不扣分(self, evaluator, make_script_artifact):
        """群体动作行（character 缺失）合法：只有**对白行**缺说话人算指代歧义。"""
        artifact = make_script_artifact()
        assert artifact.line("s2-l2").kind == "action"
        assert artifact.line("s2-l2").character is None
        assert _evaluate(evaluator, artifact).score == 1.0

    def test_登记别名满分(self, evaluator, make_script_artifact):
        """别名表即登记口径：用登记别名（阿静）与规范名等价。"""
        artifact = _with_line_character(make_script_artifact, "s1-l1", "阿静")
        assert _evaluate(evaluator, artifact).score == 1.0

    def test_同名异写扣分并诊断(self, evaluator, make_script_artifact):
        """C8 场景 5：注入未登记但与登记名同源的写法（小静 ↔ 林静）→ 扣分 + 诊断。"""
        result = _evaluate(evaluator, make_script_artifact("entity_variant"))
        assert result.score == pytest.approx(0.8)
        assert result.diagnostics["same_name_variants"] == [
            {"line_id": "s1-l1", "spelling": "小静", "canonical": "林静"}
        ]
        assert result.diagnostics["unregistered"] == []

    def test_未登记指称扣分并诊断(self, evaluator, make_script_artifact):
        """角色表外写法（赵护士，与任何登记名不同源）→ 扣分更重 + 诊断。"""
        artifact = _with_line_character(make_script_artifact, "s2-l1", "赵护士")
        result = _evaluate(evaluator, artifact)
        assert result.score == pytest.approx(0.7)
        assert result.diagnostics["same_name_variants"] == []
        assert result.diagnostics["unregistered"] == [{"line_id": "s2-l1", "spelling": "赵护士"}]

    def test_指代歧义扣分并诊断(self, evaluator, make_script_artifact):
        """对白行未标注说话人 → 指代歧义（轻扣分，诊断到行）。"""
        artifact = _with_line_character(make_script_artifact, "s1-l3", None)
        result = _evaluate(evaluator, artifact)
        assert result.score == pytest.approx(0.9)
        assert result.diagnostics["pronoun_ambiguities"] == ["s1-l3"]

    def test_缺陷累积且分数下限为零(self, evaluator, make_script_artifact):
        """多处缺陷累积扣分（不封顶前的负数收敛到 0；不伪造高于 0 的分）。"""
        payload = make_script_artifact().to_dict()
        for line_id, name in (
            ("s1-l1", "赵护士"),
            ("s1-l3", "王主任"),
            ("s2-l1", "李律师"),
            ("s2-l3", None),
            ("s3-l3", "小静"),
        ):
            for line in payload["lines"]:
                if line["line_id"] == line_id:
                    line["character"] = name
        result = _evaluate(evaluator, ScriptArtifact.from_dict(payload))
        assert result.score == 0.0
        assert result.diagnostics["penalty"] > 1.0

    def test_重算逐位一致(self, evaluator, make_script_artifact):
        artifact = make_script_artifact("entity_variant")
        first, second = _evaluate(evaluator, artifact), _evaluate(evaluator, artifact)
        assert first.score == second.score
        assert first.diagnostics == second.diagnostics

    def test_角色表缺失拒绝启动(self):
        with pytest.raises(ScreenplayConfigError, match="character_aliases"):
            EntityConsistencyEvaluator({})

    def test_元数据与版本含别名表(self):
        evaluator = EntityConsistencyEvaluator(_config().character_aliases)
        assert evaluator.spec.evaluator_id == "proxy.entity_consistency"
        assert evaluator.spec.kind is EvaluatorKind.PROXY_MODEL
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.cost_per_call == 0.0
        same = EntityConsistencyEvaluator(_config().character_aliases)
        assert same.spec.version == evaluator.spec.version
        changed = EntityConsistencyEvaluator({"林静": ("静静",)})
        assert changed.spec.version != evaluator.spec.version


class TestTimelineConflict:
    """C9：时间线单调性冲突（连续分量，诊断驱动）。"""

    @pytest.fixture()
    def evaluator(self):
        return TimelineConflictEvaluator()

    def test_无冲突满分(self, evaluator, make_script_artifact):
        result = _evaluate(evaluator, make_script_artifact())
        assert result.score == 1.0
        assert result.diagnostics["applicable"] is True
        assert result.diagnostics["conflicts"] == []
        assert result.diagnostics["timeline"] == [
            {"scene_id": "scene-1", "time_marker": 0},
            {"scene_id": "scene-2", "time_marker": 30},
            {"scene_id": "scene-3", "time_marker": 75},
        ]

    def test_同刻不算冲突(self, evaluator, make_script_artifact):
        """时间戳相等（同时/紧接）合法：单调性判定为非降序。"""
        artifact = _with_time_markers(make_script_artifact, {"scene-3": 30})
        assert _evaluate(evaluator, artifact).score == 1.0

    def test_回退命中并逐条诊断(self, evaluator, make_script_artifact):
        """C9 场景 6：scene-3 时间戳回退（75 → 5，前序 30）→ 冲突 + 逐条诊断。"""
        result = _evaluate(evaluator, make_script_artifact("timeline_conflict"))
        assert result.score == pytest.approx(round(1 - 1 / 3, 6))
        assert result.diagnostics["conflicts"] == [
            {
                "scene_id": "scene-3",
                "previous_scene_id": "scene-2",
                "previous_time_marker": 30,
                "time_marker": 5,
                "delta": -25,
            }
        ]

    def test_多处回退按冲突场景占比扣分(self, evaluator, make_script_artifact):
        artifact = _with_time_markers(make_script_artifact, {"scene-1": 60, "scene-3": 10})
        result = _evaluate(evaluator, artifact)
        assert len(result.diagnostics["conflicts"]) == 2
        assert result.score == pytest.approx(round(1 - 2 / 3, 6))

    def test_重算逐位一致(self, evaluator, make_script_artifact):
        artifact = make_script_artifact("timeline_conflict")
        first, second = _evaluate(evaluator, artifact), _evaluate(evaluator, artifact)
        assert first.score == second.score
        assert first.diagnostics == second.diagnostics

    def test_元数据(self):
        evaluator = TimelineConflictEvaluator()
        assert evaluator.spec.evaluator_id == "proxy.timeline_conflict"
        assert evaluator.spec.kind is EvaluatorKind.PROXY_MODEL
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.cost_per_call == 0.0
        assert evaluator.spec.version == TimelineConflictEvaluator().spec.version
