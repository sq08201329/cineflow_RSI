"""宣发形态配置（configs/*.yaml 的 promo 段 → PromoConfig）。

配置即形态（宪章原则五）：预算门禁、物料规格、敏感词库、价目表、
CTR 先验、指标权重、模拟平台分布全部来自配置，缺失即报错（不放行）。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class PromoConfigError(Exception):
    """宣发配置缺失/非法（缺配置拒投不放行，宪章边界语义）。"""


def _require(mapping: dict, key: str, where: str):
    value = mapping.get(key)
    if value is None:
        raise PromoConfigError(f"{where} 缺少配置项 {key!r}")
    return value


@dataclass(frozen=True)
class PromoConfig:
    """promo 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    exploration_per_round_usd: float
    promo_pilot_ratio: float
    default_model: str
    materials_per_round: int
    material_spec: dict
    sensitive_words: list[str]
    ctr_prior: dict
    ctr_cap: float
    metric_weights: dict
    model_prices: dict
    simulated_platform: dict
    evaluator_weights: dict = field(default_factory=dict)  # evaluator_weights.promo 段

    @classmethod
    def from_dict(cls, config: dict) -> "PromoConfig":
        """从完整形态配置 dict 构造；缺 promo 段或关键项即 PromoConfigError。"""
        promo = config.get("promo")
        if not isinstance(promo, dict):
            raise PromoConfigError("形态配置缺少 promo 段")
        evaluator_weights = config.get("evaluator_weights", {}).get("promo")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise PromoConfigError("形态配置缺少 evaluator_weights.promo 段")
        return cls(
            exploration_per_round_usd=float(_require(promo, "exploration_per_round_usd", "promo")),
            promo_pilot_ratio=float(_require(promo, "promo_pilot_ratio", "promo")),
            default_model=str(_require(promo, "default_model", "promo")),
            materials_per_round=int(_require(promo, "materials_per_round", "promo")),
            material_spec=dict(_require(promo, "material_spec", "promo")),
            sensitive_words=list(_require(promo, "sensitive_words", "promo")),
            ctr_prior=dict(_require(promo, "ctr_prior", "promo")),
            ctr_cap=float(_require(promo, "ctr_cap", "promo")),
            metric_weights=dict(_require(promo, "metric_weights", "promo")),
            model_prices=dict(_require(promo, "model_prices", "promo")),
            simulated_platform=dict(_require(promo, "simulated_platform", "promo")),
            evaluator_weights=evaluator_weights,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PromoConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    @property
    def budget_cap_usd(self) -> float:
        """单轮投放预算上限 = 总预算 × 试点比例（FR-002）。"""
        return self.exploration_per_round_usd * self.promo_pilot_ratio
