"""投放前置门禁与两腿分辨单测（功能 020 / 契约 C14 / FR-008，T2013）。

覆盖面一句话：C 路径投放调用**确定地**受 019 的跨进程账本门禁约束，且与 015 的
**进程内按轮上限**共同生效、口径可分辨、互不替代：

- **调用前拒绝**：超限的那一笔 ⇒ 平台调用 **0 次**、成本 **0 入账**、`alerts.jsonl` 落
  `kind=budget_refused`、原因**点名环节与余量**；
- **未声明投放环节**（缺 `stage` / 环节不在该渠道 `tiers` 键集）⇒ `tier_undeclared`，
  同样零调用零入账；
- **两腿辨**：015 的按轮上限（`agents/promo/loop.py` 原位保留）与被 019 账本拒绝的两条文案
  **互不相同且可归因**；被 019 拒后 015 的按轮口径**不重复记账**（`spent_usd` 不变）；
- **唯一包装点**：`core/billing/runlog.py` 的 `RecordingChannelCall` 是投放面唯一的调用包装点
  （`agents/promo/loop.py` 内零门禁调用）；
- **分型互斥**：`budget_refused`（本系统门禁）≠ 厂商 401/403 ≠ 429。

零真实花费：全部走**桩适配器/模拟平台 + 夹具配置**，无外部网络、无凭证。
"""

import copy
import json
from pathlib import Path

import pytest
import yaml

from agents.promo.loop import run_round
from core.billing.budget import (
    AlertLog,
    BudgetConfig,
    BudgetRefusedError,
    FileLedger,
    alerts_path,
    assemble_guard,
    channel_for_adapter,
    ledger_path,
)
from core.billing.runlog import RecordingChannelCall, load_run

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMO_ADAPTER = "promo_platform"
DELIVERY_TIER = "promo_launch"
DELIVERY_STAGE = DELIVERY_TIER  # 投放环节 id = 该渠道恰好一个档位的键


class _CountingPlatform:
    """投放平台桩：只计调用次数（零真实花费），返回值 = 平台侧实测花费。"""

    def __init__(self, *, spent_usd: float = 0.05, error: Exception | None = None) -> None:
        self.calls = 0
        self.spent_usd = spent_usd
        self.error = error

    def create_campaign(self, material, budget_usd, *, idempotency_key):
        from agents.promo.platform.base import Campaign, CampaignStatus

        self.calls += 1
        if self.error is not None:
            raise self.error
        return Campaign(
            campaign_id="c-1",
            external_id="ext-1",
            material_id=getattr(material, "material_id", "mat-1"),
            budget_usd=float(budget_usd),
            spent_usd=min(self.spent_usd, float(budget_usd)),
            status=CampaignStatus.DELIVERED,
        )

    def get_status(self, external_id: str):
        from agents.promo.platform.base import CampaignStatus

        return CampaignStatus.DELIVERED

    def pause(self, external_id: str) -> None:
        return None


def _material():
    from agents.promo.platform.base import PromoMaterial

    return PromoMaterial(
        material_id="mat-1",
        kind="copy",
        content={"text": "夹具文案"},
        artifact_hash="ab" * 32,
        platform="stub",
    )


def _media_config(tmp_path: Path, *, delivery_limit_usd: float, name: str = "shortdrama.yaml"):
    """短剧态配置派生：账本根落 tmp，**投放档（media）额度**按用例压到指定值。"""
    payload = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8"))
    )
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    media = next(
        key
        for key, spec in payload["budget"]["channels"].items()
        if spec["adapter"] == PROMO_ADAPTER
    )
    payload["budget"]["channels"][media]["tiers"][DELIVERY_TIER]["limit_usd"] = delivery_limit_usd
    target = tmp_path / name
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _delivery_assembly(config_path: Path):
    cfg = BudgetConfig.from_yaml(config_path)
    channel = channel_for_adapter(cfg, PROMO_ADAPTER)
    return assemble_guard(config_path, channel_id=channel.channel_id)


