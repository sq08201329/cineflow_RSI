"""ScreenplayConfig 配置测试（功能 009 / T907，先于实现编写）。

C3 落点：screenplay 段解析（目标时长/页数容差/行-页换算/对白行占比区间/节拍表/角色
别名表/升级判据阈值/生成与 judge 价目与模型/judge 提示词与锚点大纲集）；**节拍表、
别名表、判据阈值缺失即报错**（不允许静默放过门禁或无判据）；价目缺失或为零即报错
（不允许静默零成本，原则三）；judge 锚点集解析为 outline 阶段 ScriptArtifact。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.config import ScreenplayConfig, ScreenplayConfigError
from core.tree.errors import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _valid_dict() -> dict:
    """最小合法配置字典（真实 movie.yaml 的深拷贝，逐用例定向破坏）。"""
    return copy.deepcopy(_REAL_CONFIG)


class Test真实配置解析:
    def test_真实_yaml_全字段解析(self):
        cfg = ScreenplayConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert cfg.target_duration_min == 90
        assert cfg.page_tolerance == 5
        assert cfg.lines_per_page == 45

    def test_页数门禁配置切片(self):
        """页数门禁构造入参切片（rule.page_minutes 与测试共用，缺项由评估器报错）。"""
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.page_minutes_slice == {
            "target_duration_min": 90,
            "page_tolerance": 5,
            "lines_per_page": 45,
        }

    def test_对白行占比区间读取(self):
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.dialogue_action_ratio == {"min": 0.4, "max": 0.8}

    def test_节拍表读取(self):
        """节拍表 = 门禁与执行前校验共用的单一事实源（缺即报错）。"""
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.beat_ids() == (
            "opening_image",
            "theme_stated",
            "inciting_incident",
            "act1_turn",
            "midpoint",
            "dark_night",
            "act2_turn",
            "climax",
            "resolution",
        )
        assert cfg.required_beat_ids() == (
            "opening_image",
            "inciting_incident",
            "act1_turn",
            "midpoint",
            "dark_night",
            "act2_turn",
            "climax",
            "resolution",
        )
        assert "theme_stated" not in cfg.required_beat_ids()  # 可选节拍
        assert {beat["act"] for beat in cfg.beat_sheet} == {"act1", "act2", "act3"}

    def test_别名表读取(self):
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.character_aliases == {
            "林静": ("阿静",),
            "陈默": ("默哥",),
            "周医生": (),
        }
        assert cfg.registered_names() == frozenset({"林静", "阿静", "陈默", "默哥", "周医生"})
        assert cfg.canonical_of("默哥") == "陈默"
        assert cfg.canonical_of("林静") == "林静"
        assert cfg.canonical_of("小静") is None
        assert cfg.is_registered("阿静") is True
        assert cfg.is_registered("小静") is False

    def test_判据阈值读取(self):
        """升级判据阈值配置化（澄清 Q1：自动判定口径由配置给出）。"""
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.upgrade_criteria == {
            "judge_r_target": 0.6,
            "min_samples": 5,
            "drift_band": 0.1,
            "gate_violation_max": 0.2,
        }

    def test_生成模型与价目读取(self):
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.model == "mock-copy-v1"
        assert cfg.model_prices["mock-copy-v1"]["prompt_per_1k"] == 0.001
        assert cfg.price_of("mock-copy-v1")["completion_per_1k"] == 0.002

    def test_judge_提示词与锚点大纲集(self):
        """锚点集经同一解析路径产出 outline 阶段工件（成对比较的对照面）。"""
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.judge["model"] == "mock-copy-v1"
        assert len(cfg.judge["prompts"]) == 3
        assert len(cfg.anchor_outlines) == 2
        assert all(isinstance(anchor, ScriptArtifact) for anchor in cfg.anchor_outlines)
        assert {anchor.stage for anchor in cfg.anchor_outlines} == {"outline"}
        assert cfg.anchor_outlines[0].scene_ids() == ("anchor-a1", "anchor-a2")
        assert cfg.anchor_outlines[1].beat_ids() == ("opening_image", "dark_night", "resolution")

    def test_权重节引用(self):
        cfg = ScreenplayConfig.from_dict(_valid_dict())
        assert cfg.evaluator_weights == {
            "rule.beat_structure": "gate",
            "rule.page_minutes": "gate",
            "rule.scene_character": "gate",
            "rule.dialogue_action_ratio": "gate",
            "proxy.entity_consistency": 0.5,
            "proxy.timeline_conflict": 0.5,
            "judge.dramatic_tension": 0.5,
        }


class Test缺失即报错:
    def test_缺_screenplay_段(self):
        config = _valid_dict()
        del config["screenplay"]
        with pytest.raises(ScreenplayConfigError, match="screenplay"):
            ScreenplayConfig.from_dict(config)

    def test_缺权重节(self):
        config = _valid_dict()
        del config["evaluator_weights"]["screenplay"]
        with pytest.raises(ScreenplayConfigError, match="evaluator_weights"):
            ScreenplayConfig.from_dict(config)

    @pytest.mark.parametrize(
        "key",
        [
            "target_duration_min",
            "page_tolerance",
            "lines_per_page",
            "dialogue_action_ratio",
            "beat_sheet",
            "character_aliases",
            "upgrade_criteria",
            "model",
            "model_prices",
            "judge",
        ],
    )
    def test_缺字段即报错(self, key):
        config = _valid_dict()
        del config["screenplay"][key]
        with pytest.raises(ScreenplayConfigError, match=key):
            ScreenplayConfig.from_dict(config)

    @pytest.mark.parametrize(
        "key, value",
        [
            ("target_duration_min", 0),
            ("target_duration_min", 90.5),
            ("page_tolerance", -1),
            ("lines_per_page", 0),
            ("lines_per_page", 1.5),
        ],
    )
    def test_取值域非法即报错(self, key, value):
        config = _valid_dict()
        config["screenplay"][key] = value
        with pytest.raises(ScreenplayConfigError, match=key):
            ScreenplayConfig.from_dict(config)


class Test比例区间纪律:
    def test_区间上界不大于下界即报错(self):
        config = _valid_dict()
        config["screenplay"]["dialogue_action_ratio"] = {"min": 0.8, "max": 0.4}
        with pytest.raises(ScreenplayConfigError, match="dialogue_action_ratio"):
            ScreenplayConfig.from_dict(config)

    @pytest.mark.parametrize(
        "ratio",
        [{"min": -0.1, "max": 0.5}, {"min": 0.4, "max": 1.2}, {"min": 0.4}, {"max": 0.8}],
        ids=["下界越界", "上界越界", "缺上界", "缺下界"],
    )
    def test_区间非法即报错(self, ratio):
        config = _valid_dict()
        config["screenplay"]["dialogue_action_ratio"] = ratio
        with pytest.raises(ScreenplayConfigError, match="dialogue_action_ratio"):
            ScreenplayConfig.from_dict(config)

    def test_非数值即报错(self):
        config = _valid_dict()
        config["screenplay"]["dialogue_action_ratio"] = {"min": "0.4", "max": 0.8}
        with pytest.raises(ScreenplayConfigError, match="dialogue_action_ratio"):
            ScreenplayConfig.from_dict(config)


class Test节拍表纪律:
    """节拍表缺失即报错——不允许静默放过门禁（C3 / 原则五）。"""

    def test_空节拍表即报错(self):
        config = _valid_dict()
        config["screenplay"]["beat_sheet"] = []
        with pytest.raises(ScreenplayConfigError, match="beat_sheet"):
            ScreenplayConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["beat_id", "act", "required", "description"])
    def test_节拍缺字段即报错(self, key):
        config = _valid_dict()
        del config["screenplay"]["beat_sheet"][0][key]
        with pytest.raises(ScreenplayConfigError, match=key):
            ScreenplayConfig.from_dict(config)

    def test_节拍_id_重复即报错(self):
        config = _valid_dict()
        config["screenplay"]["beat_sheet"][1]["beat_id"] = "opening_image"
        with pytest.raises(ScreenplayConfigError, match="beat_id"):
            ScreenplayConfig.from_dict(config)

    def test_无关键节拍即报错(self):
        """全部节拍都标 optional 等于无节拍可判——门禁会恒过，配置层拒绝。"""
        config = _valid_dict()
        for beat in config["screenplay"]["beat_sheet"]:
            beat["required"] = False
        with pytest.raises(ScreenplayConfigError, match="required"):
            ScreenplayConfig.from_dict(config)

    def test_required_非布尔即报错(self):
        config = _valid_dict()
        config["screenplay"]["beat_sheet"][0]["required"] = "yes"
        with pytest.raises(ScreenplayConfigError, match="required"):
            ScreenplayConfig.from_dict(config)

    def test_节拍不属于三幕即报错(self):
        config = _valid_dict()
        config["screenplay"]["beat_sheet"][0]["act"] = "act4"
        with pytest.raises(ScreenplayConfigError, match="act"):
            ScreenplayConfig.from_dict(config)


class Test别名表纪律:
    """别名表缺失即报错——实体一致性代理无规范化依据时不得静默打分。"""

    def test_空别名表即报错(self):
        config = _valid_dict()
        config["screenplay"]["character_aliases"] = {}
        with pytest.raises(ScreenplayConfigError, match="character_aliases"):
            ScreenplayConfig.from_dict(config)

    def test_别名跨角色复用即报错(self):
        """同一写法归属两个角色 → 规范化多义（同名异写判定失真），配置层拒绝。"""
        config = _valid_dict()
        config["screenplay"]["character_aliases"]["陈默"] = ["阿静"]
        with pytest.raises(ScreenplayConfigError, match="阿静"):
            ScreenplayConfig.from_dict(config)

    def test_别名与规范名冲突即报错(self):
        config = _valid_dict()
        config["screenplay"]["character_aliases"]["周医生"] = ["林静"]
        with pytest.raises(ScreenplayConfigError, match="林静"):
            ScreenplayConfig.from_dict(config)

    def test_别名非字符串列表即报错(self):
        config = _valid_dict()
        config["screenplay"]["character_aliases"]["林静"] = "阿静"
        with pytest.raises(ScreenplayConfigError, match="character_aliases"):
            ScreenplayConfig.from_dict(config)

    def test_空别名列表合法(self):
        """无别名角色（空列表）是合法形态——别名可选但表本身必填。"""
        config = _valid_dict()
        config["screenplay"]["character_aliases"]["陈默"] = []
        cfg = ScreenplayConfig.from_dict(config)
        assert cfg.character_aliases["陈默"] == ()


class Test判据阈值纪律:
    @pytest.mark.parametrize(
        "key", ["judge_r_target", "min_samples", "drift_band", "gate_violation_max"]
    )
    def test_缺阈值即报错(self, key):
        """阈值缺失即报错——不允许静默"无判据"（澄清 Q1 / FR-010）。"""
        config = _valid_dict()
        del config["screenplay"]["upgrade_criteria"][key]
        with pytest.raises(ScreenplayConfigError, match=key):
            ScreenplayConfig.from_dict(config)

    @pytest.mark.parametrize(
        "key, value",
        [
            ("judge_r_target", 0.0),
            ("judge_r_target", 1.2),
            ("min_samples", 0),
            ("min_samples", 2.5),
            ("drift_band", -0.1),
            ("gate_violation_max", 1.5),
        ],
    )
    def test_阈值取值域非法即报错(self, key, value):
        config = _valid_dict()
        config["screenplay"]["upgrade_criteria"][key] = value
        with pytest.raises(ScreenplayConfigError, match=key):
            ScreenplayConfig.from_dict(config)

    def test_空判据段即报错(self):
        config = _valid_dict()
        config["screenplay"]["upgrade_criteria"] = {}
        with pytest.raises(ScreenplayConfigError, match="upgrade_criteria"):
            ScreenplayConfig.from_dict(config)


class Test价目与模型纪律:
    def test_缺模型名即报错(self):
        config = _valid_dict()
        del config["screenplay"]["model"]
        with pytest.raises(ScreenplayConfigError, match="model"):
            ScreenplayConfig.from_dict(config)

    def test_模型不在价目表即报错(self):
        """缺价目即报错——不允许静默零成本（原则三 / 003 同款纪律）。"""
        config = _valid_dict()
        config["screenplay"]["model"] = "mock-copy-v9"
        with pytest.raises(ScreenplayConfigError, match="mock-copy-v9"):
            ScreenplayConfig.from_dict(config)

    def test_judge_模型不在价目表即报错(self):
        config = _valid_dict()
        config["screenplay"]["judge"]["model"] = "mock-copy-v9"
        with pytest.raises(ScreenplayConfigError, match="mock-copy-v9"):
            ScreenplayConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["prompt_per_1k", "completion_per_1k"])
    def test_价目缺字段即报错(self, key):
        config = _valid_dict()
        del config["screenplay"]["model_prices"]["mock-copy-v1"][key]
        with pytest.raises(ScreenplayConfigError, match=key):
            ScreenplayConfig.from_dict(config)

    def test_价目为零即报错(self):
        config = _valid_dict()
        config["screenplay"]["model_prices"]["mock-copy-v1"]["prompt_per_1k"] = 0.0
        with pytest.raises(ScreenplayConfigError, match="prompt_per_1k"):
            ScreenplayConfig.from_dict(config)


class TestJudge纪律:
    def test_提示词为空即报错(self):
        config = _valid_dict()
        config["screenplay"]["judge"]["prompts"] = []
        with pytest.raises(ScreenplayConfigError, match="prompts"):
            ScreenplayConfig.from_dict(config)

    def test_缺_judge_模型名即报错(self):
        config = _valid_dict()
        del config["screenplay"]["judge"]["model"]
        with pytest.raises(ScreenplayConfigError, match="model"):
            ScreenplayConfig.from_dict(config)

    def test_锚点集为空即报错(self):
        config = _valid_dict()
        config["screenplay"]["judge"]["anchor_outlines"] = []
        with pytest.raises(ScreenplayConfigError, match="anchor_outlines"):
            ScreenplayConfig.from_dict(config)

    def test_锚点阶段非大纲即报错(self):
        """judge 仅作用于大纲阶段（research 决策 2）：锚点集必须是大纲工件。"""
        config = _valid_dict()
        config["screenplay"]["judge"]["anchor_outlines"][0]["stage"] = "script"
        with pytest.raises(ScreenplayConfigError, match="outline"):
            ScreenplayConfig.from_dict(config)

    def test_锚点缺结构化标记即报错(self):
        config = _valid_dict()
        config["screenplay"]["judge"]["anchor_outlines"][0]["beats"] = []
        with pytest.raises(ValidationError, match="beats"):
            ScreenplayConfig.from_dict(config)
