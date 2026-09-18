"""回放轨迹（FR-013/FR-015）：一轮回放的完整记录，可序列化为 JSON 报告。

轨迹是做梦层奖励函数 `pareto_auc(best_score_curve, probe_count)
− λ · (effective_sequential_rounds / max(probe_count, 1))` 的直接输入，
并携带策略版本（代码 BLAKE3 前 12 位）维持谱系可查。
"""

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from core.replay.errors import ValidationError
from core.tree.models import CostRecord


class TrajectoryStatus(StrEnum):
    """回放结局状态机（终态，无迁移）。"""

    COMPLETED = "completed"
    BUDGET_EXCEEDED = "budget_exceeded"
    POLICY_ERROR = "policy_error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ReplayTrajectory:
    """回放轨迹：逐轮最优得分曲线、probe 计数、串行轮数、成本合计、策略版本。"""

    policy_version: str
    best_score_curve: list[float]
    probe_count: int
    effective_sequential_rounds: float
    total_cost: CostRecord
    final_node_id: str | None
    status: TrajectoryStatus
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, TrajectoryStatus):
            object.__setattr__(self, "status", TrajectoryStatus(self.status))
        if (
            not isinstance(self.probe_count, int)
            or isinstance(self.probe_count, bool)
            or self.probe_count < 0
        ):
            raise ValidationError(f"probe_count 必须为 ≥ 0 的整数，实际为 {self.probe_count!r}")
        if self.effective_sequential_rounds < 0:
            raise ValidationError("effective_sequential_rounds 必须 ≥ 0")
        for score in self.best_score_curve:
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not 0.0 <= score <= 1.0
            ):
                raise ValidationError(f"best_score_curve 元素必须 ∈ [0,1]，实际为 {score!r}")
        if not isinstance(self.policy_version, str):
            raise ValidationError("policy_version 必须为字符串（谱系字段，FR-015）")

    def to_dict(self) -> dict:
        """JSON 值语义字典（quickstart 报告与做梦层消费的形态）。"""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
