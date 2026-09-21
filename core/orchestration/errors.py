"""通用编排错误类型（功能 015）：执行器/图/账目的显式失败语义。

诚实边界（宪章原则六）：拒绝与失败一律**显式抛错**，不静默降级——
拓扑不合法、续跑输入已变、账目对不上都必须让调用方看见。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型引用（避免与 models 形成导入环）
    from core.orchestration.models import CandidateOutcome


class OrchestrationError(Exception):
    """编排层错误基类。"""


class DagError(OrchestrationError):
    """依赖图非法（环依赖 / 依赖不存在 / stage_id 重复）。"""


class ResumeRejectedError(OrchestrationError):
    """断点续跑被拒（输入指纹或配置指纹与运行记录不一致，或阶段集合已变）。"""


class LedgerMismatchError(OrchestrationError):
    """账目对账不一致（按阶段的记录成本与来源账目不符）。"""


class StageFailedError(OrchestrationError):
    """业务侧执行入口判定本阶段失败（候选全败）——附带全部候选的判 0 理由。"""

    def __init__(self, reason: str, *, candidates: tuple[CandidateOutcome, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.candidates = tuple(candidates)
