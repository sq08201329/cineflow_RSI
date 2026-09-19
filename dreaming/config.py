"""做梦形态配置（configs/*.yaml 的 dreaming 段 → DreamConfig）。"""

from dataclasses import dataclass
from pathlib import Path

import yaml


class DreamConfigError(Exception):
    """做梦配置缺失/非法。"""


@dataclass(frozen=True)
class DreamConfig:
    """dreaming 段配置（候选数/摘要窗口/λ/随机预算/塌缩检测/回放并行度）。"""

    candidates_per_round: int
    demo_candidates: int
    recent_k: int
    lambda_: float
    epsilon_random: float
    validation_top_ratio: float
    collapse_window: int
    collapse_threshold: float
    replay_parallelism: int

    @classmethod
    def from_dict(cls, config: dict) -> "DreamConfig":
        dreaming = config.get("dreaming")
        if not isinstance(dreaming, dict):
            raise DreamConfigError("形态配置缺少 dreaming 段")

        def req(key):
            if key not in dreaming:
                raise DreamConfigError(f"dreaming 缺少配置项 {key!r}")
            return dreaming[key]

        return cls(
            candidates_per_round=int(req("candidates_per_round")),
            demo_candidates=int(req("demo_candidates")),
            recent_k=int(req("recent_k")),
            lambda_=float(req("lambda")),
            epsilon_random=float(req("epsilon_random")),
            validation_top_ratio=float(req("validation_top_ratio")),
            collapse_window=int(req("collapse_window")),
            collapse_threshold=float(req("collapse_threshold")),
            replay_parallelism=int(req("replay_parallelism")),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DreamConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