def _wrapped(config_path: Path, adapter, *, source: str = "real", stage: str = DELIVERY_STAGE):
    assembly = _delivery_assembly(config_path)
    return RecordingChannelCall(adapter, assembly=assembly, stage=stage, source=source)


def _ledger_of(config_path: Path, channel_id: str) -> dict:
    cfg = BudgetConfig.from_yaml(config_path)
    return FileLedger(ledger_path(cfg.ledger_root(), channel_id), timeout_seconds=1.0).read()


def _alert_kinds(config_path: Path, channel_id: str) -> list[str]:
    cfg = BudgetConfig.from_yaml(config_path)
    return [
        entry["kind"] for entry in AlertLog(alerts_path(cfg.ledger_root(), channel_id)).entries()
    ]


def _media_channel_id(config_path: Path) -> str:
    return channel_for_adapter(BudgetConfig.from_yaml(config_path), PROMO_ADAPTER).channel_id


# ---------------------------------------------------------------------------
# ① 超限 ⇒ 调用前拒绝、平台 0 次、零入账
# ---------------------------------------------------------------------------


class Test调用前拒绝:
    def test_超限投放零调用零入账且原因点名环节与额度(self, tmp_path):
        config = _media_config(tmp_path, delivery_limit_usd=1e-6)
        channel = _media_channel_id(config)
        adapter = _CountingPlatform()
        call = _wrapped(config, adapter)
        with pytest.raises(BudgetRefusedError) as excinfo:
            call.create_campaign(_material(), 1.0, idempotency_key="k")
        assert excinfo.value.reason == "over_limit"
        assert DELIVERY_STAGE in str(excinfo.value)  # 点名环节
        assert excinfo.value.remaining_usd is not None  # 点名余量
        assert adapter.calls == 0  # **平台调用 0 次**
        record = _ledger_of(config, channel)["tiers"][DELIVERY_TIER]
        assert record["spent_usd"] == 0.0  # 零入账
        assert record["refusals"] == 1  # 拒绝计数（019 既有口径）
        assert _alert_kinds(config, channel) == ["budget_refused"]
        entry = load_run(
            "2026-09-25", channel_id=channel, root=BudgetConfig.from_yaml(config).ledger_root()
        )
        assert entry["entries"] == [] or entry["entries"][0]["result"] == "refused"

    def test_未声明投放环节即_tier_undeclared(self, tmp_path):
        config = _media_config(tmp_path, delivery_limit_usd=10.0)
        channel = _media_channel_id(config)
        for stage in ("ghost_tier", ""):
            adapter = _CountingPlatform()
            call = _wrapped(config, adapter, stage=stage)
            with pytest.raises(BudgetRefusedError) as excinfo:
                call.create_campaign(_material(), 0.1, idempotency_key="k")
            assert excinfo.value.reason == "tier_undeclared"
            assert adapter.calls == 0
        assert _ledger_of(config, channel).get("tiers", {}) == {}  # 未声明档不写账本
        assert set(_alert_kinds(config, channel)) == {"budget_refused"}

    def test_结算按平台实测入账(self, tmp_path):
        config = _media_config(tmp_path, delivery_limit_usd=10.0)
        channel = _media_channel_id(config)
        adapter = _CountingPlatform(spent_usd=0.05)
        call = _wrapped(config, adapter)
        campaign = call.create_campaign(_material(), 1.0, idempotency_key="k")
        assert campaign.spent_usd == 0.05 and adapter.calls == 1
        record = _ledger_of(config, channel)["tiers"][DELIVERY_TIER]
        assert record["spent_usd"] == pytest.approx(0.05)
        assert record["reserved_usd"] == 0.0  # 预留已结清（无残留）
        entry = load_run(
            "2026-09-25", channel_id=channel, root=BudgetConfig.from_yaml(config).ledger_root()
        )["entries"][-1]
        assert entry["result"] == "ok" and entry["stage"] == DELIVERY_STAGE
        assert entry["source"] == "real" and entry["cost_source"] == "measured_backfill"


