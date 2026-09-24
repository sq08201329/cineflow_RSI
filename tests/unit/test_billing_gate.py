"""前置预算门禁端到端（功能 019 / US1 / T1924）：契约 C10 + C11。

用**真守卫**（`SpendGuard` + `FileLedger` + `AlertLog`）走真实现路径：固定六步序列的次序即语义、
超限即拒绝且后端 0 次调用、网关三量不变、原因与预估价落 `alerts.jsonl`、账本 `refusals` 计数、
本地缓存命中不过门禁、`spend_guard=None` 时既有装配逐字节不变、三类错误分型互斥可辨。
"""

import pytest

from core.billing.budget import (
    AlertLog,
    BudgetLedgerError,
    BudgetRefusedError,
    FileLedger,
    SpendGuard,
    alerts_path,
    ledger_path,
)
from core.llm_gateway.gateway import (
    BackendResult,
    LLMGateway,
    PermanentBackendError,
    TransientBackendError,
    estimate_cost_usd,
)

PRICES = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


class _Backend:
    """记账后端：固定 token 数；可注入厂商侧失败（401/429）。"""

    def __init__(self, *, prompt_tokens: int = 1000, completion_tokens: int = 500, error=None):
        self.call_count = 0
        self._prompt = prompt_tokens
        self._completion = completion_tokens
        self._error = error

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        if self._error is not None:
            raise self._error
        return BackendResult(
            text="伪文本", prompt_tokens=self._prompt, completion_tokens=self._completion
        )


def _guard(cfg, root):
    channel = next(iter(cfg.channels))
    return SpendGuard(
        cfg=cfg,
        channel_id=channel,
        ledger=FileLedger(ledger_path(root, channel), timeout_seconds=1.0),
        alerts=AlertLog(alerts_path(root, channel)),
    )


def _gateway(cfg, root, backend, *, guard=True):
    channel = next(iter(cfg.channels))
    return LLMGateway(
        backend,
        price_book=PRICES,
        sleep=lambda _: None,
        spend_guard=_guard(cfg, root) if guard else None,
        channel_id=channel if guard else "",
    )


def _alerts(cfg, root) -> list[dict]:
    channel = next(iter(cfg.channels))
    return AlertLog(alerts_path(root, channel)).entries()


def _record(cfg, root, tier_id: str) -> dict:
    channel = next(iter(cfg.channels))
    return FileLedger(ledger_path(root, channel), timeout_seconds=1.0).read()["tiers"][tier_id]


