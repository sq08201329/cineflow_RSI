"""探索策略基类：Budget / SimulatorEnv / ExplorationPolicy（契约 §3）。

策略是唯一被进化/被评估的对象：`solve(env, budget) -> node_id`。
env 仅暴露 observed()/probe() 两个信息入口（沙箱内由 IPC 客户端实现同名协议）。

回放纪律（宪章原则三，FR-005）：回放装配时 max_generation_calls 恒为 0——
由 zero_generation_budget 强制归零 + from_trees 断言双重落实，不依赖调用方自觉。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Protocol

from core.replay.errors import ValidationError
from core.replay.observation import Observation, ProbeResult


@dataclass(frozen=True)
class Budget:
    """探索预算：probe 上限 + 生成调用上限（回放中恒为 0）。"""

    max_probes: int
    max_generation_calls: int = 0

    def __post_init__(self) -> None:
        for name in ("max_probes", "max_generation_calls"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError(f"Budget.{name} 必须为 ≥ 0 的整数，实际为 {value!r}")


def zero_generation_budget(budget: Budget) -> Budget:
    """回放装配：强制 max_generation_calls = 0（FR-005，零生成不是约定而是强制）。"""
    return replace(budget, max_generation_calls=0)


def assert_replay_budget(budget: Budget) -> None:
    """回放装配断言：进入回放路径的预算必须已被归零（第二道校验）。"""
    if budget.max_generation_calls != 0:
        raise ValidationError(
            f"回放路径 max_generation_calls 恒为 0，实际为 {budget.max_generation_calls}"
        )


class SimulatorEnv(Protocol):
    """策略唯一可用的环境接口（沙箱内由 IPC 客户端实现）。"""

    def observed(self) -> dict[str, Observation]: ...

    def probe(self, parent_id: str, gen_params: dict) -> ProbeResult: ...


class ExplorationPolicy(ABC):
    """探索策略抽象基类。代码即策略，版本 = 代码内容 BLAKE3 前 12 位。"""

    @abstractmethod
    def solve(self, env: SimulatorEnv, budget: Budget) -> str:
        """运行探索，返回最终选中的 node_id。"""
        ...