# ---------------------------------------------------------------------------
# ③ 两腿同时生效且可辨（015 按轮上限 × 019 账本）
# ---------------------------------------------------------------------------


def _promo_briefs(config, budgets):
    return [
        {
            "prompt": "写一句宣发文案",
            "gen_params": {"temperature": 0.3 + 0.01 * index},
            "kind": "copy",
            "tags": ["剧情"],
            "budget_usd": budget,
        }
        for index, budget in enumerate(budgets)
    ]


class _StubPolicy:
    policy_version = "a1b2c3d4e5f6"

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _run_round(config_path: Path, adapter, briefs, *, tree_store, campaigns_engine, tmp_path):
    from agents.promo.config import PromoConfig
    from core.llm_gateway.backends.mock import MockBackend
    from core.llm_gateway.gateway import LLMGateway
    from core.tree.artifacts import LocalArtifactStore

    promo_config = PromoConfig.from_yaml(config_path)
    gateway = LLMGateway(MockBackend(), price_book=promo_config.model_prices, sleep=lambda _: None)
    return run_round(
        "r-gate",
        _StubPolicy(briefs),
        tree_store,
        LocalArtifactStore(tmp_path / "artifacts"),
        adapter,
        gateway,
        promo_config,
        engine=campaigns_engine,
        sleep=lambda _: None,
    )


class Test两腿共同生效:
    def test_015_按轮上限先拒且平台零调用(
        self, tmp_path, tree_store, campaigns_engine, artifact_store
    ):
        """第一腿：015 的进程内按轮上限（`budget_cap_usd = 120 × 0.02 = 2.4`）。"""
        config = _media_config(tmp_path, delivery_limit_usd=10.0)
        adapter = _CountingPlatform()
        call = _wrapped(config, adapter)
        # 申请 3.0 > 按轮上限 2.4 ⇒ 015 在 create_campaign **之前**拒投
        result = _run_round(
            config,
            call,
            _promo_briefs(config, [3.0]),
            tree_store=tree_store,
            campaigns_engine=campaigns_engine,
            tmp_path=tmp_path,
        )
        rejected = [item for item in result.materials if item["status"] == "rejected"]
        assert rejected and "预算门禁" in rejected[0]["reason"] and "拒投" in rejected[0]["reason"]
        assert adapter.calls == 0
        assert result.spent_usd == 0.0
        assert artifact_store  # 夹具形态（见 conftest）：本轮零投放

    def test_019_账本后拒且两腿文案可辨(
        self, tmp_path, tree_store, campaigns_engine, artifact_store
    ):
        """第二腿：019 跨进程账本（该渠道投放档额度压到极小）。"""
        config = _media_config(tmp_path, delivery_limit_usd=1e-6)
        channel = _media_channel_id(config)
        adapter = _CountingPlatform()
        call = _wrapped(config, adapter)
        # 申请 1.0 ≤ 按轮上限 2.4（015 放行）⇒ 在调用前被 019 门禁拒绝
        result = _run_round(
            config,
            call,
            _promo_briefs(config, [1.0]),
            tree_store=tree_store,
            campaigns_engine=campaigns_engine,
            tmp_path=tmp_path,
        )
        rejected = [item for item in result.materials if item["status"] == "rejected"]
        assert rejected
        reason = rejected[0]["reason"]
        assert "预算拒绝" in reason and "over_limit" in reason  # 019 的文案（可归因）
        assert "拒投" not in reason  # 不是 015 的那一条
        assert adapter.calls == 0  # 平台调用 0 次
        assert result.spent_usd == 0.0  # 015 的按轮口径**不重复记账**（未被记入）
        assert _alert_kinds(config, channel) == ["budget_refused"]
        assert artifact_store

    def test_两道不得互相替代_静态(self):
        loop_source = (REPO_ROOT / "agents" / "promo" / "loop.py").read_text(encoding="utf-8")
        assert "budget_cap_usd" in loop_source  # 015 的按轮上限原位保留
        assert "（拒投）" in loop_source  # 其拒绝文案未被替换
        runlog_source = (REPO_ROOT / "core" / "billing" / "runlog.py").read_text(encoding="utf-8")
        assert "guard.check(" in runlog_source  # 019 的门禁调用在包装内
        backends = (REPO_ROOT / "agents" / "pilot" / "backends.py").read_text(encoding="utf-8")
        assert "_promo" in backends and "RecordingChannelCall(" in backends
        assert "assemble_guard(" in backends

    def test_唯一包装点_静态(self):
        """C14：投放面唯一的调用包装点是 `RecordingChannelCall`；loop 内零门禁调用。"""
        loop_source = (REPO_ROOT / "agents" / "promo" / "loop.py").read_text(encoding="utf-8")
        for forbidden in ("RecordingChannelCall(", "SpendGuard(", "guard.check(", ".check("):
            assert forbidden not in loop_source, forbidden
        # 未声明环节/超限的**证据**来自包装点写入的告警与运行记录（本文件已逐条机检）
        assert "BudgetRefusedError" in loop_source  # 零成本分支：捕获拒绝并如实落节点
        assert "refusal_reason" in loop_source  # 原因沿用 core 的单一措辞来源


