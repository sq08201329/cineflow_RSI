"""剧本四 gate 评估器单测（功能 009 / T916，先于实现编写）。

C4 rule.beat_structure：工件节拍清单对照配置节拍表——required 节拍逐条存在、节拍全部
∈ 配置节拍表、幕序列可解析（不回退）、配置涉幕齐备；缺/不可解析 → 判 0。
C5 rule.page_minutes：总行数 ÷ lines_per_page → 页数 ∈ 目标时长 ± 容差；越界 → 判 0。
C6 rule.scene_character：逐场景出场角色 ∈ 登记写法（无幽灵角色）+ 场景头地点段与
location 字段一致；违规 → 判 0；角色表缺失 → 拒绝启动（配置纪律）。
C7 rule.dialogue_action_ratio：对白行占比 ∈ 配置区间；越界 → 判 0。
**合法工件全过**（四 gate 齐过路径的夹具 = 默认 9 行工件 + 页数窗口按夹具缩放）。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.config import ScreenplayConfig, ScreenplayConfigError
from agents.screenplay.evaluators.beat_structure import BeatStructureEvaluator
from agents.screenplay.evaluators.dialogue_action_ratio import DialogueActionRatioEvaluator
from agents.screenplay.evaluators.page_minutes import PageMinutesEvaluator
from agents.screenplay.evaluators.scene_character import SceneCharacterEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

# 夹具行数口径：默认 9 行 → 3 页（lines_per_page=3），目标 3 页 ± 0（收窄到夹具可行域）
_FIXTURE_PAGE_WINDOW = {"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3}


def _config(**overrides) -> ScreenplayConfig:
    """夹具配置：真实 movie.yaml + 页数窗口缩放 + 定向覆盖。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    raw["screenplay"].update(_FIXTURE_PAGE_WINDOW)
    raw["screenplay"].update(overrides)
    return ScreenplayConfig.from_dict(raw)


def _real_config() -> ScreenplayConfig:
    """真实配置（未缩放）：长形态工件（pages×lines_per_page）过页数门禁的对照路径。"""
    return ScreenplayConfig.from_dict(copy.deepcopy(_REAL_CONFIG))


def _ref() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


def _evaluate(evaluator, artifact) -> object:
    return evaluator.evaluate(_ref(), {"artifact": artifact})


def _artifact_with_kinds(make_script_artifact, dialogue: int, action: int) -> ScriptArtifact:
    """按对白/动作行数构造工件（比例边界用例：行类型与总数精确可控）。"""
    payload = make_script_artifact().to_dict()
    kinds = ["dialogue"] * dialogue + ["action"] * action
    lines = []
    for index, kind in enumerate(kinds):
        line = dict(payload["lines"][index % len(payload["lines"])])
        line["line_id"] = f"r-l{index + 1}"
        line["scene_id"] = "scene-1"
        line["kind"] = kind
        line["character"] = "林静" if kind == "dialogue" else None
        lines.append(line)
    payload["lines"] = lines
    return ScriptArtifact.from_dict(payload)


