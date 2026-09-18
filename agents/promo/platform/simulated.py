"""确定性模拟平台（research 决策 3，开发/CI 默认实现）。

以 blake3(material.artifact_hash + budget + 平台 salt) 为种子产出指标：
同物料同参数逐字节可复现；内部账本记录每次扣费供对账（SC-003）。
"""

import random
from dataclasses import replace

import blake3

from agents.promo.platform.base import (
    Campaign,
    CampaignStatus,
    InvalidRequestError,
    MetricSnapshot,
    MetricsNotReadyError,
    PlatformError,
    PromoMaterial,
)
from core.tree.models import new_id

_SALT = "simulated-platform-v1"


class SimulatedPlatform:
    """确定性模拟投放平台：幂等键去重、状态机逐次推进、哈希种子指标。"""

    def __init__(self, distribution: dict, *, data_version: str = "sim-v1") -> None:
        self._dist = distribution
        self._data_version = data_version
        self._campaigns: dict[str, Campaign] = {}
        self._ticks: dict[str, int] = {}  # external_id → get_status 调用次数
        self._by_idempotency: dict[str, str] = {}
        self._ledger: list[dict] = []  # 内部账本：每次扣费一条

    @property
    def campaign_count(self) -> int:
        return len(self._campaigns)

    @property
    def total_spent(self) -> float:
        return sum(entry["spent_usd"] for entry in self._ledger)

    def _seed(self, *parts: str) -> int:
        return int(blake3.blake3("|".join(parts).encode()).hexdigest()[:16], 16)

    def create_campaign(
        self, material: PromoMaterial, budget_usd: float, *, idempotency_key: str
    ) -> Campaign:
        if idempotency_key in self._by_idempotency:  # 幂等：重复提交返回同一活动
            return self._campaigns[self._by_idempotency[idempotency_key]]
        if budget_usd <= 0:
            raise PlatformError("申请预算必须为正")

        external_id = f"sim-{blake3.blake3(idempotency_key.encode()).hexdigest()[:16]}"
        rng = random.Random(self._seed(material.artifact_hash, str(budget_usd), _SALT))
        spent = round(budget_usd * rng.uniform(0.6, 1.0), 2)  # 花费上限：不超申请额
        campaign = Campaign(
            campaign_id=new_id(),
            external_id=external_id,
            material_id=material.material_id,
            budget_usd=budget_usd,
            spent_usd=spent,
            status=CampaignStatus.CREATED,
        )
        self._campaigns[external_id] = campaign
        self._by_idempotency[idempotency_key] = external_id
        self._ticks[external_id] = 0
        self._ledger.append({"external_id": external_id, "spent_usd": spent})
        return campaign

    def _require(self, external_id: str) -> Campaign:
        if external_id not in self._campaigns:
            raise InvalidRequestError(f"未知活动：{external_id}")
        return self._campaigns[external_id]

    def get_status(self, external_id: str) -> CampaignStatus:
        campaign = self._require(external_id)
        if campaign.status in (
            CampaignStatus.DELIVERED,
            CampaignStatus.PAUSED,
            CampaignStatus.FAILED,
        ):
            return campaign.status
        # created → delivering → delivered：每次查询推进一格（确定性）
        self._ticks[external_id] += 1
        nxt = (
            CampaignStatus.DELIVERING if self._ticks[external_id] == 1 else CampaignStatus.DELIVERED
        )
        self._campaigns[external_id] = replace(campaign, status=nxt)
        return nxt

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        campaign = self._require(external_id)
        if campaign.status is not CampaignStatus.DELIVERED:
            raise MetricsNotReadyError(f"活动 {external_id} 未 delivered，指标未就绪")
        rng = random.Random(self._seed(external_id, "metrics", _SALT))
        base = int(self._dist["base_impressions"])
        impressions = base + rng.randint(0, base // 2)
        ctr = rng.betavariate(*self._dist["ctr_beta"])
        completion = rng.betavariate(*self._dist["completion_beta"])
        clicks = int(impressions * ctr)
        return MetricSnapshot(
            ctr=ctr,
            completion_rate=completion,
            conversions=int(clicks * rng.betavariate(2, 20)),
            impressions=impressions,
            clicks=clicks,
            platform_timestamp=1700000000.0,  # 固定时间戳：逐字节可复现
            data_version=self._data_version,
        )

    def pause(self, external_id: str) -> None:
        campaign = self._require(external_id)
        if campaign.status in (
            CampaignStatus.DELIVERED,
            CampaignStatus.FAILED,
            CampaignStatus.PAUSED,
        ):
            return  # 幂等：已结束/已暂停为无操作
        self._campaigns[external_id] = replace(campaign, status=CampaignStatus.PAUSED)
