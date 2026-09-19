"""奖励函数（research 决策 1 权威口径，宪章奖励函数）。

reward = pareto_auc − λ·parallel_penalty：
- pareto_auc = 逐轮最优得分曲线的梯形面积 Σ(s[i]+s[i+1])/2 除以满分红线
  面积 (n−1)，归一化 [0,1]；曲线首点（初始观测）计入；
  n < 2 时取首值（单点）或 0（空曲线）；
- parallel_penalty = effective_sequential_rounds / max(probe_count, 1)。

本模块是 pareto_auc 的唯一权威实现（003 report 由 T425 对齐复用）。
"""

from dataclasses import dataclass

from core.replay.trajectory import ReplayTrajectory


class RewardError(Exception):
    """奖励分解校验失败。"""


def pareto_auc(best_score_curve: list[float]) -> float:
    """梯形归一化口径（决策 1，权威定义）。"""
    n = len(best_score_curve)
    if n == 0:
        return 0.0
    if n == 1:
        return float(best_score_curve[0])
    area = sum((best_score_curve[i] + best_score_curve[i + 1]) / 2 for i in range(n - 1))
    return area / (n - 1)


def parallel_penalty(effective_sequential_rounds: float, probe_count: int) -> float:
    """并行惩罚 = 有效串行轮 / max(probe 数, 1)。"""
    return effective_sequential_rounds / max(probe_count, 1)


@dataclass(frozen=True)
class RewardBreakdown:
    """奖励分解：pareto_auc / parallel_penalty / λ / reward。"""

    pareto_auc: float
    parallel_penalty: float
    lambda_: float
    reward: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.pareto_auc <= 1.0:
            raise RewardError(f"pareto_auc 必须 ∈ [0,1]，实际为 {self.pareto_auc!r}")
        if self.parallel_penalty < 0:
            raise RewardError(f"parallel_penalty 必须 ≥ 0，实际为 {self.parallel_penalty!r}")


def compute_reward(trajectory: ReplayTrajectory, lambda_: float) -> RewardBreakdown:
    """从回放轨迹计算奖励分解。"""
    auc = pareto_auc(trajectory.best_score_curve)
    penalty = parallel_penalty(trajectory.effective_sequential_rounds, trajectory.probe_count)
    return RewardBreakdown(
        pareto_auc=auc,
        parallel_penalty=penalty,
        lambda_=lambda_,
        reward=auc - lambda_ * penalty,
    )
