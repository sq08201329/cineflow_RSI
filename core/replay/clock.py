"""虚拟时钟（FR-006）：决策轮 + 有效串行轮 ⌈k/W⌉。

worker_count（W）来自形态配置 configs/*.yaml → replay.worker_count；
有效串行轮支撑做梦层奖励函数的并行惩罚项。
"""

import math
from dataclasses import dataclass

from core.replay.errors import ValidationError


@dataclass
class VirtualClock:
    """回放虚拟时钟（运行时状态，随 tick 递增；非落盘实体，故不 frozen）。"""

    worker_count: int
    decision_rounds: int = 0
    effective_sequential_rounds: float = 0.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.worker_count, int)
            or isinstance(self.worker_count, bool)
            or self.worker_count < 1
        ):
            raise ValidationError(f"worker_count 必须为 ≥ 1 的整数，实际为 {self.worker_count!r}")

    def tick_decision(self) -> None:
        """每次 observed/probe 调用记一个决策轮。"""
        self.decision_rounds += 1

    def tick_execution(self, batch_size: int) -> None:
        """一批 k 个揭示消耗 ⌈k/W⌉ 个有效串行轮。"""
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValidationError(f"batch_size 必须为 ≥ 1 的整数，实际为 {batch_size!r}")
        self.effective_sequential_rounds += math.ceil(batch_size / self.worker_count)
