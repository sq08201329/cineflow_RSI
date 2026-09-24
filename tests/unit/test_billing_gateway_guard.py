"""网关门禁注入点单测（功能 019 / T1919；契约 C9 / C10）。

网关侧只做三件事：**固定六步序列**、把预估价（唯一估算实现）填进 `SpendRequest`、
把拒绝原样上抛（不吞、不改写三量）。调用点侧的零成本分支属 T1950/T1951（US1），不在本文件。

`spend_guard=None`（默认）时行为与改造前逐字节一致——既有网关用例全绿即为该断言的常驻形态。
"""

import pathlib

import pytest

from core.billing.budget import (
    AlertLog,
    BudgetRefusedError,
    FileLedger,
    SpendGuard,
    alerts_path,
    ledger_path,
)
from core.llm_gateway.gateway import (
    BackendResult,
    GatewayError,
    LLMGateway,
    PermanentBackendError,
    TransientBackendError,
    estimate_cost_usd,
)
from core.llm_gateway.profiles import load_or_migrate
from core.llm_gateway.routing import Role

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PRICES = {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}


class _Backend:
    def __init__(self, *, prompt_tokens: int = 1000, completion_tokens: int = 500, error=None):
        self.call_count = 0
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.error = error

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return BackendResult(
            text="伪文本",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


class _Reservation:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def settle(self, actual_usd: float) -> dict:
        self._sink.append(actual_usd)
        return {}


class _RecordingGuard:
    """记录型守卫（结构化满足 `SpendGuard` 协议：`check(request) -> Reservation`）。"""

    channel_id = "channel-fixture"

    def __init__(self, *, refuse: bool = False, adjustment: dict | None = None) -> None:
        self.refuse = refuse
        self.adjustment = dict(adjustment or {})
        self.requests: list = []
        self.settled: list[float] = []
        self.sequence: list[str] = []

    def check(self, request):
        self.requests.append(request)
        self.sequence.append("check")
        if self.refuse:
            raise BudgetRefusedError(
                "预算拒绝（over_limit）：预估价超过余量——拒绝调用，成本零入账",
                reason="over_limit",
                tier_id=getattr(request, "stage", ""),
                remaining_usd=0.0,
                estimated_usd=request.estimated_usd,
            )
        return _Reservation(self.settled)


def _gateway(backend, *, guard=None, channel_id="channel-fixture", profiles=None):
    if profiles is None:
        profiles = load_or_migrate(
            {
                "screenplay": {
                    "model": "mock-copy-v1",
                    "model_prices": {"mock-copy-v1": dict(_PRICES)},
                }
            }
        )
    return LLMGateway(
        backend,
        price_book={"mock-copy-v1": dict(_PRICES)},
        sleep=lambda _: None,
        profiles=profiles,
        spend_guard=guard,
        channel_id=channel_id,
    )


class Test固定六步序列:
    def test_门禁在取价之后后端调用之前且settle拿到实测(self):
        guard = _RecordingGuard()
        backend = _Backend()
        gateway = _gateway(backend, guard=guard)
        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert len(guard.requests) == 1 and backend.call_count == 1
        # ③ 在 ④ 之前：守卫先被问，后端才被调（预估额由网关填）
        assert guard.sequence == ["check"]
        assert guard.requests[0].estimated_usd == pytest.approx(
            estimate_cost_usd("提示词", _PRICES, 1024)
        )
        # ⑥ settle 拿到**实测**（网关记账值），与入账一致
        assert guard.settled == [pytest.approx(result.cost_usd)]
        assert result.cost_usd == pytest.approx(1000 / 1000 * 0.001 + 500 / 1000 * 0.002)

    def test_请求三字段由网关填(self):
        guard = _RecordingGuard()
        gateway = _gateway(_Backend(), guard=guard)
        gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        request = guard.requests[0]
        assert request.channel_id == "channel-fixture"  # 渠道 id 来自装配声明
        assert request.stage == "screenplay"  # 环节 id 由调用点声明
        assert request.estimated_usd > 0

    def test_预估额取自唯一估算实现(self):
        """估算额 = 网关的 `estimate_cost_usd`（prompt `len//2` + 输出满额），守卫不自行估算。"""
        guard = _RecordingGuard()
        gateway = _gateway(_Backend(), guard=guard)
        gateway.chat("提示词", role=Role.GENERATION, stage="screenplay", max_tokens=256)
        estimated = guard.requests[0].estimated_usd
        assert estimated == pytest.approx(
            max(1, len("提示词") // 2) / 1000 * 0.001 + 256 / 1000 * 0.002
        )
        # 与公开估算方法同源（调用点薄调用它）
        assert estimated == pytest.approx(
            gateway.estimate_cost("提示词", role=Role.GENERATION, max_tokens=256)
        )

    def test_实测超预估_如实入账且后续由守卫拒绝(self):
        guard = _RecordingGuard()
        backend = _Backend(prompt_tokens=100_000, completion_tokens=50_000)
        gateway = _gateway(backend, guard=guard)
        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert result.cost_usd > guard.requests[0].estimated_usd  # 实测超预估
        assert guard.settled == [pytest.approx(result.cost_usd)]  # 如实结算，不回滚
        assert gateway.total_cost_usd == pytest.approx(result.cost_usd)


class Test拒绝语义:
    def test_拒绝原样上抛_三量不变_后端零调用_缓存不写(self):
        guard = _RecordingGuard(refuse=True)
        backend = _Backend()
        gateway = _gateway(backend, guard=guard)
        with pytest.raises(BudgetRefusedError) as excinfo:
            gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert excinfo.value.reason == "over_limit"
        assert gateway.call_count == 0
        assert gateway.total_cost_usd == 0.0
        assert gateway.cost_breakdown() == {}
        assert backend.call_count == 0
        # 缓存不写：同一 prompt 再调仍要过门禁（若写了缓存就会"命中零成本"绕开）
        with pytest.raises(BudgetRefusedError):
            gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert len(guard.requests) == 2 and backend.call_count == 0

    def test_拒绝类型与厂商失败分型互斥(self):
        assert issubclass(BudgetRefusedError, GatewayError)
        assert not issubclass(BudgetRefusedError, PermanentBackendError)
        assert not issubclass(BudgetRefusedError, TransientBackendError)
        # 厂商 401/429 各自的类型不被拒绝类型吞掉（各自 kind 可辨）
        gateway = _gateway(_Backend(error=PermanentBackendError("后端拒绝请求（401）")), guard=None)
        with pytest.raises(PermanentBackendError):
            gateway.chat("提示词", role=Role.GENERATION)
        assert gateway.call_count == 0

    def test_缺_stage_即拒绝tier_undeclared(self, budget_config_factory, billing_root):
        """真守卫（`core/billing.budget.SpendGuard`）结构化接入：缺环节声明即拒绝。"""
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        channel = next(iter(cfg.channels))
        guard = SpendGuard(
            cfg=cfg,
            channel_id=channel,
            ledger=FileLedger(
                ledger_path(billing_root, channel),
                timeout_seconds=cfg.ledger["lock_timeout_seconds"],
            ),
            alerts=AlertLog(alerts_path(billing_root, channel)),
        )
        gateway = LLMGateway(
            _Backend(),
            price_book={"mock-copy-v1": dict(_PRICES)},
            sleep=lambda _: None,
            spend_guard=guard,
            channel_id=channel,
        )
        with pytest.raises(BudgetRefusedError) as excinfo:
            gateway.chat("提示词", model="mock-copy-v1")  # 未声明 stage=
        assert excinfo.value.reason == "tier_undeclared"
        assert gateway.call_count == 0

    def test_真守卫的预留与结算落到账本(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        channel = next(iter(cfg.channels))
        ledger = FileLedger(
            ledger_path(billing_root, channel), timeout_seconds=cfg.ledger["lock_timeout_seconds"]
        )
        guard = SpendGuard(
            cfg=cfg,
            channel_id=channel,
            ledger=ledger,
            alerts=AlertLog(alerts_path(billing_root, channel)),
        )
        expensive = {"mock-copy-v1": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0}}
        backend = _Backend(prompt_tokens=100, completion_tokens=50)
        gateway = LLMGateway(
            backend,
            price_book=expensive,
            sleep=lambda _: None,
            spend_guard=guard,
            channel_id=channel,
        )
        result = gateway.chat("x" * 100, model="mock-copy-v1", stage="screenplay", max_tokens=100)
        record = ledger.read()["tiers"]["screenplay"]
        assert record["spent_usd"] == pytest.approx(result.cost_usd)
        assert record["reserved_usd"] == 0.0 and record["pending"] == []
        # 超限（预估额 > 余量）即拒绝：后端零调用
        backend.call_count = 0
        with pytest.raises(BudgetRefusedError) as excinfo:
            gateway.chat("x" * 100_000, model="mock-copy-v1", stage="screenplay", max_tokens=100)
        assert excinfo.value.reason == "over_limit"
        assert backend.call_count == 0 and gateway.call_count == 1


class Test未注入守卫:
    def test_默认_None_时行为与改造前一致(self):
        backend = _Backend()
        gateway = LLMGateway(
            backend, price_book={"mock-copy-v1": dict(_PRICES)}, sleep=lambda _: None
        )
        result = gateway.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert result.cost_usd == pytest.approx(1000 / 1000 * 0.001 + 500 / 1000 * 0.002)
        assert gateway.call_count == 1 and backend.call_count == 1

    def test_注入守卫但缺渠道_id_即装配期拒绝(self):
        with pytest.raises(GatewayError, match="channel_id"):
            LLMGateway(
                _Backend(),
                price_book={"mock-copy-v1": dict(_PRICES)},
                sleep=lambda _: None,
                spend_guard=_RecordingGuard(),
            )


class Test预估口径唯一实现:
    def test_估算公式只在_core_llm_gateway_内出现一处(self):
        """T1921 收敛断言：两处第二份（剧本线/开发线）已改为薄调用，不留第二份公式。"""
        formula = "len(prompt) // 2"
        for relative in ("agents/screenplay/loop.py", "agents/dev/loop.py"):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert formula not in source, f"{relative} 仍留有第二份估算公式"
            assert "_estimate_cost" not in source, f"{relative} 仍自定义估算函数"
            assert "estimate_cost" in source, f"{relative} 未改为薄调用网关估算"
        gateway_source = (REPO_ROOT / "core/llm_gateway/gateway.py").read_text(encoding="utf-8")
        assert gateway_source.count("prompt_tokens = max(1, len(prompt) // 2)") == 1  # 唯一实现
        # 模拟后端的 usage 生成用途不同（造伪 usage，非成本上界）：不并入
        mock_source = (REPO_ROOT / "core/llm_gateway/backends/mock.py").read_text(encoding="utf-8")
        assert formula in mock_source
