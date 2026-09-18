"""真实平台适配器（契约同构骨架，T219）。

凭证经环境变量注入（PROMO_PLATFORM_BASE_URL / PROMO_PLATFORM_API_KEY）；
无凭证环境构造即 UnavailableError——不假装投放（宪章原则六）。
契约语义（幂等键、花费上限、错误映射）与 SimulatedPlatform 完全一致，
受 tests/contract 同一套件约束。
"""

import os

from agents.promo.platform.base import (
    Campaign,
    CampaignStatus,
    MetricSnapshot,
    PromoMaterial,
    UnavailableError,
)


class HttpRealPlatform:
    """真实渠道 HTTP 适配器骨架：凭证缺失即不可用（骨架阶段不发起真实投放）。"""

    def __init__(self, base_url: str, api_key: str, distribution: dict | None = None) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实平台缺凭证：需要 PROMO_PLATFORM_BASE_URL / PROMO_PLATFORM_API_KEY"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

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
        raise UnavailableError("真实平台接入是凭证配置的运维动作，本期未接入")

    def get_status(self, external_id: str) -> CampaignStatus:
        raise UnavailableError("真实平台未接入")

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        raise UnavailableError("真实平台未接入")

    def pause(self, external_id: str) -> None:
        raise UnavailableError("真实平台未接入")
