"""漂移统计原语单测（功能 012 / T1107，先于实现编写；research 决策 2）。

- psi()：同分布 ≈ 0；均值平移 / 方差展宽 / 双峰化三形态显著超阈（> 0.2）；
  零桶 epsilon 平滑不炸（无 ln(0)、结果有限）；空分布/长度不一致显式报错；
- quantile_shifts()：p25/p50/p75/p90 位移向量，精度 < 1e-6；
- 分桶合并与占比（滑动窗口基线的纯函数部分）。
"""

import math

import pytest

from core.calibration.drift_stats import (
    bucket_proportions,
    max_abs_quantile_shift,
    merge_bucket_counts,
    psi,
    quantile_shifts,
)
from core.evaluators.errors import ValidationError

# 基线型窄峰分布（bucket 3~6 集中）；三形态漂移各只改一处形状
_BASELINE = [0, 0, 0, 3, 9, 10, 3, 0, 0, 0]
_STABLE = [0, 0, 0, 3, 10, 9, 3, 0, 0, 0]
_MEAN_SHIFT = [0, 0, 0, 0, 0, 3, 9, 10, 3, 0]
_VARIANCE_WIDEN = [0, 0, 2, 5, 5, 6, 5, 2, 0, 0]
_BIMODAL = [0, 13, 0, 0, 0, 0, 0, 0, 12, 0]

_QUANTILES = {"p25": 0.42, "p50": 0.50, "p75": 0.58, "p90": 0.64}
_SHIFTED_QUANTILES = {"p25": 0.62, "p50": 0.70, "p75": 0.78, "p90": 0.84}
_MIXED_QUANTILES = {"p25": 0.40, "p50": 0.50, "p75": 0.62, "p90": 0.60}


class TestPSI:
    def test_同分布为零(self):
        assert psi(_BASELINE, list(_BASELINE)) == pytest.approx(0.0, abs=1e-12)
        assert psi(_BASELINE, _STABLE) < 0.05  # 稳定抖动远低于阈值 0.2

    def test_三形态漂移超阈(self):
        assert psi(_BASELINE, _MEAN_SHIFT) > 0.2  # 均值平移
        assert psi(_BASELINE, _VARIANCE_WIDEN) > 0.2  # 方差展宽
        assert psi(_BASELINE, _BIMODAL) > 0.2  # 双峰化

    def test_计数与占比输入同结果(self):
        proportions = bucket_proportions(_BASELINE)
        assert psi(proportions, _MEAN_SHIFT) == pytest.approx(psi(_BASELINE, _MEAN_SHIFT))

    def test_非负且对称口径一致(self):
        assert psi(_BASELINE, _MEAN_SHIFT) >= 0
        # 两侧互换仍非负（PSI 本身不对称，但恒 ≥ 0）
        assert psi(_MEAN_SHIFT, _BASELINE) >= 0

    def test_零桶_epsilon_平滑不炸(self):
        value = psi([1, 0, 0], [0, 0, 1], epsilon=1e-9)
        assert math.isfinite(value)
        assert value > 0

    def test_双侧零桶不为零(self):
        # 两侧都空的桶经 epsilon 平滑后重新归一，仍不产生 ln(0)
        assert psi([0, 5, 0], [0, 0, 5]) > 0

    def test_epsilon_必须为正(self):
        with pytest.raises(ValidationError, match="epsilon"):
            psi(_BASELINE, _STABLE, epsilon=0)

    def test_空分布报错(self):
        with pytest.raises(ValidationError, match="预期分布"):
            psi([], _STABLE)
        with pytest.raises(ValidationError, match="实际分布"):
            psi(_BASELINE, [])
        with pytest.raises(ValidationError, match="预期分布"):
            psi([0, 0, 0], [0, 0, 0])  # 总量为 0：无从归一

    def test_长度不一致报错(self):
        with pytest.raises(ValidationError, match="长度"):
            psi(_BASELINE, _STABLE[:5])

    def test_负计数报错(self):
        with pytest.raises(ValidationError, match="预期分布"):
            psi([1, -1], [1, 1])


class TestQuantileShifts:
    def test_位移向量精度(self):
        shifts = quantile_shifts(_QUANTILES, _SHIFTED_QUANTILES)
        assert list(shifts) == ["p25", "p50", "p75", "p90"]
        for name, expected in (
            ("p25", 0.2),
            ("p50", 0.2),
            ("p75", 0.2),
            ("p90", 0.2),
        ):
            assert abs(shifts[name] - expected) < 1e-6

    def test_负位移与混合方向(self):
        shifts = quantile_shifts(_QUANTILES, _MIXED_QUANTILES)
        assert shifts["p25"] == pytest.approx(-0.02)
        assert shifts["p75"] == pytest.approx(0.04)
        assert shifts["p90"] == pytest.approx(-0.04)

    def test_缺分位点报错(self):
        for bad in ({"p25": 0.4}, {**_QUANTILES, "p99": 0.9}):
            with pytest.raises(ValidationError, match="quantiles"):
                quantile_shifts(bad, _QUANTILES)

    def test_非数值报错(self):
        with pytest.raises(ValidationError, match="quantiles"):
            quantile_shifts(_QUANTILES, {**_QUANTILES, "p50": "mid"})

    def test_最大绝对位移(self):
        shifts = quantile_shifts(_QUANTILES, _MIXED_QUANTILES)
        assert max_abs_quantile_shift(shifts) == pytest.approx(0.04)
        with pytest.raises(ValidationError, match="shifts"):
            max_abs_quantile_shift({})


class Test分桶合并:
    def test_逐桶相加(self):
        assert merge_bucket_counts(([1, 2], [3, 4])) == (4, 6)
        assert merge_bucket_counts(([1, 2], [3, 4], [1, 1])) == (5, 7)

    def test_并入与占比(self):
        assert bucket_proportions([2, 2]) == pytest.approx((0.5, 0.5))
        assert sum(bucket_proportions(_BASELINE)) == pytest.approx(1.0)
        assert bucket_proportions([0, 0, 0]) == (0.0, 0.0, 0.0)  # 空窗口不炸（不除零）

    def test_空输入与非法值报错(self):
        with pytest.raises(ValidationError, match="counts"):
            merge_bucket_counts(())
        with pytest.raises(ValidationError, match="counts"):
            merge_bucket_counts(([],))
        with pytest.raises(ValidationError, match="counts"):
            merge_bucket_counts(([1, 2], [3]))
        with pytest.raises(ValidationError, match="counts"):
            bucket_proportions([1, "2"])
