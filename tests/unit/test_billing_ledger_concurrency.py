"""跨进程账本与预留—结算（功能 019 / US1 / T1925）：契约 C11。

- **两进程并发抢同一档** ⇒ 总入账 ≤ `limit_usd`、`revision` 单调、无丢失更新（预留使并发不超额）；
- `check` 时 `reserved += estimated`；`settle` 时 `reserved -= estimated; spent += actual`；
  余量 = `limit − spent − reserved`；
- 锁被占满超时 ⇒ `BudgetLedgerError` 拒绝调用（不无锁写、不静默放行）；
- 崩溃残留预留 ⇒ 如实列出（`reserved_usd > 0` 且有未结算标记），**不自动清零**；
- 窗口滚动（`run`/`day`/`period` 键变更即归零，旧窗口记录保留在 `history` 里可审计）。

**登记边界（如实）**：本账本为**单主机多进程安全**；多主机并发共享额度需换 PG 行锁/事务（未做）。
"""

import datetime as dt
import json

import pytest

from core.billing.budget import (
    AlertLog,
    BudgetLedgerError,
    FileLedger,
    SpendGuard,
    alerts_path,
    ledger_path,
    tiers_of,
)

_DAY = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)


class _Req:
    def __init__(self, channel_id, stage, estimated_usd):
        self.channel_id = channel_id
        self.stage = stage
        self.estimated_usd = estimated_usd


def _guard(cfg, root, *, window_context=None):
    channel = next(iter(cfg.channels))
    ledger = FileLedger(ledger_path(root, channel), timeout_seconds=1.0)
    return (
        SpendGuard(
            cfg=cfg,
            channel_id=channel,
            ledger=ledger,
            alerts=AlertLog(alerts_path(root, channel)),
            window_context=window_context,
            clock=lambda: _DAY,
        ),
        ledger,
        channel,
    )