class Test六步序列与拒绝语义:
    def test_预估额超余量_拒绝且后端零调用三量不变(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=0.0005, window_kind="day")
        backend = _Backend()
        gateway = _gateway(cfg, billing_root, backend)
        with pytest.raises(BudgetRefusedError) as excinfo:
            gateway.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert excinfo.value.reason == "over_limit"
        assert excinfo.value.remaining_usd == pytest.approx(0.0005)
        assert excinfo.value.estimated_usd == pytest.approx(
            estimate_cost_usd("提示词", PRICES["mock-copy-v1"], 1024)
        )
        # 后端 0 次调用 + 网关三量不变 + 缓存不写（同一 prompt 再来仍要过门禁）
        assert backend.call_count == 0
        assert gateway.call_count == 0 and gateway.total_cost_usd == 0.0
        assert gateway.cost_breakdown() == {}
        with pytest.raises(BudgetRefusedError):
            gateway.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert backend.call_count == 0
        # 原因与预估价落 alerts.jsonl + 账本 refusals 计数与 last_refusal
        refusal = [a for a in _alerts(cfg, billing_root) if a["kind"] == "budget_refused"]
        assert len(refusal) == 2
        assert refusal[0]["detail"]["reason"] == "over_limit"
        assert refusal[0]["detail"]["estimated_usd"] > 0
        record = _record(cfg, billing_root, "screenplay")
        assert record["refusals"] == 2
        assert record["last_refusal"]["estimated_usd"] > 0

    def test_通过时预留与结算落账本且余量自洽(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        backend = _Backend(prompt_tokens=1000, completion_tokens=500)
        gateway = _gateway(cfg, billing_root, backend)
        result = gateway.chat("提示词", model="mock-copy-v1", stage="screenplay", max_tokens=100)
        expected = 1000 / 1000 * 0.001 + 500 / 1000 * 0.002
        assert result.cost_usd == pytest.approx(expected)
        record = _record(cfg, billing_root, "screenplay")
        assert record["spent_usd"] == pytest.approx(expected)
        assert record["reserved_usd"] == 0.0 and record["pending"] == []  # 已结算
        assert record["refusals"] == 0
        assert gateway.total_cost_usd == pytest.approx(expected) and gateway.call_count == 1

    def test_本地缓存命中不过门禁且零成本零后端调用(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        backend = _Backend(prompt_tokens=100, completion_tokens=50)
        gateway = _gateway(cfg, billing_root, backend)
        first = gateway.chat("同一提示词", model="mock-copy-v1", stage="screenplay", max_tokens=50)
        ledger_after_first = _record(cfg, billing_root, "screenplay")
        second = gateway.chat("同一提示词", model="mock-copy-v1", stage="screenplay", max_tokens=50)
        assert second.cached is True and second.cost_usd == 0.0
        assert backend.call_count == 1 and gateway.call_count == 1
        assert _record(cfg, billing_root, "screenplay") == ledger_after_first  # 不占额、不再入账
        assert gateway.total_cost_usd == pytest.approx(first.cost_usd)

    def test_缺_stage_即拒绝_tier_undeclared(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        backend = _Backend()
        gateway = _gateway(cfg, billing_root, backend)
        with pytest.raises(BudgetRefusedError) as excinfo:
            gateway.chat("提示词", model="mock-copy-v1")  # 未声明环节
        assert excinfo.value.reason == "tier_undeclared"
        assert backend.call_count == 0 and gateway.call_count == 0
        # 未声明档不写账本（事实由 alerts 承载），不污染档位计数
        assert (
            "screenplay"
            not in FileLedger(
                ledger_path(billing_root, next(iter(cfg.channels))), timeout_seconds=1.0
            ).read()["tiers"]
        )
        assert any(
            a["kind"] == "budget_refused" and a["detail"]["reason"] == "tier_undeclared"
            for a in _alerts(cfg, billing_root)
        )

    def test_实测超预估_余量为负_告警且后续拒绝不回滚(self, budget_config_factory, billing_root):
        # 额度定得比**实测**小、比**预估**大：调用放行 → 实测超预估 ⇒ 余量为负
        cfg = budget_config_factory(tier_limit_usd=0.0015, window_kind="day")
        backend = _Backend(prompt_tokens=1000, completion_tokens=500)
        gateway = _gateway(cfg, billing_root, backend)
        result = gateway.chat("短提示", model="mock-copy-v1", stage="screenplay", max_tokens=10)
        created = gateway.estimate_cost("短提示", model="mock-copy-v1", max_tokens=10)
        assert result.cost_usd > created  # 上界估算仍可能被超（已如实登记）
        record = _record(cfg, billing_root, "screenplay")
        assert record["spent_usd"] == pytest.approx(result.cost_usd)
        assert record["limit_usd"] - record["spent_usd"] - record["reserved_usd"] < 0  # 余量为负
        over = [a for a in _alerts(cfg, billing_root) if a["kind"] == "over_limit"]
        assert over and over[0]["detail"]["remaining_usd"] < 0
        # 已发生花费如实入账（不回滚）；后续调用被拒
        assert gateway.total_cost_usd == pytest.approx(result.cost_usd)
        with pytest.raises(BudgetRefusedError):
            gateway.chat("再来一次", model="mock-copy-v1", stage="screenplay", max_tokens=10)


class Test未注入守卫与分型互斥:
    def test_spend_guard_None_时既有装配逐字节不变(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        plain = LLMGateway(_Backend(), price_book=PRICES, sleep=lambda _: None)
        guarded = _gateway(cfg, billing_root, _Backend())
        args = dict(model="mock-copy-v1", temperature=0.0, max_tokens=100)
        a = plain.chat("提示词", **args)
        b = guarded.chat("提示词", stage="screenplay", **args)
        assert (a.text, a.usage, a.cost_usd, a.cached, a.role, a.profile_id, a.cell_key) == (
            b.text,
            b.usage,
            b.cost_usd,
            b.cached,
            b.role,
            b.profile_id,
            b.cell_key,
        )
        assert plain.cost_breakdown() == guarded.cost_breakdown()
        assert plain.total_cost_usd == guarded.total_cost_usd

    def test_三类错误分型互斥且_kind_可辨(self, budget_config_factory, billing_root):
        refused_cfg = budget_config_factory(tier_limit_usd=0.0005, window_kind="day")
        refused = _gateway(refused_cfg, billing_root, _Backend())
        with pytest.raises(BudgetRefusedError):
            refused.chat("提示词", model="mock-copy-v1", stage="screenplay")
        # 401（认证失败）与 429（限流）各自分型：额度充足故走**后端**失败面（都不是拒绝）
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        permanent = _gateway(
            cfg, billing_root, _Backend(error=PermanentBackendError("后端拒绝请求（401）"))
        )
        with pytest.raises(PermanentBackendError):
            permanent.chat("提示词", model="mock-copy-v1", stage="screenplay")
        transient = _gateway(
            cfg, billing_root, _Backend(error=TransientBackendError("后端限流（429）"))
        )
        with pytest.raises(TransientBackendError):
            transient.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert not issubclass(BudgetRefusedError, PermanentBackendError)
        assert not issubclass(BudgetRefusedError, TransientBackendError)
        assert not issubclass(PermanentBackendError, BudgetRefusedError)
        kinds = {a["kind"] for a in _alerts(cfg, billing_root)}
        assert kinds == {"budget_refused"}  # 厂商侧失败不落门禁告警（分型互斥）

    def test_账本锁超时即拒绝调用(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        channel = next(iter(cfg.channels))
        ledger = FileLedger(ledger_path(billing_root, channel), timeout_seconds=0.05)
        import contextlib
        import fcntl
        import os

        ledger.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(ledger.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            with pytest.raises(BudgetLedgerError, match="锁超时"):
                ledger.update(lambda payload: payload.setdefault("tiers", {}))
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)
        assert contextlib  # 保持导入语义（上下文管理器风格与其他用例一致）
