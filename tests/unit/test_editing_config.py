"""EditingConfig 配置测试（功能 007 / T711，先于实现编写）。

C3 落点：editing 段解析（预算/时长容差/镜头限制/转场规则库/分段基准曲线/渲染
价目与编码参数/judge 提示词与锚点 EDL 集）；基准曲线缺失即报错、价目缺失即报错
（不允许静默无基准打分/零成本，原则三/五）；编码参数强制单线程确定性档。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.editing.config import EditingConfig, EditingConfigError
from agents.editing.edl import EditDecisionList

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _valid_dict() -> dict:
    """最小合法配置字典（真实 movie.yaml 的深拷贝，逐用例定向破坏）。"""
    return copy.deepcopy(_REAL_CONFIG)


class Test真实配置解析:
    def test_真实_yaml_全字段解析(self):
        cfg = EditingConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert cfg.exploration_per_round_usd == 400.0
        assert cfg.edits_per_round == 3
        assert cfg.target_duration_s == 120.0
        assert cfg.duration_tolerance_s == 10.0
        assert cfg.shot_limits == {"min_shot_ms": 500, "max_shot_ms": 20000}

    def test_转场规则库读取(self):
        """执行前校验与门禁共用的规则库（配置单一事实源，决策 3）。"""
        cfg = EditingConfig.from_dict(_valid_dict())
        assert cfg.transition_rules["allowed"] == ["cut", "dissolve", "fade"]
        assert cfg.transition_rules["dissolve_max_ms"] == 2000
        assert cfg.transition_rules["forbid_jump_cut_within_scene"] is True

    def test_基准曲线分段读取(self):
        cfg = EditingConfig.from_dict(_valid_dict())
        baseline = cfg.pacing_baseline
        assert baseline["d_cap"] == 4000.0
        assert len(baseline["segments"]) == 3
        first = baseline["segments"][0]
        assert first["span"] == [0.0, 0.15]
        assert first["mean_ms"] == 1200
        assert first["weight"] == 2.0

    def test_渲染价目与编码单线程档(self):
        cfg = EditingConfig.from_dict(_valid_dict())
        assert cfg.render["price_per_second_usd"] == 0.05
        assert cfg.render["fps"] == 8
        assert cfg.render["encode_threads"] == 1  # 单线程确定性档（决策 2）

    def test_judge_锚点集解析为_EDL(self):
        """judge 输入 = EDL 摘要（澄清 Q1）：锚点集解析为 EditDecisionList 供摘要。"""
        cfg = EditingConfig.from_dict(_valid_dict())
        assert len(cfg.judge["prompts"]) == 3
        assert len(cfg.anchor_edls) == 2
        assert all(isinstance(edl, EditDecisionList) for edl in cfg.anchor_edls)
        assert cfg.anchor_edls[0].clips[0].shot_id == "anchor-a1"
        assert cfg.anchor_edls[1].audio[0].track_ref == "anchor-music"

    def test_权重节引用(self):
        cfg = EditingConfig.from_dict(_valid_dict())
        assert cfg.evaluator_weights == {
            "rule.duration_compliance": "gate",
            "rule.shot_distribution": "gate",
            "rule.transition_rules": "gate",
            "proxy.pacing_curve": 0.6,
            "judge.narrative_flow": 0.4,
        }


class Test缺失即报错:
    def test_缺_editing_段(self):
        config = _valid_dict()
        del config["editing"]
        with pytest.raises(EditingConfigError, match="editing"):
            EditingConfig.from_dict(config)

    def test_缺权重节(self):
        config = _valid_dict()
        del config["evaluator_weights"]["editing"]
        with pytest.raises(EditingConfigError, match="evaluator_weights"):
            EditingConfig.from_dict(config)

    @pytest.mark.parametrize(
        "key", ["exploration_per_round_usd", "edits_per_round", "target_duration_s"]
    )
    def test_缺预算与时长字段(self, key):
        config = _valid_dict()
        del config["editing"][key]
        with pytest.raises(EditingConfigError, match=key):
            EditingConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["shot_limits", "transition_rules", "judge"])
    def test_缺结构段(self, key):
        config = _valid_dict()
        del config["editing"][key]
        with pytest.raises(EditingConfigError, match=key):
            EditingConfig.from_dict(config)


class Test基准曲线纪律:
    def test_缺基准曲线即报错(self):
        """缺基准即报错——不允许静默无基准打分（C3 / 原则五）。"""
        config = _valid_dict()
        del config["editing"]["pacing_baseline"]
        with pytest.raises(EditingConfigError, match="pacing_baseline"):
            EditingConfig.from_dict(config)

    def test_基准缺_d_cap_即报错(self):
        config = _valid_dict()
        del config["editing"]["pacing_baseline"]["d_cap"]
        with pytest.raises(EditingConfigError, match="d_cap"):
            EditingConfig.from_dict(config)

    def test_基准空分段即报错(self):
        config = _valid_dict()
        config["editing"]["pacing_baseline"]["segments"] = []
        with pytest.raises(EditingConfigError, match="segments"):
            EditingConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["span", "mean_ms", "var_ms", "weight"])
    def test_分段缺字段即报错(self, key):
        config = _valid_dict()
        del config["editing"]["pacing_baseline"]["segments"][0][key]
        with pytest.raises(EditingConfigError, match=key):
            EditingConfig.from_dict(config)


class Test价目纪律:
    def test_缺_render_段即报错(self):
        config = _valid_dict()
        del config["editing"]["render"]
        with pytest.raises(EditingConfigError, match="render"):
            EditingConfig.from_dict(config)

    def test_缺价目即报错(self):
        """缺价目即报错——不允许静默零成本（原则三/003 同款纪律）。"""
        config = _valid_dict()
        del config["editing"]["render"]["price_per_second_usd"]
        with pytest.raises(EditingConfigError, match="price_per_second_usd"):
            EditingConfig.from_dict(config)

    def test_编码非单线程档拒绝(self):
        """编码线程数 ≠ 1 破坏逐字节复现纪律（SC-002），配置层拒绝。"""
        config = _valid_dict()
        config["editing"]["render"]["encode_threads"] = 4
        with pytest.raises(EditingConfigError, match="encode_threads"):
            EditingConfig.from_dict(config)


class TestJudge纪律:
    def test_提示词为空即报错(self):
        config = _valid_dict()
        config["editing"]["judge"]["prompts"] = []
        with pytest.raises(EditingConfigError, match="prompts"):
            EditingConfig.from_dict(config)

    def test_锚点集为空即报错(self):
        config = _valid_dict()
        config["editing"]["judge"]["anchor_edls"] = []
        with pytest.raises(EditingConfigError, match="anchor_edls"):
            EditingConfig.from_dict(config)

    def test_锚点_EDL_非法即报错(self):
        from core.tree.errors import ValidationError

        config = _valid_dict()
        config["editing"]["judge"]["anchor_edls"][0]["clips"] = []
        with pytest.raises(ValidationError, match="clips"):
            EditingConfig.from_dict(config)