class Test预留与结算:
    def test_预留占额_结算按实测结清(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        guard, ledger, channel = _guard(cfg, billing_root)
        reservation = guard.check(_Req(channel, "screenplay", 0.4))
        record = ledger.read()["tiers"]["screenplay"]
        assert record["reserved_usd"] == pytest.approx(0.4)  # 预留占用
        assert record["spent_usd"] == 0.0
        assert record["pending"] and record["pending"][0]["estimated_usd"] == pytest.approx(0.4)
        record = reservation.settle(0.35)
        assert record["reserved_usd"] == 0.0  # reserved -= estimated
        assert record["spent_usd"] == pytest.approx(0.35)  # spent += actual
        assert record["pending"] == []
        limit = record["limit_usd"]
        assert limit - record["spent_usd"] - record["reserved_usd"] == pytest.approx(0.65)

    def test_重复结算拒绝且不静默改写余量(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        guard, _, channel = _guard(cfg, billing_root)
        reservation = guard.check(_Req(channel, "screenplay", 0.2))
        reservation.settle(0.2)
        with pytest.raises(BudgetLedgerError, match="重复结算"):
            reservation.settle(0.2)

    def test_崩溃残留预留如实列出不自动清零(self, budget_config_factory, billing_root):
        """预留后不结算（进程崩溃的等价形态）：账本如实留痕，后续读取仍可见。"""
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        guard, ledger, channel = _guard(cfg, billing_root)
        guard.check(_Req(channel, "screenplay", 0.3))  # 不 settle
        record = ledger.read()["tiers"]["screenplay"]
        assert record["reserved_usd"] == pytest.approx(0.3) and record["pending"]
        # 另起一个守卫（模拟新进程）读同一账本：残留仍在（不静默清零）
        _, fresh_ledger, _ = _guard(cfg, billing_root)
        assert fresh_ledger.read()["tiers"]["screenplay"]["pending"]
        # 余量仍被残留预留占住（后续调用按此判定）
        from core.billing.budget import BudgetRefusedError

        with pytest.raises(BudgetRefusedError):
            guard.check(_Req(channel, "screenplay", 0.9))

    def test_窗口滚动归零且旧窗口入_history(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="run")
        guard_a, ledger, channel = _guard(cfg, billing_root, window_context={"run": "run-a"})
        guard_a.check(_Req(channel, "screenplay", 0.6)).settle(0.6)
        assert ledger.read()["tiers"]["screenplay"]["spent_usd"] == pytest.approx(0.6)
        guard_b, _, _ = _guard(cfg, billing_root, window_context={"run": "run-b"})
        guard_b.check(_Req(channel, "screenplay", 0.2))
        record = ledger.read()["tiers"]["screenplay"]
        assert record["window_key"] == ["run", "run-b"]
        assert record["spent_usd"] == 0.0 and record["reserved_usd"] == pytest.approx(0.2)
        assert record["history"][-1]["window_key"] == ["run", "run-a"]
        assert record["history"][-1]["spent_usd"] == pytest.approx(0.6)  # 旧窗口记录保留可审计

    def test_锁被占用超时即拒绝(self, budget_config_factory, billing_root, billing_ledger_lock):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        _, ledger, _ = _guard(cfg, billing_root)
        ledger.timeout_seconds = 0.05
        with billing_ledger_lock(ledger):
            with pytest.raises(BudgetLedgerError, match="锁超时"):
                ledger.update(lambda payload: payload.setdefault("tiers", {}))


class Test两进程并发:
    _WORKER = """
import datetime as dt, json, sys
from dataclasses import dataclass
from core.billing.budget import (
    AlertLog,
    BudgetConfig,
    FileLedger,
    SpendGuard,
    alerts_path,
    ledger_path,
)


@dataclass(frozen=True)
class Req:
    channel_id: str
    stage: str
    estimated_usd: float


cfg = BudgetConfig.from_dict({"budget": json.loads(open(sys.argv[1], encoding="utf-8").read())})
channel = cfg.channels and next(iter(cfg.channels))
root = sys.argv[2]
guard = SpendGuard(
    cfg=cfg,
    channel_id=channel,
    ledger=FileLedger(ledger_path(root, channel), timeout_seconds=10.0),
    alerts=AlertLog(alerts_path(root, channel)),
    clock=lambda: dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC),
)
ok = refused = 0
for _ in range(10):
    try:
        guard.check(Req(channel, "screenplay", 0.1)).settle(0.1)
        ok += 1
    except Exception:
        refused += 1
print(json.dumps({"ok": ok, "refused": refused}))
"""

    def test_两进程抢同一档_总入账不超额且_revision_单调(
        self, budget_config_factory, billing_root, billing_subprocess_runner, tmp_path
    ):
        cfg = budget_config_factory(tier_limit_usd=0.5, window_kind="day")
        channel = next(iter(cfg.channels))
        billing_root.mkdir(parents=True, exist_ok=True)
        worker = tmp_path / "worker.py"
        worker.write_text(self._WORKER, encoding="utf-8")
        config_file = tmp_path / "budget.json"
        config_file.write_text(
            json.dumps(_budget_section(cfg), ensure_ascii=False), encoding="utf-8"
        )  # noqa: E501
        results = [
            billing_subprocess_runner(
                "import runpy, sys; "
                f"sys.argv = ['w', {str(config_file)!r}, {str(billing_root)!r}]; "
                f"runpy.run_path({str(worker)!r}, run_name='__main__')"
            )
            for _ in range(2)
        ]
        for result in results:
            assert result.returncode == 0, result.stderr
        totals = [json.loads(r.stdout.strip().splitlines()[-1]) for r in results]
        booked = sum(item["ok"] for item in totals) * 0.1
        assert booked <= 0.5 + 1e-9, totals  # 预留使并发不超额
        assert sum(item["refused"] for item in totals) > 0, totals  # 超限者被拒
        payload = FileLedger(ledger_path(billing_root, channel), timeout_seconds=1.0).read()
        record = payload["tiers"]["screenplay"]
        assert record["spent_usd"] <= 0.5 + 1e-9
        assert payload["revision"] >= 20  # 每个进程 10 次尝试，每写 +1（单调、无丢失更新）
        assert payload["updated_at"]


def _budget_section(cfg) -> dict:
    """把夹具配置还原成 `budget:` 段（子进程只依赖配置文件）。

    档位以**旧扁平**形态落给子进程（子进程从该段构造 `BudgetConfig` ⇒ 走 C11 的旧形状归一，
    顺带覆盖兼容读路径）；键集取**本渠道**的档位（020 / C12：按渠道取档）。
    """  # noqa: D401
    channel_id = next(iter(cfg.channels))
    return {
        "channels": {
            channel_id: {
                "adapter": cfg.channel(channel_id).adapter,
                "bill": {
                    "format": cfg.channel(channel_id).bill.format_id,
                    "fetch": cfg.channel(channel_id).bill.fetch,
                    "columns": dict(cfg.channel(channel_id).bill.columns),
                    "classification": {
                        "line_kind": dict(cfg.channel(channel_id).bill.line_kind_classes),
                        "amount_sign": dict(cfg.channel(channel_id).bill.amount_signs),
                    },
                },
            }
        },
        "tiers": {
            tier_id: {
                "limit_usd": tier.limit_usd,
                "window": {"kind": tier.window_kind},
                "on_exhausted": "refuse",
                "note": tier.note,
            }
            for tier_id, tier in tiers_of(cfg, channel_id).items()
        },
        "peak_windows": {
            "timezone": cfg.peak_windows.timezone,
            "attribution": cfg.peak_windows.attribution,
            "windows": [window.to_snapshot() for window in cfg.peak_windows.windows],
        },
        "calibration": dict(cfg.calibration),
        "reconcile": dict(cfg.reconcile),
        "ledger": dict(cfg.ledger),
        "runs": dict(cfg.runs),
    }
