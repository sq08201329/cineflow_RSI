"""真实投放平台适配器（C 路径**协议实现**，T219：骨架 → 本批落地）。

## 实现的是什么（诚实登记，宪章原则六）

**协议实现**：实现在 `core/platform_http.py` 定死的请求形态 + 错误映射 + 纪律，
**不是"某家真实投放平台的对接"**——真实接入仍需按平台 API 调整路径/鉴权/字段（属配置或
小改）。**"能连本地 stub"不等于"C 路径已验证"**：真实凭证、真实账户、真实扣费均未跑过；
本地端到端验证形态见 `tests/integration/test_http_real_stub.py`。

## 协议（本环节用到的端点）

```
POST {base}/campaigns {"material": {...}, "budget_usd": 5.0, "idempotency_key": <key>}
  → 202 {"external_id", "campaign_id", "material_id", "budget_usd", "spent_usd", "status"}
  幂等：同 key 必返回同一活动（external_id/campaign_id 由平台返回，适配器不编造）
GET  {base}/campaigns/{id}          → {"status": "created|delivering|delivered|paused|failed"}
GET  {base}/campaigns/{id}/metrics  → {ctr, completion_rate, conversions, impressions,
                                        clicks, platform_timestamp, data_version}
                                      指标未就绪 → 409
POST {base}/campaigns/{id}/pause    → 202（已结束/已暂停为无操作）
GET  {base}/health（只读探测，ops/check_credentials.py 的 PROBE_PATHS 口径）
```

## 与契约套件一致的语义（同 SimulatedPlatform，受 tests/contract 同一套件约束）

- **指标未就绪如实抛 `MetricsNotReadyError`**（409 = 本协议的"指标未就绪"冲突语义），
  **绝不返 0 假装有数据**——回流管道据此稍后再试（005 的两段式回流纪律）；
- **指标越界即拒**：平台数据经 `validate_metrics` 校验（比率 ∈ [0,1]、计数 ≥ 0 的整数），
  越界/字段缺失/类型不符一律 `MetricValidationError`，不写入树；
- **花费上限**：`spent_usd ≤ budget_usd` 与"申请预算为正"在构造前校验，超出即显式报错
  （`PlatformError`：平台账目异常/请求非法，不静默接受超投）；
- **错误分型**：429→RateLimitedError、409（指标未就绪）→MetricsNotReadyError、
  其余 4xx（含 401/403）→InvalidRequestError、5xx/连接失败/超时/响应不可解析→UnavailableError；
- **超时**：构造参数 `request_timeout_s`（默认 30s），归 UnavailableError 并注明超时值；
- **暂停幂等**：二次 pause 无副作用（终态活动为无操作，由平台侧保证）。

## `distribution` 参数的处置（签名兼容，不接线）

`distribution`（`promo.simulated_platform` 段：base_impressions / ctr_beta / completion_beta…）
是**模拟平台**的指标生成分布；真实平台的指标是平台真值，**不接受分布注入**。故本适配器
**接受该入参只为不破坏既有装配点与契约夹具签名**（`from_env(distribution)`），
既不随请求外发、也不做任何本地推算——真实指标一律取自 `/campaigns/{id}/metrics`。

## 协议假设（真实接入时按平台 API 校准的字段）

- `cost_usd` 与指标口径（ctr/completion_rate 为比率 ∈ [0,1]、counts 为整数）以平台声明为准；
  平台若用"千分比/百分比"或"字符串数字"，须在此处按其口径换算（属实现变更加版本）；
- 真实投放属运营动作：需人工确认账户与预算，且不得由自动进化路径触发（015 纪律）。

凭证经环境变量注入（PROMO_PLATFORM_BASE_URL / PROMO_PLATFORM_API_KEY）；
无凭证构造即 UnavailableError——不假装投放（宪章原则六）。
"""

import os
from dataclasses import asdict

from agents.promo.platform.base import (
    Campaign,
    CampaignStatus,
    InvalidRequestError,
    MetricSnapshot,
    MetricsNotReadyError,
    MetricValidationError,
    PlatformError,
    PromoMaterial,
    RateLimitedError,
    UnavailableError,
    validate_metrics,
)
from core.platform_http import (
    HttpErrors,
    PlatformHttpClient,
    required_cost,
    required_str,
    snippet,
)

# 错误族注入：429 / 409（指标未就绪）/ 其余 4xx / 5xx（+连接失败超时）→ 既有 PlatformError 分型
_ERRORS = HttpErrors(
    rate_limited=RateLimitedError,
    unavailable=UnavailableError,
    invalid=InvalidRequestError,
    conflict=MetricsNotReadyError,  # 本协议里 409 专指"指标未就绪"
    anomaly=PlatformError,
)

_STATUS_MAP = {
    "created": CampaignStatus.CREATED,
    "delivering": CampaignStatus.DELIVERING,
    "delivered": CampaignStatus.DELIVERED,
    "paused": CampaignStatus.PAUSED,
    "failed": CampaignStatus.FAILED,
}

DEFAULT_REQUEST_TIMEOUT_S = 30.0

# 指标字段（比率 / 计数 / 元数据）——按契约口径声明，缺失或类型不符即 MetricValidationError
_RATE_FIELDS = ("ctr", "completion_rate")
_COUNT_FIELDS = ("conversions", "impressions", "clicks")