# ---------------------------------------------------------------------------
# ⑥ 分型互斥：budget_refused ≠ 厂商 401/403 ≠ 429
# ---------------------------------------------------------------------------


class Test分型互斥:
    def test_三类错误类型互斥(self):
        from agents.promo.platform.base import (
            PlatformError,
            RateLimitedError,
            UnavailableError,
        )

        assert issubclass(RateLimitedError, PlatformError)
        assert issubclass(UnavailableError, PlatformError)
        assert issubclass(BudgetRefusedError, Exception)
        assert not issubclass(BudgetRefusedError, PlatformError)  # 门禁拒绝 ≠ 厂商失败

    def test_厂商限流不落_budget_refused_且不冒充模拟(
        self, tmp_path, tree_store, campaigns_engine, artifact_store
    ):
        from agents.promo.platform.base import RateLimitedError

        config = _media_config(tmp_path, delivery_limit_usd=10.0)
        channel = _media_channel_id(config)
        adapter = _CountingPlatform(error=RateLimitedError("厂商限流 429"))
        call = _wrapped(config, adapter)
        result = _run_round(
            config,
            call,
            _promo_briefs(config, [1.0]),
            tree_store=tree_store,
            campaigns_engine=campaigns_engine,
            tmp_path=tmp_path,
        )
        failed = [item for item in result.materials if item["status"] == "failed"]
        assert failed and "投放失败" in failed[0]["reason"]
        assert adapter.calls > 0  # 适配器确实被调用过（退避重试）
        assert _alert_kinds(config, channel) == []  # 厂商限流不落预算门禁告警
        record = _ledger_of(config, channel)["tiers"][DELIVERY_TIER]
        assert record["spent_usd"] == 0.0  # 未发生花费（预留以 0 结清）
        assert record["reserved_usd"] == 0.0  # 无残留预留
        entries = load_run(
            "2026-09-25", channel_id=channel, root=BudgetConfig.from_yaml(config).ledger_root()
        )["entries"]
        assert [entry["result"] for entry in entries] == ["failed"] * len(entries)
        assert {entry["source"] for entry in entries} == {"real"}  # 不静默回落模拟
        assert artifact_store

    def test_落盘告警类型取值域不变(self, tmp_path):
        from core.billing.budget import ALERT_KINDS

        assert "budget_refused" in ALERT_KINDS
        assert len(ALERT_KINDS) == 6  # 六值固定：新增类型必须显式扩取值域
        config = _media_config(tmp_path, delivery_limit_usd=1e-6)
        channel = _media_channel_id(config)
        with pytest.raises(BudgetRefusedError):
            _wrapped(config, _CountingPlatform()).create_campaign(
                _material(), 1.0, idempotency_key="k"
            )
        entries = AlertLog(
            alerts_path(BudgetConfig.from_yaml(config).ledger_root(), channel)
        ).entries()
        assert json.loads(json.dumps(entries[0], ensure_ascii=False))["kind"] == "budget_refused"
