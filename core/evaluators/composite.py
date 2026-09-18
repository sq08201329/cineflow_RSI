"""合成评分（契约 §3，FR-010/FR-011）。

- 硬规则门禁：任一键以 rule. 开头且 score == 0.0 → 总分 0.0（不可行解，无视其他得分）；
- 加权求和：sum(weights[k] * res.score)，键集合不一致 → WeightMismatchError；
- 权重由调用方从形态配置注入，本函数禁止自行读取配置或硬编码权重（宪章原则五）；
- 不做隐式归一化：Σweights 由配置作者保证（口径一致性优先）。
"""

from core.evaluators.base import EvalResult
from core.evaluators.errors import WeightMismatchError

_GATE_PREFIX = "rule."


def composite_score(breakdown: dict[str, EvalResult], weights: dict[str, float]) -> float:
    """把逐评估器结果按权重合成为节点总分。"""
    only_in_breakdown = set(breakdown) - set(weights)
    only_in_weights = set(weights) - set(breakdown)
    if only_in_breakdown or only_in_weights:
        parts = []
        if only_in_breakdown:
            parts.append(f"仅出现在 breakdown：{sorted(only_in_breakdown)}")
        if only_in_weights:
            parts.append(f"仅出现在 weights：{sorted(only_in_weights)}")
        raise WeightMismatchError(
            "breakdown 与 weights 键集合不一致（拒绝静默按部分权重计算）：" + "；".join(parts)
        )

    # 硬规则门禁优先于加权求和：不可行解直接 0 分
    for key, result in breakdown.items():
        if key.startswith(_GATE_PREFIX) and result.score == 0.0:
            return 0.0

    return sum(weights[key] * result.score for key, result in breakdown.items())
