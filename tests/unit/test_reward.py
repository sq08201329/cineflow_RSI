"""奖励函数单测（T403，research 决策 1 权威口径）。

pareto_auc 梯形归一化：auc = Σ(s[i]+s[i+1])/2 / (n−1)，n<2 边界
（单点取首值、空曲线取 0）；parallel_penalty = 串行轮 / max(probes, 1)；
λ 注入；RewardBreakdown 校验。
"""

import pytest

from dreaming.reward import RewardBreakdown, compute_reward, parallel_penalty, pareto_auc
from tests.conftest import *  # noqa: F401,F403 - 夹具注册


class TestParetoAuc梯形归一化:
    def test_梯形面积归一化(self):
        # 曲线 [0.0, 0.5, 1.0]：梯形面积 = (0+0.5)/2 + (0.5+1)/2 = 1.0；
        # 满分红线面积 = 1.0 × (3−1) = 2.0 → auc = 0.5
        assert pareto_auc([0.0, 0.5, 1.0]) == pytest.approx(0.5)

    def test_全程满分(self):
        assert pareto_auc([1.0, 1.0, 1.0]) == pytest.approx(1.0)

    def test_早收敛奖励(self):
        """更快达到高分的策略 auc 更高（探索效率目标）。"""
        fast = pareto_auc([0.9, 0.9, 0.9])
        slow = pareto_auc([0.1, 0.5, 0.9])
        assert fast > slow

    def test_曲线首点计入(self):
        # 首点为初始观测：curve [0.4, 0.4] → auc = 0.4（梯形 = 0.4，红线 = 1.0）
        assert pareto_auc([0.4, 0.4]) == pytest.approx(0.4)


class Test边界:
    def test_单点取首值(self):
        assert pareto_auc([0.7]) == pytest.approx(0.7)

    def test_空曲线取零(self):
        assert pareto_auc([]) == 0.0


class Test并行惩罚:
    def test_串行轮除以probe数(self):
        assert parallel_penalty(4.0, 8) == pytest.approx(0.5)

    def test_零probe除一(self):
        assert parallel_penalty(4.0, 0) == pytest.approx(4.0)


class TestRewardBreakdown:
    def test_合成与校验(self, make_trajectory):
        breakdown = compute_reward(
            make_trajectory(
                best_score_curve=[0.0, 0.5, 1.0],
                probe_count=4,
                effective_sequential_rounds=2.0,
            ),
            lambda_=0.5,
        )
        assert breakdown.pareto_auc == pytest.approx(0.5)
        assert breakdown.parallel_penalty == pytest.approx(0.5)
        assert breakdown.lambda_ == 0.5
        assert breakdown.reward == pytest.approx(0.5 - 0.5 * 0.5)

    def test_lambda_注入生效(self, make_trajectory):
        trajectory = make_trajectory(
            best_score_curve=[0.5, 0.5], probe_count=2, effective_sequential_rounds=2.0
        )
        assert (
            compute_reward(trajectory, lambda_=0.0).reward
            > compute_reward(trajectory, lambda_=1.0).reward
        )

    def test_非法值拒构造(self):
        with pytest.raises(Exception, match="pareto_auc"):
            RewardBreakdown(pareto_auc=1.5, parallel_penalty=0.1, lambda_=0.5, reward=1.0)
        with pytest.raises(Exception, match="parallel_penalty"):
            RewardBreakdown(pareto_auc=0.5, parallel_penalty=-0.1, lambda_=0.5, reward=0.0)
