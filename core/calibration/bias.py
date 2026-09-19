"""偏差与相关性计算（功能 010 US2，契约 C5）。

- 连续分量：mean_shift = mean(anchor − auto) + Pearson r（自实现，零新依赖）；
- judge 类：Kendall τ 序一致（复用 core/replay/unbiasedness.py 的 τ-b 实现，
  research 决策 3——禁止胜率与分数直接相减，judge 口径不产 mean_shift）；
- 样本 < min_samples → 只备注"样本不足"，不产偏差值（FR-004）；
- 负相关（r 或 τ < 0）在 note 标记，供报告 alerts 与提案禁止消费。
"""

import math

from core.calibration.models import BiasRecord, PairingRecord
from core.evaluators.errors import ValidationError
from core.replay.unbiasedness import kendall_tau

NOTE_INSUFFICIENT = "样本不足"
NOTE_NEGATIVE = "负相关"


def pearson_r(a: list[float], b: list[float]) -> float | None:
    """Pearson 相关系数；任一侧无方差（分母为零）→ None。"""
    if len(a) != len(b):
        raise ValidationError(f"两序列必须等长，实际为 {len(a)} 与 {len(b)}")
    n = len(a)
    if n < 2:
        raise ValidationError(f"样本不足：序列长度 {n} < 2")
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    denominator = math.sqrt(var_a * var_b)
    if denominator == 0:
        return None
    # 浮点尾数钳制到 [-1,1]（BiasRecord 域校验要求）
    return max(-1.0, min(1.0, cov / denominator))


def compute_bias(
    pairs: list[PairingRecord],
    *,
    evaluator_key: str,
    period: str,
    min_samples: int,
    judge: bool = False,
) -> BiasRecord:
    """单评估器单周期偏差：剔除/缺分量记录不计样本。

    judge=True 走 Kendall τ（锚点排名 × judge 胜率排名）；否则走
    mean_shift + Pearson r。负相关在 note 产出标记。
    """
    usable = [p for p in pairs if not p.excluded and p.auto_score is not None]
    samples = len(usable)
    if samples < min_samples:
        return BiasRecord(
            evaluator_key=evaluator_key,
            period=period,
            samples=samples,
            note=f"{NOTE_INSUFFICIENT}：有效配对 {samples} < min_samples={min_samples}",
        )

    anchors = [p.anchor_score for p in usable]
    autos = [p.auto_score for p in usable]
    if judge:
        tau = kendall_tau(anchors, autos)
        note = NOTE_NEGATIVE if tau < 0 else ""
        return BiasRecord(
            evaluator_key=evaluator_key,
            period=period,
            samples=samples,
            kendall_tau=tau,
            note=note,
        )

    mean_shift = sum(a - s for a, s in zip(anchors, autos, strict=True)) / samples
    r = pearson_r(anchors, autos)
    notes = []
    if r is None:
        notes.append("得分无方差，Pearson r 不产")
    elif r < 0:
        notes.append(NOTE_NEGATIVE)
    return BiasRecord(
        evaluator_key=evaluator_key,
        period=period,
        samples=samples,
        mean_shift=mean_shift,
        pearson_r=r,
        note="；".join(notes),
    )