def _status_of(value: object, *, where: str) -> CampaignStatus:
    status = _STATUS_MAP.get(str(value)) if isinstance(value, str) else None
    if status is None:
        raise UnavailableError(
            f"{where} 的平台状态未登记（实际 {value!r}）：本协议状态取值为 "
            f"{sorted(_STATUS_MAP)}——不静默猜测未知状态"
        )
    return status


def _rate_of(payload: dict, field: str) -> float:
    """比率字段：非数值/bool 即拒（不 str() 强转，避免把"0.5%"这类口径悄悄读成 0.5）。"""
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricValidationError(
            f"指标字段 {field!r} 非合法数值：{value!r}（必须为数值型比率 ∈ [0,1]）"
        )
    return float(value)


def _count_of(payload: dict, field: str) -> int:
    """计数字段：必须为 ≥ 0 的整数（bool 拒绝；浮点拒绝——平台口径不符即拒，不四舍五入）。"""
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetricValidationError(f"指标字段 {field!r} 非 ≥0 整数：{value!r}")
    return value


class HttpRealPlatform:
    """真实渠道 HTTP 协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        distribution: dict | None = None,
        *,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实平台缺凭证：需要 PROMO_PLATFORM_BASE_URL / PROMO_PLATFORM_API_KEY"
            )
        # distribution：模拟平台的指标生成分布，真实平台不接线（见模块 docstring）
        self._client = PlatformHttpClient(
            base_url, api_key, errors=_ERRORS, timeout_s=request_timeout_s
        )

    @classmethod
    def from_env(cls, distribution: dict | None = None) -> "HttpRealPlatform":
        return cls(
            os.environ.get("PROMO_PLATFORM_BASE_URL", ""),
            os.environ.get("PROMO_PLATFORM_API_KEY", ""),
            distribution,
        )

    def create_campaign(
        self, material: PromoMaterial, budget_usd: float, *, idempotency_key: str
    ) -> Campaign:
        """创建投放活动：幂等键原样透传，预算与扣费以响应为准（花费上限纪律）。"""
        if not isinstance(budget_usd, (int, float)) or budget_usd <= 0:
            raise InvalidRequestError(f"申请预算必须为正，实际 {budget_usd!r}")
        payload = self._client.post_json(
            "/campaigns",
            {
                "material": asdict(material),
                "budget_usd": float(budget_usd),
                "idempotency_key": idempotency_key,
            },
        )
        where = "POST /campaigns 响应"
        budget = required_cost(payload, "budget_usd", errors=_ERRORS, where=where)
        spent = required_cost(payload, "spent_usd", errors=_ERRORS, where=where)
        if spent > budget + 1e-9:
            raise PlatformError(
                f"平台实际扣费 ${spent:.4f} 超过申请预算 ${budget:.4f}"
                "——投放花费上限纪律，拒绝入账（不静默接受超投）"
            )
        return Campaign(
            campaign_id=required_str(payload, "campaign_id", errors=_ERRORS, where=where),
            external_id=required_str(payload, "external_id", errors=_ERRORS, where=where),
            material_id=required_str(payload, "material_id", errors=_ERRORS, where=where),
            budget_usd=budget,
            spent_usd=spent,
            status=_status_of(payload.get("status", "created"), where=where),
        )

    def get_status(self, external_id: str) -> CampaignStatus:
        """单次状态查询（不轮询、不 sleep）：节奏由回流管道决定。"""
        payload = self._client.get_json(f"/campaigns/{external_id}")
        return _status_of(payload.get("status"), where=f"GET /campaigns/{external_id} 响应")

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        """读取指标快照（平台真值）：未就绪如实抛 MetricsNotReadyError，绝不返 0。"""
        payload = self._client.get_json(f"/campaigns/{external_id}/metrics")
        where = f"GET /campaigns/{external_id}/metrics 响应"
        snapshot = self._snapshot(payload, where=where)
        validate_metrics(snapshot)  # 越界即 MetricValidationError（不写入树）
        return snapshot

    def pause(self, external_id: str) -> None:
        """暂停投放（已结束/已暂停为无操作）；未知活动 → 4xx → InvalidRequestError。"""
        self._client.post_json(f"/campaigns/{external_id}/pause", {})

    # ---- 内部 ----

    def _snapshot(self, payload: dict, *, where: str) -> MetricSnapshot:
        """指标快照解析：字段缺失/类型不符即 MetricValidationError（不猜、不补零）。"""
        try:
            timestamp = payload["platform_timestamp"]
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise MetricValidationError(
                    f"指标字段 'platform_timestamp' 非合法数值：{timestamp!r}"
                )
            data_version = payload.get("data_version")
            if not isinstance(data_version, str) or not data_version:
                raise MetricValidationError(f"指标字段 'data_version' 缺失或非法：{data_version!r}")
            return MetricSnapshot(
                ctr=_rate_of(payload, "ctr"),
                completion_rate=_rate_of(payload, "completion_rate"),
                conversions=_count_of(payload, "conversions"),
                impressions=_count_of(payload, "impressions"),
                clicks=_count_of(payload, "clicks"),
                platform_timestamp=float(timestamp),
                data_version=data_version,
            )
        except KeyError as exc:
            raise MetricValidationError(
                f"指标响应缺字段 {exc}（{where}）：指标是平台真值，缺失即拒、不补零；"
                f"响应片段：{snippet(str(payload).encode())}"
            ) from exc
        except PlatformError:
            raise
        except (TypeError, ValueError) as exc:
            raise MetricValidationError(f"指标响应字段类型不符（{where}）：{exc}") from exc
