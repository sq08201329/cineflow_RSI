"""平台适配器基座：模型、错误类型、Protocol（contracts/platform-adapter.md）。

适配器原样透传平台数据不篡改；指标越界由调用方经 validate_metrics 校验拒绝。
错误统一映射 PlatformError 族，不泄漏 SDK/HTTP 异常类型。
"""

import re
from dataclasses import dataclass, field
from datetime import date
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
    """指标快照：平台真值（写入即冻结为常数）。

    `metric_date`（功能 020）是**归属日**——平台指标所描述的日期（ISO `YYYY-MM-DD`），
    周期归属的唯一依据。它**末位可选**（`None` = 平台未提供）：数据类不承担必填，
    理由是兼容历史落盘 payload 的 dict 重建（`agents/promo/evaluators/platform_metrics.py`）；
    **存在性检查与格式检查落在写入路径**（本模块的 `validate_metrics` + 两个适配器的
    采集出口）——历史 payload 重建不会构造期抛错，而**新采集写入路径产生 `None` 的次数恒 0**。

    命名约束（不得复用）：`platform_timestamp` 是**真值产生时刻**（语义不变）、
    `data_version` 是**平台数据版本**；三者**不等同**（迟到/回补时归属日与平台时间戳必然不同）。
    """

    ctr: float
    completion_rate: float
    conversions: int
    impressions: int
    clicks: int
    platform_timestamp: float
    data_version: str
    metric_date: str | None = None


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_metric_ranges(snapshot: MetricSnapshot) -> None:
    """指标越界校验（FR-006）：比率 ∈ [0,1]、计数 ≥ 0，越界拒绝。

    只做**值域**校验、**不**检查归属日存在性——读取/重建路径（历史 payload 无该键）
    必须继续可用（存在性检查只在写入路径强制，见 `validate_metrics`）。
    """
    for name in ("ctr", "completion_rate"):
        value = getattr(snapshot, name)
        if not 0.0 <= value <= 1.0:
            raise MetricValidationError(f"{name} 越界：{value}（必须 ∈ [0,1]）")
    for name in ("conversions", "impressions", "clicks"):
        value = getattr(snapshot, name)
        if not isinstance(value, int) or value < 0:
            raise MetricValidationError(f"{name} 非法：{value}（必须为 ≥ 0 的整数）")


def validate_metrics(snapshot: MetricSnapshot) -> None:
    """**写入路径**校验：值域（同 `validate_metric_ranges`）+ 归属日存在性/格式。

    缺失 ⇒ `MetricValidationError`「平台未提供指标归属日」；形态非法/非真实日历日
    ⇒「指标归属日非法：…」。调用点 = 两个适配器的采集出口与回流写入
    （`agents/promo/{ingest,daily}.py`）；读取/重建路径走 `validate_metric_ranges`。
    """
    validate_metric_ranges(snapshot)
    metric_date = snapshot.metric_date
    if metric_date is None:
        raise MetricValidationError(
            "平台未提供指标归属日（metric_date）：归属日是周期归属的唯一依据，缺失即失败、"
            "不以拉取/采集时刻兜底——该条不落锚点、不落日级回流记录"
        )
    if not isinstance(metric_date, str) or not _ISO_DATE_RE.fullmatch(metric_date):
        raise MetricValidationError(
            f"指标归属日非法：{metric_date!r}（必须为 ISO 日期 YYYY-MM-DD）"
        )
    try:
        date.fromisoformat(metric_date)
    except ValueError as exc:
        raise MetricValidationError(
            f"指标归属日非法：{metric_date!r}（非真实日历日：{exc}）"
        ) from exc


class PlatformAdapter(Protocol):
    """平台适配器协议：投放创建/状态查询/指标读取/暂停。"""

    def create_campaign(
        self, material: PromoMaterial, budget_usd: float, *, idempotency_key: str
    ) -> Campaign: ...

    def get_status(self, external_id: str) -> CampaignStatus: ...

    def fetch_metrics(self, external_id: str) -> MetricSnapshot: ...

    def pause(self, external_id: str) -> None: ...