class TestBeatStructure:
    """C4：节拍表结构完整性（gate，配置驱动）。"""

    @pytest.fixture()
    def evaluator(self):
        return BeatStructureEvaluator(_config().beat_sheet)

    def test_合法工件全过(self, evaluator, make_script_artifact):
        result = _evaluate(evaluator, make_script_artifact())
        assert result.score == 1.0
        assert result.diagnostics["applicable"] is True
        assert result.diagnostics["violations"] == []
        assert result.diagnostics["required_beat_count"] == 8
        assert result.diagnostics["missing_required"] == []

    def test_可选节拍缺失不违规(self, evaluator, make_script_artifact):
        """可选节拍（required=False）不在存在性要求内（夹具默认即不含 theme_stated）。"""
        artifact = make_script_artifact()
        assert "theme_stated" not in artifact.beat_ids()
        assert _evaluate(evaluator, artifact).score == 1.0

    def test_可选节拍存在同样合法(self, evaluator, make_script_artifact):
        payload = make_script_artifact().to_dict()
        payload["beats"].insert(
            1,
            {
                "beat_id": "theme_stated",
                "act": "act1",
                "required": False,
                "description": "主题陈述",
            },
        )
        assert _evaluate(evaluator, ScriptArtifact.from_dict(payload)).score == 1.0

    def test_缺关键节拍判零(self, evaluator, make_script_artifact):
        """C4：required 节拍缺失 → 判 0 并逐条列出缺口。"""
        result = _evaluate(evaluator, make_script_artifact("missing_beat"))
        assert result.score == 0.0
        assert result.diagnostics["missing_required"] == ["climax"]
        assert any("climax" in violation for violation in result.diagnostics["violations"])

    def test_非配置节拍判零(self, evaluator, make_script_artifact):
        """结构不可解析：工件含配置节拍表外的节拍（无法对照判定）。"""
        payload = make_script_artifact().to_dict()
        payload["beats"].append(
            {"beat_id": "deus_ex_machina", "act": "act3", "required": True, "description": "天降"}
        )
        result = _evaluate(evaluator, ScriptArtifact.from_dict(payload))
        assert result.score == 0.0
        assert result.diagnostics["unknown_beats"] == ["deus_ex_machina"]

    def test_幕序回退判零(self, evaluator, make_script_artifact):
        """序列结构不可解析：节拍所属幕回退（act3 节拍排到 act1 之前）。"""
        payload = make_script_artifact().to_dict()
        payload["beats"].insert(0, payload["beats"].pop())  # resolution(act3) 提到最前
        result = _evaluate(evaluator, ScriptArtifact.from_dict(payload))
        assert result.score == 0.0
        assert result.diagnostics["regressions"]
        assert any("幕序回退" in violation for violation in result.diagnostics["violations"])

    def test_缺幕判零(self, make_script_artifact):
        """三幕齐备：配置涉幕（含仅可选节拍的幕）必须在工件中出现。"""
        config = _config()
        beat_sheet = [{**beat, "required": beat["act"] != "act3"} for beat in config.beat_sheet]
        evaluator = BeatStructureEvaluator(beat_sheet)
        payload = make_script_artifact().to_dict()
        payload["beats"] = [beat for beat in payload["beats"] if beat["act"] != "act3"]
        result = _evaluate(evaluator, ScriptArtifact.from_dict(payload))
        assert result.score == 0.0
        assert result.diagnostics["missing_acts"] == ["act3"]

    def test_节拍表缺失拒绝启动(self):
        with pytest.raises(ScreenplayConfigError, match="beat_sheet"):
            BeatStructureEvaluator([])

    def test_元数据与版本冻结(self):
        evaluator = BeatStructureEvaluator(_config().beat_sheet)
        assert evaluator.spec.evaluator_id == "rule.beat_structure"
        assert evaluator.spec.kind is EvaluatorKind.RULE
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.cost_per_call == 0.0
        same = BeatStructureEvaluator(_config().beat_sheet)
        assert same.spec.version == evaluator.spec.version
        changed = BeatStructureEvaluator(
            [
                *(_config().beat_sheet),
                {"beat_id": "extra", "act": "act3", "required": False, "description": "另加"},
            ]
        )
        assert changed.spec.version != evaluator.spec.version  # 配置变更即版本变更


