"""StoryboardConfig 配置测试（功能 008 / T811，先于实现编写）。

C3 落点：storyboard 段解析（预算/单轮组数/镜头语法规则库/轴规则/情绪向量表/
渲染价目与编码参数/judge 提示词与锚点 ShotList 集）；规则库缺失即报错、价目缺失
即报错（不允许静默放过门禁或静默零成本，原则三/五）；编码参数强制单线程确定性档；
情绪向量表维度与取值域校验；judge 锚点集解析为 ShotList。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.storyboard.config import StoryboardConfig, StoryboardConfigError
from agents.storyboard.shotlist import ShotList

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _valid_dict() -> dict:
    """最小合法配置字典（真实 movie.yaml 的深拷贝，逐用例定向破坏）。"""
    return copy.deepcopy(_REAL_CONFIG)


class Test真实配置解析:
    def test_真实_yaml_全字段解析(self):
        cfg = StoryboardConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert cfg.exploration_per_round_usd == 200.0
        assert cfg.boards_per_round == 3

    def test_镜头语法规则库读取(self):
        """执行前校验与 gate 共用的规则库（配置单一事实源）。"""
        cfg = StoryboardConfig.from_dict(_valid_dict())
        grammar = cfg.shot_grammar
        assert grammar["shot_sizes"] == [
            "extreme_close_up",
            "close_up",
            "medium",
            "full",
            "wide",
        ]
        assert grammar["max_size_jump"] == 2
        assert grammar["max_same_size_run"] == 2
        assert "side" in grammar["camera_positions"]  # 机位档位含侧别（轴规则机检口径）
        assert grammar["movements"][0] == "static"

    def test_轴规则读取(self):
        cfg = StoryboardConfig.from_dict(_valid_dict())
        assert cfg.axis_rules["require_transition_on_cross"] is True
        assert cfg.axis_rules["allowed_transition_shots"] == 1
        assert cfg.axis_rules["side_field"] == "side"

    def test_情绪向量表读取(self):
        cfg = StoryboardConfig.from_dict(_valid_dict())
        assert sorted(cfg.emotion_vectors) == ["awe", "calm", "joyful", "sorrow", "tense"]
        assert len(cfg.emotion_vectors["tense"]) == 3

    def test_对齐口径读取(self):
        """对齐映射阈值配置化（原则五）：cos ≤ 下限 → 0 分。"""
        cfg = StoryboardConfig.from_dict(_valid_dict())
        assert cfg.alignment == {"cos_floor": 0.90}

    def test_渲染价目与编码单线程档(self):
        cfg = StoryboardConfig.from_dict(_valid_dict())
        assert cfg.render["price_per_shot_usd"] == 0.06
        assert cfg.render["fps"] == 8
        assert cfg.render["encode_threads"] == 1  # 单线程确定性档

    def test_judge_锚点集解析为_ShotList(self):
        """judge 输入 = ShotList 摘要：锚点集解析为 ShotList 供摘要。"""
        cfg = StoryboardConfig.from_dict(_valid_dict())
        assert cfg.judge["model"] == "mock-copy-v1"
        assert len(cfg.judge["prompts"]) == 3
        assert len(cfg.anchor_shotlists) == 2
        assert all(isinstance(sl, ShotList) for sl in cfg.anchor_shotlists)
        assert cfg.anchor_shotlists[0].shot_ids() == ("shot-anchor-a1", "shot-anchor-a2")
        assert cfg.anchor_shotlists[1].shot("shot-anchor-b2").alternatives == 4

    def test_权重节引用(self):
        cfg = StoryboardConfig.from_dict(_valid_dict())
        assert cfg.evaluator_weights == {
            "rule.shot_grammar": "gate",
            "rule.coverage": "gate",
            "rule.axis_rule": "gate",
            "proxy.emotion_alignment": 0.5,
            "judge.script_fit": 0.5,
        }


class Test缺失即报错:
    def test_缺_storyboard_段(self):
        config = _valid_dict()
        del config["storyboard"]
        with pytest.raises(StoryboardConfigError, match="storyboard"):
            StoryboardConfig.from_dict(config)

    def test_缺权重节(self):
        config = _valid_dict()
        del config["evaluator_weights"]["storyboard"]
        with pytest.raises(StoryboardConfigError, match="evaluator_weights"):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["exploration_per_round_usd", "boards_per_round"])
    def test_缺预算与组数字段(self, key):
        config = _valid_dict()
        del config["storyboard"][key]
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize(
        "key",
        ["shot_grammar", "axis_rules", "alignment", "emotion_vectors", "render", "judge"],
    )
    def test_缺结构段(self, key):
        config = _valid_dict()
        del config["storyboard"][key]
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)


class Test规则库纪律:
    """规则库缺失即报错——不允许静默放过门禁（C3 / 原则五）。"""

    @pytest.mark.parametrize(
        "key", ["shot_sizes", "camera_positions", "movements", "max_size_jump", "max_same_size_run"]
    )
    def test_规则库缺字段即报错(self, key):
        config = _valid_dict()
        del config["storyboard"]["shot_grammar"][key]
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["shot_sizes", "camera_positions", "movements"])
    def test_档位枚举空即报错(self, key):
        config = _valid_dict()
        config["storyboard"]["shot_grammar"][key] = []
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)

    def test_景别档位重复即报错(self):
        """景别档位是"有序枚举"：重复档位破坏序号差语义（跳跃上限判定依据）。"""
        config = _valid_dict()
        config["storyboard"]["shot_grammar"]["shot_sizes"] = ["close_up", "medium", "close_up"]
        with pytest.raises(StoryboardConfigError, match="shot_sizes"):
            StoryboardConfig.from_dict(config)

    def test_机位档位须含_side(self):
        """轴规则机检依赖侧机位档位（机位档位含 side，research 决策 5）。"""
        config = _valid_dict()
        config["storyboard"]["shot_grammar"]["camera_positions"] = ["eye_level", "low_angle"]
        with pytest.raises(StoryboardConfigError, match="side"):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["max_size_jump", "max_same_size_run"])
    def test_上限取值域非法即报错(self, key):
        config = _valid_dict()
        config["storyboard"]["shot_grammar"][key] = -1
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize(
        "key", ["require_transition_on_cross", "allowed_transition_shots", "side_field"]
    )
    def test_轴规则缺字段即报错(self, key):
        config = _valid_dict()
        del config["storyboard"]["axis_rules"][key]
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)


class Test价目纪律:
    def test_缺_render_段即报错(self):
        config = _valid_dict()
        del config["storyboard"]["render"]
        with pytest.raises(StoryboardConfigError, match="render"):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["price_per_shot_usd", "fps", "width", "height"])
    def test_缺渲染字段即报错(self, key):
        config = _valid_dict()
        del config["storyboard"]["render"][key]
        with pytest.raises(StoryboardConfigError, match=key):
            StoryboardConfig.from_dict(config)

    def test_价目为零即报错(self):
        """缺价目/零价目即报错——不允许静默零成本（原则三/003 同款纪律）。"""
        config = _valid_dict()
        config["storyboard"]["render"]["price_per_shot_usd"] = 0.0
        with pytest.raises(StoryboardConfigError, match="price_per_shot_usd"):
            StoryboardConfig.from_dict(config)

    def test_编码非单线程档拒绝(self):
        """编码线程数 ≠ 1 破坏逐字节复现纪律（SC-002），配置层拒绝。"""
        config = _valid_dict()
        config["storyboard"]["render"]["encode_threads"] = 4
        with pytest.raises(StoryboardConfigError, match="encode_threads"):
            StoryboardConfig.from_dict(config)


class Test对齐口径纪律:
    def test_缺_cos_floor_即报错(self):
        config = _valid_dict()
        del config["storyboard"]["alignment"]["cos_floor"]
        with pytest.raises(StoryboardConfigError, match="cos_floor"):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize("value", [1.0, -1.5, "0.9", True])
    def test_cos_floor_取值域非法即报错(self, value):
        config = _valid_dict()
        config["storyboard"]["alignment"]["cos_floor"] = value
        with pytest.raises(StoryboardConfigError, match="cos_floor"):
            StoryboardConfig.from_dict(config)


class Test情绪向量纪律:
    def test_空向量表即报错(self):
        config = _valid_dict()
        config["storyboard"]["emotion_vectors"] = {}
        with pytest.raises(StoryboardConfigError, match="emotion_vectors"):
            StoryboardConfig.from_dict(config)

    def test_维度非三即报错(self):
        config = _valid_dict()
        config["storyboard"]["emotion_vectors"]["calm"] = [0.3, 0.5]
        with pytest.raises(StoryboardConfigError, match="calm"):
            StoryboardConfig.from_dict(config)

    @pytest.mark.parametrize("value", [1.5, -0.2, "0.5"])
    def test_分量越界或非数即报错(self, value):
        config = _valid_dict()
        config["storyboard"]["emotion_vectors"]["calm"] = [value, 0.5, 0.5]
        with pytest.raises(StoryboardConfigError, match="calm"):
            StoryboardConfig.from_dict(config)


class TestJudge纪律:
    def test_提示词为空即报错(self):
        config = _valid_dict()
        config["storyboard"]["judge"]["prompts"] = []
        with pytest.raises(StoryboardConfigError, match="prompts"):
            StoryboardConfig.from_dict(config)

    def test_缺模型名即报错(self):
        """judge 走网关计费：模型名须显式配置（缺价目网关即报错，不允许静默零成本）。"""
        config = _valid_dict()
        del config["storyboard"]["judge"]["model"]
        with pytest.raises(StoryboardConfigError, match="model"):
            StoryboardConfig.from_dict(config)

    def test_模型名为空即报错(self):
        config = _valid_dict()
        config["storyboard"]["judge"]["model"] = ""
        with pytest.raises(StoryboardConfigError, match="model"):
            StoryboardConfig.from_dict(config)

    def test_锚点集为空即报错(self):
        config = _valid_dict()
        config["storyboard"]["judge"]["anchor_shotlists"] = []
        with pytest.raises(StoryboardConfigError, match="anchor_shotlists"):
            StoryboardConfig.from_dict(config)

    def test_锚点_ShotList_非法即报错(self):
        from core.tree.errors import ValidationError

        config = _valid_dict()
        config["storyboard"]["judge"]["anchor_shotlists"][0]["shots"] = []
        with pytest.raises(ValidationError, match="shots"):
            StoryboardConfig.from_dict(config)
