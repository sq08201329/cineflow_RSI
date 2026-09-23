"""VisualConfig 配置测试：judge 输出预算入形态配置（016 遗留 5，原则五）。

judge 段的提示词与输出预算必须由配置显式给出（缺即报错，不静默取码内默认）——
输出预算决定 judge 实际产出（预算被思维链吃光则正文为空），故属行为口径而非实现细节。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.visual.config import VisualConfig, VisualConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _valid_dict() -> dict:
    """最小合法配置字典（真实 movie.yaml 的深拷贝，逐用例定向破坏）。"""
    return copy.deepcopy(_REAL_CONFIG)


class Test真实配置解析:
    def test_judge_输出预算读取(self):
        cfg = VisualConfig.from_dict(_valid_dict())
        assert cfg.judge["max_tokens"] == 512
        assert len(cfg.judge["prompts"]) == 3


class TestJudge纪律:
    def test_缺_judge_输出预算即报错(self):
        config = _valid_dict()
        del config["visual"]["judge"]["max_tokens"]
        with pytest.raises(VisualConfigError, match="max_tokens"):
            VisualConfig.from_dict(config)

    def test_非正_judge_输出预算即报错(self):
        config = _valid_dict()
        config["visual"]["judge"]["max_tokens"] = 0
        with pytest.raises(VisualConfigError, match="max_tokens"):
            VisualConfig.from_dict(config)

    def test_缺_提示词即报错(self):
        config = _valid_dict()
        config["visual"]["judge"]["prompts"] = []
        with pytest.raises(VisualConfigError, match="prompts"):
            VisualConfig.from_dict(config)
