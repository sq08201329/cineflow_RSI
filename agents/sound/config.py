"""声音形态配置（configs/*.yaml 的 sound 段 → SoundConfig）。

配置即形态（宪章原则五）：生成预算、响度分档、同步阈值、采样率、三类型价目表
与模拟生成参数、四评估器权重全部来自配置，缺失即报错；缺价目即报错——
不允许静默零成本（003 同款纪律，原则三）。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 三类型适配器与成本分账粒度（澄清 Q2）；响度分档三档（rule.loudness_compliance 对照）
_GEN_TYPES = ("tts", "sfx", "music")
_LOUDNESS_TIERS = ("dialogue", "sfx", "music")


class SoundConfigError(Exception):
    """声音配置缺失/非法。"""


def _require(mapping: dict, key: str, where: str):
    value = mapping.get(key)
    if value is None:
        raise SoundConfigError(f"{where} 缺少配置项 {key!r}")
    return value


def _require_price_table(prices: dict) -> dict:
    """三类型价目表：缺任一类型或空子表即报错（不允许静默零成本）。"""
    for gen_type in _GEN_TYPES:
        table = prices.get(gen_type)
        if not isinstance(table, dict) or not table:
            raise SoundConfigError(
                f"sound.prices 缺少类型 {gen_type!r} 的价目（缺价目即报错，不允许静默零成本）"
            )
    return {k: dict(v) for k, v in prices.items()}


def _require_loudness_tiers(loudness: dict) -> dict:
    """响度分档三档齐全，各档 target_lufs/tolerance 齐全。"""
    for tier in _LOUDNESS_TIERS:
        band = loudness.get(tier)
        if not isinstance(band, dict):
            raise SoundConfigError(f"sound.loudness 缺少分档 {tier!r}")
        _require(band, "target_lufs", f"sound.loudness.{tier}")
        _require(band, "tolerance", f"sound.loudness.{tier}")
    return {k: dict(v) for k, v in loudness.items()}


@dataclass(frozen=True)
class SoundConfig:
    """sound 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    exploration_per_round_usd: float
    clips_per_round: int
    loudness: dict
    av_sync_threshold_ms: int
    sample_rate: int
    prices: dict
    simulated_gen: dict
    evaluator_weights: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> "SoundConfig":
        sound = config.get("sound")
        if not isinstance(sound, dict):
            raise SoundConfigError("形态配置缺少 sound 段")
        evaluator_weights = config.get("evaluator_weights", {}).get("sound")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise SoundConfigError("形态配置缺少 evaluator_weights.sound 段")
        return cls(
            exploration_per_round_usd=float(
                _require(sound, "exploration_per_round_usd", "sound")
            ),
            clips_per_round=int(_require(sound, "clips_per_round", "sound")),
            loudness=_require_loudness_tiers(_require(sound, "loudness", "sound")),
            av_sync_threshold_ms=int(_require(sound, "av_sync_threshold_ms", "sound")),
            sample_rate=int(_require(sound, "sample_rate", "sound")),
            prices=_require_price_table(_require(sound, "prices", "sound")),
            simulated_gen=dict(_require(sound, "simulated_gen", "sound")),
            evaluator_weights=evaluator_weights,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SoundConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