class TestPageMinutes:
    """C5：页数-时长换算（gate）。"""

    def test_界内通过(self, make_script_artifact):
        result = _evaluate(
            PageMinutesEvaluator(_config().page_minutes_slice), make_script_artifact()
        )
        assert result.score == 1.0
        assert result.diagnostics["pages"] == pytest.approx(3.0)
        assert result.diagnostics["line_count"] == 9

    def test_容差边界通过(self, make_script_artifact):
        """页数 == 目标 + 容差（闭区间）通过。"""
        config = _config(target_duration_min=3, page_tolerance=1)
        artifact = make_script_artifact(lines_per_scene=4)  # 12 行 → 4 页
        result = _evaluate(PageMinutesEvaluator(config.page_minutes_slice), artifact)
        assert result.score == 1.0
        assert result.diagnostics["pages"] == pytest.approx(4.0)

    def test_越界判零(self, make_script_artifact):
        """C5：页数越界 → 判 0（夹具三倍行数变体 → 9 页）。"""
        result = _evaluate(
            PageMinutesEvaluator(_config().page_minutes_slice),
            make_script_artifact("page_out_of_range"),
        )
        assert result.score == 0.0
        assert result.diagnostics["violations"]
        assert result.diagnostics["pages"] == pytest.approx(9.0)
        assert result.diagnostics["target_pages"] == 3

    def test_行数不足同样越界(self, make_script_artifact):
        config = _config(target_duration_min=3, page_tolerance=0, lines_per_page=45)
        result = _evaluate(PageMinutesEvaluator(config.page_minutes_slice), make_script_artifact())
        assert result.score == 0.0
        assert result.diagnostics["pages"] == pytest.approx(0.2)

    def test_真实配置长形态工件通过(self, make_script_artifact):
        """真实配置（90 分钟 × 45 行/页）下的长形态工件：4050 行 → 90 页 ∈ [85, 95]。"""
        artifact = make_script_artifact(pages=90, lines_per_page=45)
        result = _evaluate(PageMinutesEvaluator(_real_config().page_minutes_slice), artifact)
        assert result.score == 1.0
        assert result.diagnostics["line_count"] == 4050
        assert result.diagnostics["pages"] == pytest.approx(90.0)

    @pytest.mark.parametrize("key", ["target_duration_min", "page_tolerance", "lines_per_page"])
    def test_缺配置项拒绝启动(self, key):
        page_minutes = dict(_config().page_minutes_slice)
        del page_minutes[key]
        with pytest.raises(ScreenplayConfigError, match=key):
            PageMinutesEvaluator(page_minutes)

    @pytest.mark.parametrize("bad", [0, -1, 1.5, True])
    def test_lines_per_page_非法拒绝启动(self, bad):
        page_minutes = {**_config().page_minutes_slice, "lines_per_page": bad}
        with pytest.raises(ScreenplayConfigError, match="lines_per_page"):
            PageMinutesEvaluator(page_minutes)

    def test_元数据(self):
        evaluator = PageMinutesEvaluator(_config().page_minutes_slice)
        assert evaluator.spec.evaluator_id == "rule.page_minutes"
        assert evaluator.spec.kind is EvaluatorKind.RULE
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.cost_per_call == 0.0


class TestSceneCharacter:
    """C6：场景-角色一致性（gate，别名表 = 登记口径）。"""

    @pytest.fixture()
    def evaluator(self):
        return SceneCharacterEvaluator(_config().character_aliases)

    def test_合法工件全过(self, evaluator, make_script_artifact):
        result = _evaluate(evaluator, make_script_artifact())
        assert result.score == 1.0
        assert result.diagnostics["ghost_characters"] == []
        assert result.diagnostics["location_mismatches"] == []
        assert result.diagnostics["scene_count"] == 3

    def test_幽灵角色判零(self, evaluator, make_script_artifact):
        """C6 场景 3：出场角色未登记（赵护士）→ 判 0 并逐条诊断。"""
        result = _evaluate(evaluator, make_script_artifact("ghost_character"))
        assert result.score == 0.0
        assert result.diagnostics["ghost_characters"] == ["scene-2:赵护士"]
        assert any("赵护士" in violation for violation in result.diagnostics["violations"])

    def test_地点不一致判零(self, evaluator, make_script_artifact):
        """C6：场景头地点段与 location 字段不一致（走廊 vs 病房）→ 判 0。"""
        result = _evaluate(evaluator, make_script_artifact("location_mismatch"))
        assert result.score == 0.0
        assert result.diagnostics["location_mismatches"] == [
            {"scene_id": "scene-2", "heading_location": "病房", "location": "走廊"}
        ]

    def test_登记别名合法(self, evaluator, make_script_artifact):
        """出场角色用登记别名（阿静）→ 合法（别名表即登记口径，非幽灵角色）。"""
        payload = make_script_artifact().to_dict()
        payload["scenes"][0]["characters"] = ["阿静", "陈默", "周医生"]
        assert _evaluate(evaluator, ScriptArtifact.from_dict(payload)).score == 1.0

    def test_角色表缺失拒绝启动(self):
        """角色表缺失即报错（不得静默放过门禁，配置纪律）。"""
        with pytest.raises(ScreenplayConfigError, match="character_aliases"):
            SceneCharacterEvaluator({})

    def test_元数据与版本含别名表(self):
        evaluator = SceneCharacterEvaluator(_config().character_aliases)
        assert evaluator.spec.evaluator_id == "rule.scene_character"
        assert evaluator.spec.kind is EvaluatorKind.RULE
        assert evaluator.spec.deterministic is True
        changed = SceneCharacterEvaluator({"林静": ("静静",)})
        assert changed.spec.version != evaluator.spec.version


