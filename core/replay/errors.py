"""回放模块的错误类型体系。

与 core/tree、core/evaluators 相互独立（core 内各模块不互相耦合）。
契约见 specs/002-replay-sandbox/contracts/replay-api.md。
"""


class ReplayError(Exception):
    """回放模块全部错误的基类。"""


class ValidationError(ReplayError):
    """参数/构造校验失败（worker_count、batch_size、轨迹字段等）。"""


class PoolError(ReplayError):
    """模拟器池拒绝：树未冻结、异 Agent 混池。"""


class BudgetExhaustedError(ReplayError):
    """probe 次数达到预算上限（FR-007，超限拒绝）。"""
