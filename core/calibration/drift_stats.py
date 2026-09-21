"""漂移统计原语（纯函数，功能 012 / T1108；口径见 research 决策 2）。

- `psi()`：Population Stability Index（分桶分布距离，**主指标**）。口径：两侧先归一为
  占比（计数或占比输入皆可），零桶以 epsilon 平滑后**重新归一**——既避免 `ln(0)` 爆掉，
  又保证"同分布严格为 0"。经验阈值（< 0.1 稳定 / 0.1~0.2 关注 / > 0.2 显著）由配置
  提供，不在本模块硬编码（原则五）；
- `quantile_shifts()`：p25/p50/p75/p90 位移向量（**辅指标**），逐分位点相减（精度 < 1e-6）；
  基线侧为滑动窗口合并分布的分位数（样本量加权，见 drift_metrics）；
- `merge_bucket_counts()` / `bucket_proportions()`：滑动窗口基线的分桶合并（逐桶相加 →
  占比），窗口内周期数不足或快照缺口由调用方如实标注（不插值）；
- `max_abs_quantile_shift()`：位移向量 → 标量（判定用）。

全部为纯函数（无 IO、无状态、无随机），是口径版本化（同一输入同一结果）的前提。
"""

import math
from collections.abc import Iterable, Mapping, Sequence

from core.calibration.drift_models import QUANTILE_NAMES
from core.evaluators.errors import ValidationError

DEFAULT_EPSILON = 1e-6


def _require_counts(name: str, values: object) -> tuple[float, ...]:
    """校验分桶计数/占比序列：非空、有限、非负。"""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise ValidationError(f"{name} 必须为非空分桶序列，实际为 {values!r}")
    out = []
    for value in values:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"{name} 的元素必须为数值，实际为 {value!r}")
        if not math.isfinite(value) or value < 0:
            raise ValidationError(f"{name} 的元素必须为 ≥ 0 的有限数值，实际为 {value!r}")
        out.append(float(value))
    return tuple(out)


def _normalized(values: Sequence[float], epsilon: float, *, name: str) -> tuple[float, ...]:
    """占比归一 + 零桶 epsilon 平滑 + 重新归一（保证同分布严格为 0）。"""
    total = sum(values)
    if total <= 0:
        raise ValidationError(f"{name} 总量必须 > 0（空分布无从比较）")
    shares = [max(value / total, epsilon) for value in values]
    smoothed_total = sum(shares)
    return tuple(share / smoothed_total for share in shares)


def merge_bucket_counts(arrays: Iterable[Sequence[float]]) -> tuple[float, ...]:
    """逐桶相加（滑动窗口内各周期快照的合并口径；周期顺序不影响结果）。"""
    merged: tuple[float, ...] | None = None
    for index, counts in enumerate(arrays):
        current = _require_counts(f"counts[{index}]", counts)
        if merged is None:
            merged = current
        elif len(current) != len(merged):
            raise ValidationError(
                f"counts[{index}] 的分桶数与首项不一致（{len(current)} != {len(merged)}）"
            )
        else:
            merged = tuple(left + right for left, right in zip(merged, current, strict=True))
    if merged is None:
        raise ValidationError("counts 必须包含至少一个分桶序列")
    return merged


def bucket_proportions(counts: Sequence[float]) -> tuple[float, ...]:
    """分桶计数 → 占比（总量为 0 的空窗口返回全 0，不做除零）。"""
    values = _require_counts("counts", counts)
    total = sum(values)
    if total <= 0:
        return tuple(0.0 for _ in values)
    return tuple(value / total for value in values)


def psi(
    expected: Sequence[float], actual: Sequence[float], *, epsilon: float = DEFAULT_EPSILON
) -> float:
    """分布距离 PSI（主指标）：`Σ (aᵢ − eᵢ)·ln(aᵢ / eᵢ)`。

    - expected = 基线（滑动窗口合并）分布；actual = 当前周期分布；计数/占比皆可；
    - 零桶（样本稀疏的常态）经 epsilon 平滑 + 重新归一，不产生 `ln(0)` 与 Inf；
    - 同分布 → 0（严格）；PSI 恒 ≥ 0，越大越远。
    """
    if not isinstance(epsilon, (int, float)) or isinstance(epsilon, bool) or epsilon <= 0:
        raise ValidationError(f"epsilon 必须为 > 0 的数值，实际为 {epsilon!r}")
    expected_values = _require_counts("预期分布（基线）", expected)
    actual_values = _require_counts("实际分布（当前周期）", actual)
    if len(expected_values) != len(actual_values):
        raise ValidationError(
            f"两侧分桶长度必须一致，实际为 {len(expected_values)} != {len(actual_values)}"
        )
    shares_expected = _normalized(expected_values, float(epsilon), name="预期分布（基线）")
    shares_actual = _normalized(actual_values, float(epsilon), name="实际分布（当前周期）")
    return sum(
        (actual_share - expected_share) * math.log(actual_share / expected_share)
        for expected_share, actual_share in zip(shares_expected, shares_actual, strict=True)
    )


def _require_quantiles(name: str, quantiles: object) -> dict[str, float]:
    if not isinstance(quantiles, Mapping) or set(quantiles) != set(QUANTILE_NAMES):
        raise ValidationError(
            f"{name} 必须为恰好包含 {list(QUANTILE_NAMES)} 的映射，实际为 {quantiles!r}"
        )
    out: dict[str, float] = {}
    for key in QUANTILE_NAMES:
        value = quantiles[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"{name}.{key} 必须为数值，实际为 {value!r}")
        out[key] = float(value)
    return out


def quantile_shifts(
    baseline: Mapping[str, float], current: Mapping[str, float]
) -> dict[str, float]:
    """分位数位移向量（辅指标）：current − baseline，逐分位点（精度 < 1e-6）。"""
    base = _require_quantiles("quantiles（基线）", baseline)
    now = _require_quantiles("quantiles（当前周期）", current)
    return {name: now[name] - base[name] for name in QUANTILE_NAMES}


def max_abs_quantile_shift(shifts: Mapping[str, float]) -> float:
    """位移向量 → 最大绝对位移（判定口径：> quantile_threshold 判漂移）。"""
    if not isinstance(shifts, Mapping) or not shifts:
        raise ValidationError(f"shifts 必须为非空位移映射，实际为 {shifts!r}")
    magnitudes = []
    for name, value in shifts.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"shifts[{name!r}] 必须为数值，实际为 {value!r}")
        magnitudes.append(abs(float(value)))
    return max(magnitudes)
