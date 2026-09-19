"""得分定点归一（research 决策 6，FR-012）。

所有评估器得分入库前必须经过 quantize_score：保留 6 位小数
（Python round 即 round-half-even），消除浮点路径末位差异，
使"重算逐字节一致"（SC-001）在工程上可达成。业务无关纯函数。
"""

from core.evaluators.errors import ValidationError

SCORE_DECIMALS = 6


def quantize_score(x: float) -> float:
    """把得分定点归一到 6 位小数；越界/非数值/NaN/Inf 一律拒绝。"""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise ValidationError(f"score 必须为数值，实际为 {type(x).__name__}")
    value = float(x)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValidationError(f"score 非法：{x!r}（NaN/Inf 拒绝）")
    if not 0.0 <= value <= 1.0:
        raise ValidationError(f"score 必须 ∈ [0,1]，实际为 {x!r}")
    return round(value, SCORE_DECIMALS)
