"""Kendall τ 计算单测（US3 / T130，research 决策 4：手写 τ-b，零新依赖）。

覆盖：τ-b 同分处理（教科书手算案例）、完全一致/完全反转、
样本不足与长度不等 → ValidationError。
"""

import pytest

from core.replay.errors import ValidationError
from core.replay.unbiasedness import kendall_tau


class Test已知答案对照:
    def test_完全一致_tau_为一(self):
        assert kendall_tau([0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]) == pytest.approx(1.0)

    def test_完全反转_tau_为负一(self):
        assert kendall_tau([0.1, 0.2, 0.3], [0.3, 0.2, 0.1]) == pytest.approx(-1.0)

    def test_无关乱序_tau_对照(self):
        # [1,2,3,4] vs [2,1,4,3]：C=4，D=2（两对反转），τ = 2/6 = 1/3
        assert kendall_tau([1, 2, 3, 4], [2, 1, 4, 3]) == pytest.approx(1 / 3)

    def test_tau_b_同分手算案例(self):
        """a=[1,2,2,3], b=[1,2,3,3]：C=4, D=0, ties_a=1, ties_b=1, n0=6
        τ-b = 4 / sqrt((6-1)(6-1)) = 0.8"""
        assert kendall_tau([1, 2, 2, 3], [1, 2, 3, 3]) == pytest.approx(0.8)

    def test_单侧同分_介于零一之间(self):
        # a 有同分、b 无同分：τ = C / sqrt((n0 - n1) * n0)
        # a=[1,1,2], b=[1,2,3]：C=2（对 1-3、2-3），D=0，ties_a=1，n0=3
        # τ = 2 / sqrt(2 * 3) ≈ 0.8165
        assert kendall_tau([1, 1, 2], [1, 2, 3]) == pytest.approx(2 / (2 * 3) ** 0.5)


class Test退化与校验:
    def test_样本不足拒绝(self):
        with pytest.raises(ValidationError, match="样本不足"):
            kendall_tau([0.5], [0.5])

    def test_空序列拒绝(self):
        with pytest.raises(ValidationError, match="样本不足"):
            kendall_tau([], [])

    def test_长度不等拒绝(self):
        with pytest.raises(ValidationError, match="等长"):
            kendall_tau([0.1, 0.2], [0.1])

    def test_双方全同分_完全一致视为一(self):
        """退化序列：分母为零时，逐元素一致 → 1.0，否则 → 0.0（确定性规则）。"""
        assert kendall_tau([0.5, 0.5], [0.5, 0.5]) == 1.0
        assert kendall_tau([0.5, 0.5], [0.3, 0.3]) == 0.0
