"""定点归一单测（T305，research 决策 6）。

quantize_score：6 位小数、round-half-even、边界、幂等、越界拒绝。
"""

import pytest

from core.evaluators.errors import ValidationError
from core.evaluators.quantize import quantize_score


class Test定点归一:
    def test_六位小数(self):
        assert quantize_score(0.123456789) == 0.123457
        assert quantize_score(1 / 3) == 0.333333

    def test_round_half_even(self):
        # Python round 即银行家舍入：0.1234565 → 0.123456（偶数侧）
        assert quantize_score(0.1234565) == round(0.1234565, 6)

    def test_边界值(self):
        assert quantize_score(0.0) == 0.0
        assert quantize_score(1.0) == 1.0

    def test_幂等(self):
        once = quantize_score(0.123456789123)
        assert quantize_score(once) == once

    def test_整数输入(self):
        assert quantize_score(1) == 1.0

    @pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"])
    def test_越界与非法类型拒绝(self, bad):
        with pytest.raises(ValidationError):
            quantize_score(bad)
