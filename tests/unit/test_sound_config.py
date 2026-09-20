"""SoundConfig 配置测试（功能 006 / T609，先于实现编写）。

C2 落点：sound 段解析（预算/响度分档/同步阈值/采样率/价目/模拟参数）；
缺价目即报错（不允许静默零成本，003 同款纪律）；evaluator_weights.sound 权重节引用。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.sound.config import SoundConfig, SoundConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load(
    (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8")
)


def _valid_dict() -> dict:
    """最小合法配置字典（真实 movie.yaml 的深拷贝，逐用例定向破坏）。"""
    return copy.deepcopy(_REAL_CONFIG)


class Test真实配置解析:
    def test_真实_yaml_全字段解析(self):
        cfg = SoundConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert cfg.exploration_per_round_usd == 300.0
        assert cfg.clips_per_round == 4
        assert cfg.av_sync_threshold_ms == 120
        assert cfg.sample_rate == 16000
        assert set(cfg.prices) == {"tts", "sfx", "music"}
        assert set(cfg.simulated_gen) >= {"base_freq_hz", "harmonics", "duration_seconds"}

    def test_响度分档读取(self):
        cfg = SoundConfig.from_dict(_valid_dict())
        assert set(cfg.loudness) == {"dialogue", "sfx", "music"}
        assert cfg.loudness["dialogue"] == {"target_lufs": -27.0, "tolerance": 2.0}
        assert cfg.loudness["sfx"] == {"target_lufs": -30.0, "tolerance": 3.0}
        assert cfg.loudness["music"] == {"target_lufs": -25.0, "tolerance": 3.0}

    def test_权重节引用(self):
        cfg = SoundConfig.from_dict(_valid_dict())
        assert cfg.evaluator_weights == {
            "rule.loudness_compliance": "gate",
            "rule.av_sync": "gate",
            "proxy.asr_transcript": 0.5,
            "proxy.emotion_music_match": 0.5,
        }

    def test_三类型价目读取(self):
        cfg = SoundConfig.from_dict(_valid_dict())
        assert cfg.prices["tts"] == {"per_second_usd": 0.02}
        assert cfg.prices["sfx"] == {"per_event_usd": 0.05}
        assert cfg.prices["music"] == {"per_second_usd": 0.03}


class Test缺失即报错:
    def test_缺_sound_段(self):
        config = _valid_dict()
        del config["sound"]
        with pytest.raises(SoundConfigError, match="sound"):
            SoundConfig.from_dict(config)

    def test_缺权重节(self):
        config = _valid_dict()
        del config["evaluator_weights"]["sound"]
        with pytest.raises(SoundConfigError, match="evaluator_weights"):
            SoundConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["exploration_per_round_usd", "clips_per_round"])
    def test_缺预算字段(self, key):
        config = _valid_dict()
        del config["sound"][key]
        with pytest.raises(SoundConfigError, match=key):
            SoundConfig.from_dict(config)

    @pytest.mark.parametrize("key", ["av_sync_threshold_ms", "sample_rate"])
    def test_缺阈值与采样率(self, key):
        config = _valid_dict()
        del config["sound"][key]
        with pytest.raises(SoundConfigError, match=key):
            SoundConfig.from_dict(config)

    def test_缺模拟生成参数(self):
        config = _valid_dict()
        del config["sound"]["simulated_gen"]
        with pytest.raises(SoundConfigError, match="simulated_gen"):
            SoundConfig.from_dict(config)


class Test价目纪律:
    def test_缺_prices_整段即报错(self):
        """缺价目即报错——不允许静默零成本（原则三/003 同款纪律）。"""
        config = _valid_dict()
        del config["sound"]["prices"]
        with pytest.raises(SoundConfigError, match="prices"):
            SoundConfig.from_dict(config)

    @pytest.mark.parametrize("gen_type", ["tts", "sfx", "music"])
    def test_缺任一类型价目即报错(self, gen_type):
        config = _valid_dict()
        del config["sound"]["prices"][gen_type]
        with pytest.raises(SoundConfigError, match=gen_type):
            SoundConfig.from_dict(config)

    def test_空价目子表即报错(self):
        config = _valid_dict()
        config["sound"]["prices"]["sfx"] = {}
        with pytest.raises(SoundConfigError, match="sfx"):
            SoundConfig.from_dict(config)


class Test响度分档纪律:
    @pytest.mark.parametrize("tier", ["dialogue", "sfx", "music"])
    def test_缺任一分档即报错(self, tier):
        config = _valid_dict()
        del config["sound"]["loudness"][tier]
        with pytest.raises(SoundConfigError, match=tier):
            SoundConfig.from_dict(config)

    def test_分档缺容差即报错(self):
        config = _valid_dict()
        del config["sound"]["loudness"]["dialogue"]["tolerance"]
        with pytest.raises(SoundConfigError, match="tolerance"):
            SoundConfig.from_dict(config)

    def test_分档缺目标响度即报错(self):
        config = _valid_dict()
        del config["sound"]["loudness"]["music"]["target_lufs"]
        with pytest.raises(SoundConfigError, match="target_lufs"):
            SoundConfig.from_dict(config)
