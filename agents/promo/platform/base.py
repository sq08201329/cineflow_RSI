"""平台适配器基座：模型、错误类型、Protocol（contracts/platform-adapter.md）。

适配器原样透传平台数据不篡改；指标越界由调用方经 validate_metrics 校验拒绝。
错误统一映射 PlatformError 族，不泄漏 SDK/HTTP 异常类型。
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class PlatformError(Exception):
    """平台错误基类（统一映射，不泄漏实现侧异常类型）。"""


class RateLimitedError(PlatformError):
    """平台限流（可重试）。"""


class UnavailableError(PlatformError):
    """平台不可用/未配置凭证（可重试）。"""


class InvalidRequestError(PlatformError):
    """请求非法（如未知活动）：不重试。"""


class MetricsNotReadyError(PlatformError):
    """指标未就绪（delivered 前 fetch_metrics）：回流管道稍后再试。"""


class MetricValidationError(PlatformError):
    """指标快照越界（比率 ∉ [0,1]、负计数）：校验拒绝，不写入树。"""


class CampaignStatus(StrEnum):
    """平台侧活动状态机（DB 运营表的 ingested 由回流管道管理）。"""

    CREATED = "created"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(frozen=True)
class PromoMaterial:
    """宣发物料：内容工件化后内容寻址（artifact_hash = BLAKE3）。"""

    material_id: str
    kind: str
    content: dict
    artifact_hash: str
    platform: str
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Campaign:
    """平台侧投放活动快照。"""

    campaign_id: str
    external_id: str
    material_id: str
    budget_usd: float
    spent_usd: float  # 实际扣费，不得超过 budget_usd（契约：花费上限）
    status: CampaignStatus


@dataclass(frozen=True)
class MetricSnapshot:
    """指标快照：平台真值（写入即冻结为常数）。"""

    ctr: float
    completion_rate: float
    conversions: int
    impressions: int
    clicks: int
    platform_timestamp: float
    data_version: str


def validate_metrics(snapshot: MetricSnapshot) -> None:
    """指标越界校验（FR-006）：比率 ∈ [0,1]、计数 ≥ 0，越界拒绝。"""
    for name in ("ctr", "completion_rate"):
        value = getattr(snapshot, name)
        if not 0.0 <= value <= 1.0:
            raise MetricValidationError(f"{name} 越界：{value}（必须 ∈ [0,1]）")
    for name in ("conversions", "impressions", "clicks"):
        value = getattr(snapshot, name)
        if not isinstance(value, int) or value < 0:
            raise MetricValidationError(f"{name} 非法：{value}（必须为 ≥ 0 的整数）")


class PlatformAdapter(Protocol):
    """平台适配器协议：投放创建/状态查询/指标读取/暂停。"""

    def create_campaign(
        self, material: PromoMaterial, budget_usd: float, *, idempotency_key: str
    ) -> Campaign: ...

    def get_status(self, external_id: str) -> CampaignStatus: ...

    def fetch_metrics(self, external_id: str) -> MetricSnapshot: ...

    def pause(self, external_id: str) -> None: ...
