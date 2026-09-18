"""合成评分单测（US3 / T026）。

契约（contracts/evaluator-registry.md §3）全行覆盖：
硬规则门禁（任一 rule.* 为 0 → 总分 0）、加权求和、键不匹配 WeightMismatchError、
不做隐式归一化、权重由调用方注入。
"""

import pytest
from core.evaluators.composite import composite_score

from core.evaluators.base import EvalResult
from core.evaluators.errors import WeightMismatchError


def _breakdown(**scores: float) -> dict[str, EvalResult]:
    return {key: EvalResult(score=score) for key, score in scores.items()}


class Test硬规则门禁:
    def test_rule_零分_总分为零无视其他高分(self):
        breakdown = _breakdown(**{"rule.gate": 0.0, "proxy.a": 1.0, "proxy.b": 0.9})
        weights = {"rule.gate": 0.0, "proxy.a": 0.5, "proxy.b": 0.5}
        assert composite_score(breakdown, weights) == 0.0

    def test_带版本号的_rule_键同样触发门禁(self):
        breakdown = _breakdown(**{"rule.gate@1.0.0": 0.0, "proxy.a@2.0.0": 1.0})
        weights = {"rule.gate@1.0.0": 0.0, "proxy.a@2.0.0": 1.0}
        assert composite_score(breakdown, weights) == 0.0

    def test_rule_非零不触发门禁(self):
        breakdown = _breakdown(**{"rule.gate": 1.0, "proxy.a": 0.4})
        weights = {"rule.gate": 0.0, "proxy.a": 1.0}
        assert composite_score(breakdown, weights) == pytest.approx(0.4)


class Test加权求和:
    def test_加权求和正确(self):
        breakdown = _breakdown(**{"proxy.a": 0.8, "proxy.b": 0.6})
        weights = {"proxy.a": 0.5, "proxy.b": 0.5}
        assert composite_score(breakdown, weights) == pytest.approx(0.7)

    def test_不做隐式归一化(self):
        """Σweights ≠ 1 时按配置语义直接加权，不归一化（口径一致性优先）。"""
        breakdown = _breakdown(**{"proxy.a": 0.4, "proxy.b": 0.4})
        weights = {"proxy.a": 1.0, "proxy.b": 1.0}  # Σ=2
        assert composite_score(breakdown, weights) == pytest.approx(0.8)

    def test_空_breakdown_与空权重_得零(self):
        assert composite_score({}, {}) == 0.0


class Test键不匹配:
    def test_weights_多出键报_WeightMismatchError(self):
        breakdown = _breakdown(**{"proxy.a": 0.8})
        weights = {"proxy.a": 0.5, "proxy.ghost": 0.5}
        with pytest.raises(WeightMismatchError, match="proxy.ghost"):
            composite_score(breakdown, weights)

    def test_breakdown_多出键报_WeightMismatchError(self):
        breakdown = _breakdown(**{"proxy.a": 0.8, "proxy.extra": 0.1})
        weights = {"proxy.a": 1.0}
        with pytest.raises(WeightMismatchError, match="proxy.extra"):
            composite_score(breakdown, weights)

    def test_键不匹配优先于门禁短路(self):
        """键集合不一致是校验失败，不得因 rule 零分静默产出 0 分。"""
        breakdown = _breakdown(**{"rule.gate": 0.0, "proxy.extra": 0.5})
        weights = {"rule.gate": 0.0}
        with pytest.raises(WeightMismatchError):
            composite_score(breakdown, weights)
