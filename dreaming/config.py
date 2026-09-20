"""做梦形态配置（configs/*.yaml 的 dreaming 段 → DreamConfig）。"""

from dataclasses import dataclass
from pathlib import Path

import yaml


class DreamConfigError(Exception):
    """做梦配置缺失/非法。"""


@dataclass(frozen=True)
class DreamConfig:
    """dreaming 段配置（候选数/摘要窗口/λ/随机预算/塌缩检测/回放并行度/禁自动进化名单）。"""

    candidates_per_round: int
    demo_candidates: int
    recent_k: int
    lambda_: float
    epsilon_random: float
    validation_top_ratio: float
    collapse_window: int
    collapse_threshold: float
    replay_parallelism: int
    # 禁止自动进化的 Agent 名单（宪章原则六；命中即显式拒绝，见 pipeline.run_dream_round）
    no_auto_evolve_agents: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, config: dict) -> "DreamConfig":
        dreaming = config.get("dreaming")
        if not isinstance(dreaming, dict):
            raise DreamConfigError("形态配置缺少 dreaming 段")

        def req(key):
            if key not in dreaming:
                raise DreamConfigError(f"dreaming 缺少配置项 {key!r}")
            return dreaming[key]

        auto_evolve_off = req("no_auto_evolve_agents")
        if not isinstance(auto_evolve_off, (list, tuple)):
            raise DreamConfigError(
                "dreaming.no_auto_evolve_agents 必须为字符串列表"
                "（宪章原则六：禁止自动进化的 Agent 名单）"
            )
        for name in auto_evolve_off:
            if not isinstance(name, str) or not name:
                raise DreamConfigError(
                    "dreaming.no_auto_evolve_agents 必须为非空字符串列表，"
                    f"实际含 {name!r}（宪章原则六名单）"
                )
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
            no_auto_evolve_agents=tuple(auto_evolve_off),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DreamConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
