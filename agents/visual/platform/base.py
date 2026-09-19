"""视频生成适配器基座：模型、错误类型、Protocol（contracts/video-gen-adapter.md）。

花费：submit 含预估，fetch_artifact 时实际扣费入账，实际 ≤ 预估；
错误统一映射 VideoGenError 族，不泄漏实现侧异常类型。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class VideoGenError(Exception):
    """视频生成平台错误基类。"""


class RateLimitedError(VideoGenError):
    """平台限流（可重试）。"""


class UnavailableError(VideoGenError):
    """平台不可用/未配置凭证（可重试）。"""


class InvalidParamsError(VideoGenError):
    """参数非法/未知任务：不重试。"""


class ArtifactNotReadyError(VideoGenError):
    """工件未就绪（completed 前 fetch_artifact）。"""


class GenJobStatus(StrEnum):
    """平台侧生成任务状态机（DB 运营表的 ingested 由执行器管理）。"""

    SUBMITTED = "submitted"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class GenJob:
    """生成任务快照：预估花费随任务返回，实际扣费在 fetch_artifact 时入账。"""

    job_id: str
    external_id: str
    params_hash: str
    estimated_cost_usd: float
    status: GenJobStatus


class VideoGenAdapter(Protocol):
    """视频生成平台适配器协议。"""

    def submit(self, gen_params: dict, *, idempotency_key: str) -> GenJob: ...

    def get_status(self, external_id: str) -> GenJobStatus: ...

    def fetch_artifact(self, external_id: str) -> bytes: ...

    def job_actual_cost(self, external_id: str) -> float:
        """实际扣费查询（fetch_artifact 时入账；对账三方之一）。"""
        ...

    def cancel(self, external_id: str) -> None: ...
