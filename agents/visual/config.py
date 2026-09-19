"""视觉形态配置（configs/*.yaml 的 visual 段 → VisualConfig）。

配置即形态（宪章原则五）：生成预算、片段规格、帧采样规则、judge 锚点集
与提示词、五评估器权重全部来自配置，缺失即报错。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class VisualConfigError(Exception):
    """视觉配置缺失/非法。"""


def _require(mapping: dict, key: str, where: str):
    value = mapping.get(key)
    if value is None:
        raise VisualConfigError(f"{where} 缺少配置项 {key!r}")
    return value


@dataclass(frozen=True)
class VisualConfig:
    """visual 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    exploration_per_round_usd: float
    clips_per_round: int
    clip_spec: dict
    frame_sampling: dict
    simulated_gen: dict
    judge: dict
    evaluator_weights: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> "VisualConfig":
        visual = config.get("visual")
        if not isinstance(visual, dict):
            raise VisualConfigError("形态配置缺少 visual 段")
        evaluator_weights = config.get("evaluator_weights", {}).get("visual")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise VisualConfigError("形态配置缺少 evaluator_weights.visual 段")
        return cls(
            exploration_per_round_usd=float(
                _require(visual, "exploration_per_round_usd", "visual")
            ),
            clips_per_round=int(_require(visual, "clips_per_round", "visual")),
            clip_spec=dict(_require(visual, "clip_spec", "visual")),
            frame_sampling=dict(_require(visual, "frame_sampling", "visual")),
            simulated_gen=dict(_require(visual, "simulated_gen", "visual")),
            judge=dict(_require(visual, "judge", "visual")),
            evaluator_weights=evaluator_weights,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "VisualConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
