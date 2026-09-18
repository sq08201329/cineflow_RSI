"""平台适配器契约套件（US1 / T211，SC-007）。

同一套契约用例对两个实现各跑一遍：花费上限、幂等键、状态机推进、
指标 schema、错误映射、暂停幂等。真实实现无凭证时跳过其用例但保留套件。
"""

import os

import pytest

from agents.promo.platform.base import (
    CampaignStatus,
    InvalidRequestError,
    MetricsNotReadyError,
    PlatformError,
)
from agents.promo.platform.simulated import SimulatedPlatform


def _material(artifact_hash="ab" * 32, tags=None):
    from agents.promo.platform.base import PromoMaterial
    from core.tree.models import new_id

    return PromoMaterial(
        material_id=new_id(),
        kind="copy",
        content={"copy": "测试文案"},
        artifact_hash=artifact_hash,
        platform="simulated",
        tags=tags or ["剧情"],
    )


def _make_real_adapter(promo_config):
    """真实适配器：无凭证环境跳过（接口语义仍受本套件约束）。"""
    from agents.promo.platform.http_real import HttpRealPlatform

    if not os.environ.get("PROMO_PLATFORM_BASE_URL"):
        pytest.skip("真实平台无凭证（PROMO_PLATFORM_BASE_URL 未配置），跳过其契约用例")
    return HttpRealPlatform.from_env(promo_config.simulated_platform)


@pytest.fixture(params=["simulated", "http_real"])
def adapter(request, promo_config):
    if request.param == "simulated":
        return SimulatedPlatform(promo_config.simulated_platform)
    return _make_real_adapter(promo_config)


class Test花费上限:
    def test_实际扣费不超过申请额(self, adapter):
        campaign = adapter.create_campaign(_material(), 5.0, idempotency_key="r1:m1")
        assert campaign.spent_usd <= 5.0
        assert campaign.spent_usd >= 0


class Test幂等键:
    def test_同幂等键返回同一活动(self, adapter):
        material = _material()
        first = adapter.create_campaign(material, 5.0, idempotency_key="r1:m1")
        second = adapter.create_campaign(material, 5.0, idempotency_key="r1:m1")
        assert second.external_id == first.external_id
        assert second.campaign_id == first.campaign_id

    def test_不同幂等键不同活动(self, adapter):
        material = _material()
        a = adapter.create_campaign(material, 5.0, idempotency_key="r1:m1")
        b = adapter.create_campaign(material, 5.0, idempotency_key="r1:m2")
        assert a.external_id != b.external_id


class Test状态机与指标:
    def test_指标就绪前_fetch_抛_MetricsNotReadyError(self, adapter):
        campaign = adapter.create_campaign(_material(), 5.0, idempotency_key="r2:m1")
        with pytest.raises(MetricsNotReadyError):
            adapter.fetch_metrics(campaign.external_id)

    def test_状态机推进至_delivered(self, adapter):
        campaign = adapter.create_campaign(_material(), 5.0, idempotency_key="r3:m1")
        assert adapter.get_status(campaign.external_id) in (
            CampaignStatus.CREATED,
            CampaignStatus.DELIVERING,
        )
        for _ in range(5):  # 推进至 delivered（模拟实现每次调用推进一格）
            status = adapter.get_status(campaign.external_id)
            if status is CampaignStatus.DELIVERED:
                break
        assert status is CampaignStatus.DELIVERED

    def test_指标快照_schema(self, adapter):
        campaign = adapter.create_campaign(_material(), 5.0, idempotency_key="r4:m1")
        for _ in range(5):
            if adapter.get_status(campaign.external_id) is CampaignStatus.DELIVERED:
                break
        snapshot = adapter.fetch_metrics(campaign.external_id)
        assert 0.0 <= snapshot.ctr <= 1.0
        assert 0.0 <= snapshot.completion_rate <= 1.0
        assert snapshot.impressions >= 0 and snapshot.clicks >= 0
        assert snapshot.conversions >= 0
        assert snapshot.data_version

    def test_确定性_同物料同参数同指标(self, adapter):
        m = _material()
        c1 = adapter.create_campaign(m, 5.0, idempotency_key="r5:m1")
        for _ in range(5):
            if adapter.get_status(c1.external_id) is CampaignStatus.DELIVERED:
                break
        snapshot = adapter.fetch_metrics(c1.external_id)
        again = adapter.fetch_metrics(c1.external_id)
        assert snapshot == again  # 逐字节可复现


class Test错误映射:
    def test_未知活动_get_status(self, adapter):
        with pytest.raises(InvalidRequestError):
            adapter.get_status("ghost-external-id")

    def test_未知活动_fetch_metrics(self, adapter):
        with pytest.raises(InvalidRequestError):
            adapter.fetch_metrics("ghost-external-id")

    def test_错误类型不泄漏实现细节(self, adapter):
        with pytest.raises(PlatformError):
            adapter.get_status("ghost")


class Test暂停:
    def test_pause_幂等(self, adapter):
        campaign = adapter.create_campaign(_material(), 5.0, idempotency_key="r6:m1")
        adapter.pause(campaign.external_id)
        adapter.pause(campaign.external_id)  # 二次调用无副作用

    def test_pause_已结束活动无操作(self, adapter):
        campaign = adapter.create_campaign(_material(), 5.0, idempotency_key="r7:m1")
        for _ in range(5):
            if adapter.get_status(campaign.external_id) is CampaignStatus.DELIVERED:
                break
        adapter.pause(campaign.external_id)  # 已 delivered：无操作
        assert adapter.get_status(campaign.external_id) is CampaignStatus.DELIVERED