class TestDialogueActionRatio:
    """C7：对白行占比（gate）。"""

    @pytest.fixture()
    def evaluator(self):
        return DialogueActionRatioEvaluator(_config().dialogue_action_ratio)

    def test_合法工件全过(self, evaluator, make_script_artifact):
        result = _evaluate(evaluator, make_script_artifact())
        assert result.score == 1.0
        assert result.diagnostics["dialogue_ratio"] == pytest.approx(round(6 / 9, 6))
        assert result.diagnostics["dialogue_lines"] == 6
        assert result.diagnostics["action_lines"] == 3

    @pytest.mark.parametrize("dialogue, action", [(4, 1), (2, 3)], ids=["上界", "下界"])
    def test_区间边界通过(self, evaluator, make_script_artifact, dialogue, action):
        artifact = _artifact_with_kinds(make_script_artifact, dialogue, action)
        assert _evaluate(evaluator, artifact).score == 1.0

    @pytest.mark.parametrize("dialogue, action", [(5, 0), (1, 4)], ids=["对白过高", "对白过低"])
    def test_越界判零(self, evaluator, make_script_artifact, dialogue, action):
        artifact = _artifact_with_kinds(make_script_artifact, dialogue, action)
        result = _evaluate(evaluator, artifact)
        assert result.score == 0.0
        assert result.diagnostics["violations"]

    def test_比例失衡夹具判零(self, evaluator, make_script_artifact):
        """夹具比例失衡变体（全部对白 → 1.0 > 上限）判 0。"""
        result = _evaluate(evaluator, make_script_artifact("ratio_imbalance"))
        assert result.score == 0.0
        assert result.diagnostics["dialogue_ratio"] == pytest.approx(1.0)

    @pytest.mark.parametrize("key", ["min", "max"])
    def test_缺区间项拒绝启动(self, key):
        ratio = dict(_config().dialogue_action_ratio)
        del ratio[key]
        with pytest.raises(ScreenplayConfigError, match=key):
            DialogueActionRatioEvaluator(ratio)

    def test_区间倒置拒绝启动(self):
        with pytest.raises(ScreenplayConfigError, match="dialogue_action_ratio"):
            DialogueActionRatioEvaluator({"min": 0.8, "max": 0.4})

    def test_元数据(self):
        evaluator = DialogueActionRatioEvaluator(_config().dialogue_action_ratio)
        assert evaluator.spec.evaluator_id == "rule.dialogue_action_ratio"
        assert evaluator.spec.kind is EvaluatorKind.RULE
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.cost_per_call == 0.0


class Test合法工件四门齐过:
    def test_四_gate_全过(self, make_script_artifact):
        """合法工件（默认 9 行 + 缩放页数窗口）四 gate 齐过——US2 全过路径。"""
        config = _config()
        artifact = make_script_artifact()
        results = [
            _evaluate(BeatStructureEvaluator(config.beat_sheet), artifact),
            _evaluate(PageMinutesEvaluator(config.page_minutes_slice), artifact),
            _evaluate(SceneCharacterEvaluator(config.character_aliases), artifact),
            _evaluate(DialogueActionRatioEvaluator(config.dialogue_action_ratio), artifact),
        ]
        assert [result.score for result in results] == [1.0, 1.0, 1.0, 1.0]
