"""磁盘布局机检（功能 019 / 契约 C4；T1907 先于实现编写）。

六种产物（`bills/{bill_id}.json` / `reports/{period}.json` / `calibrations/{calibration_id}.json`
/ `runs/{date}.json` / `ledger.json` / `alerts.jsonl`，均在 `billing/{channel}/` 下）的
**路径口径**与**同键拒重产**：

- 根可配（`budget.ledger.root`，相对路径落在**仓库根**，绝对路径原样使用）；按渠道分目录，
  单渠道产物不跨目录写；缺失目录自动创建；
- `{bill_id}`/`{period}`/`{calibration_id}` 取自声明值；`{date}` = `peak_windows.timezone`
  的**本地日期**（渠道日历单点：峰谷判定、额度 day 窗口与运行记录日期共用它）；
- 一次性快照同键重产被拒；两渠道同周期互不覆盖；
- 产物只落 `billing/{channel}/` 下，**不落在** `pilot/`、`deployment/`、`calibration/` 等既有目录。

本文件对五模块发**真实调用**（账本/告警/校准记录/运行记录各落一次盘），不是纯字符串比较：
路径口径若与实现漂移，这里会红。
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.billing import bill, budget, calibration, reconcile, runlog

REPO_ROOT = Path(__file__).resolve().parents[2]
# UTC 17:30 在上海已是次日：用来钉住"日期按渠道日历取，不按 UTC"
_CROSS_MIDNIGHT = dt.datetime(2026, 9, 22, 17, 30, tzinfo=dt.UTC)  # 上海本地 2026-09-23


@dataclass(frozen=True)
class _Request:
    """门禁请求的最小结构化替身（网关侧的 `SpendRequest` 同形：三个属性）。"""

    channel_id: str
    stage: str
    estimated_usd: float


def _alerts(root, channel_id):
    """该渠道的告警留痕写手（门禁拒绝路径必须落盘，故 guard 构造要求它非 None）。"""
    return budget.AlertLog(budget.alerts_path(root, channel_id))


def _guard(cfg, root, channel_id, *, alerts, window_context=None):
    return budget.SpendGuard(
        cfg=cfg,
        channel_id=channel_id,
        ledger=budget.FileLedger(
            budget.ledger_path(root, channel_id),
            timeout_seconds=cfg.ledger["lock_timeout_seconds"],
        ),
        alerts=alerts,
        window_context=window_context,
        clock=lambda: _CROSS_MIDNIGHT,
    )


class Test路径派生:
    def test_根可配_相对路径落仓库根_绝对路径原样(self, tmp_path):
        assert budget.billing_root("billing") == REPO_ROOT / "billing"
        assert budget.billing_root(Path("billing")) == REPO_ROOT / "billing"
        nested = tmp_path / "elsewhere" / "billing"
        assert budget.billing_root(nested) == nested

    def test_渠道分目录与六种产物文件名(self, tmp_path):
        root = tmp_path / "billing"
        channel = root / "ch"
        assert budget.channel_dir(root, "ch") == channel
        assert bill.bill_path(root, "ch", "bill-2026-09") == channel / "bills" / "bill-2026-09.json"
        assert reconcile.report_path(root, "ch", "2026-09") == channel / "reports" / "2026-09.json"
        assert (
            calibration.calibration_path(root, "ch", "cal-1")
            == channel / "calibrations" / "cal-1.json"
        )
        assert runlog.run_path(root, "ch", "2026-09-23") == channel / "runs" / "2026-09-23.json"
        assert budget.ledger_path(root, "ch") == channel / "ledger.json"
        assert budget.alerts_path(root, "ch") == channel / "alerts.jsonl"

    def test_运行记录日期取渠道日历的本地日期(self, budget_config_factory):
        """UTC 2026-09-22T17:30 在上海是 2026-09-23：日期不按 UTC 取（三处口径同一点）。"""
        cfg = budget_config_factory()
        assert cfg.local_date(_CROSS_MIDNIGHT) == "2026-09-23"
        assert budget.peak_windows_snapshot(cfg)["timezone"] == cfg.peak_windows.timezone

    def test_默认根不与既有产物目录同名(self, budget_config_factory):
        cfg = budget_config_factory()
        name = budget.billing_root(cfg.ledger["root"]).name
        assert name == "billing"
        assert name not in {"pilot", "deployment", "calibration", "policy", "replay"}


class Test落盘与同键拒重产:
    def test_账本与告警落渠道目录(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        channel = next(iter(cfg.channels))
        alerts = budget.AlertLog(budget.alerts_path(billing_root, channel))
        guard = _guard(cfg, billing_root, channel, alerts=alerts)
        reservation = guard.check(_Request(channel, "screenplay", 0.25))
        reservation.settle(0.2)
        alerts.record(
            kind="over_limit",
            at="2026-09-23T00:00:00+00:00",
            channel_id=channel,
            period="2026-09",
            detail={"remaining_usd": -0.01},
            ref=reservation.reservation_id,
        )
        # 缺失目录自动创建（渠道路径由实现拼装，不经测试代劳）
        assert budget.ledger_path(billing_root, channel).is_file()
        assert budget.alerts_path(billing_root, channel).is_file()
        assert budget.ledger_path(billing_root, channel).parent == billing_root / channel

    def test_两渠道同周期互不覆盖(self, billing_channels_payload_factory, billing_root):
        # 020（C11）：档位在**渠道内**（`channels.<id>.tiers.<环节>`）；两渠道各一份独立额度
        section = billing_channels_payload_factory(tier_limit_usd=1.0, window_kind="day")
        first, second = "llm", "channel-iso"
        cfg = budget.BudgetConfig.from_dict({"budget": section})
        for name, amount in ((first, 0.3), (second, 0.7)):
            guard = _guard(cfg, billing_root, name, alerts=_alerts(billing_root, name))
            guard.check(_Request(name, "screenplay", amount)).settle(amount)
        first_ledger = budget.ledger_path(billing_root, first)
        second_ledger = budget.ledger_path(billing_root, second)
        assert first_ledger != second_ledger and first_ledger.is_file() and second_ledger.is_file()
        assert (
            budget.FileLedger(first_ledger, timeout_seconds=1.0).read()["tiers"]
            != (budget.FileLedger(second_ledger, timeout_seconds=1.0).read()["tiers"])
        )
        # 同一档位名在渠道内可见、**不跨渠道串用**：两本账各自的档位表互不影响
        assert cfg.tiers_of(first)["screenplay"].limit_usd == 1.0
        assert cfg.tiers_of(second)["screenplay"].limit_usd == 1.0

    def test_多渠道加旧扁平形状即歧义报错(self, billing_budget_factory):
        """C11：旧扁平 `tiers` 只在**恰好一个**渠道时可归一；多渠道 ⇒ 歧义报错（不静默择一）。"""
        section = billing_budget_factory(tier_limit_usd=1.0, window_kind="day")
        declared = section["channels"]
        first = next(iter(declared))
        section["channels"] = {
            first: declared[first],
            "channel-iso": dict(declared[first]),
        }  # 顶层旧扁平 `tiers` 仍在 ⇒ 两个来源归属歧义
        with pytest.raises(budget.BudgetConfigError, match="歧义"):
            budget.BudgetConfig.from_dict({"budget": section})

    def test_新旧两处档位并存即报错(self, billing_channels_payload_factory):
        """C11：既有顶层扁平 `tiers`、又有渠道 `tiers` ⇒ 报错（两个来源，不静默择一）。"""
        section = billing_channels_payload_factory(channels=("llm",))
        section["tiers"] = {"screenplay": {"limit_usd": 1.0, "window": {"kind": "day"}}}
        with pytest.raises(budget.BudgetConfigError, match="同时出现"):
            budget.BudgetConfig.from_dict({"budget": section})

    def test_校准记录同键重产被拒(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        first = calibration.record_calibration(
            "cal-2026-09-23",
            cfg=cfg,
            channel_id=channel,
            tier_id="screenplay",
            prices_snapshot={"profiles": {"p-1": {"prices": {"prompt_per_1k": 1.0}}}},
            sample_count=3,
            measured_cost_usd=4.8,
            expected_cost_usd=5.0,
            root=billing_root,
            note="夹具：最小规模单轮",
            at=_CROSS_MIDNIGHT,
        )
        assert first.deviation == pytest.approx(-0.04)
        assert calibration.calibration_path(billing_root, channel, "cal-2026-09-23").is_file()
        with pytest.raises(calibration.CalibrationRecordError):
            calibration.record_calibration(
                "cal-2026-09-23",
                cfg=cfg,
                channel_id=channel,
                tier_id="screenplay",
                prices_snapshot={},
                sample_count=3,
                measured_cost_usd=1.0,
                expected_cost_usd=1.0,
                root=billing_root,
                at=_CROSS_MIDNIGHT,
            )

    def test_运行记录当日封存后追加拒绝(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        date = cfg.local_date(_CROSS_MIDNIGHT)
        runlog.append_run(
            channel,
            cfg=cfg,
            root=billing_root,
            moment=_CROSS_MIDNIGHT,
            stage="screenplay",
            source="real",
            adapter_ref="pilot_llm",
            profile_id="p-1",
            result="ok",
            cost_source="gateway_accounting",
        )
        path = runlog.run_path(billing_root, channel, date)
        assert path.is_file()
        runlog.seal_run(date, channel_id=channel, root=billing_root)
        with pytest.raises(runlog.RunLogError):
            runlog.append_run(
                channel,
                cfg=cfg,
                root=billing_root,
                moment=_CROSS_MIDNIGHT,
                stage="screenplay",
                source="real",
            )

    def test_产物只落_billing_根内(self, budget_config_factory, billing_root, tmp_path):
        """既有产物目录（pilot/deployment/calibration）零新增：本特性的写入面只有 billing/。"""
        existing = {
            name: {path.name for path in (REPO_ROOT / name).rglob("*")}
            for name in ("pilot", "deployment", "calibration")
            if (REPO_ROOT / name).is_dir()
        }
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        channel = next(iter(cfg.channels))
        guard = _guard(cfg, billing_root, channel, alerts=_alerts(billing_root, channel))
        guard.check(_Request(channel, "screenplay", 0.1)).settle(0.1)
        calibration.record_calibration(
            "cal-paths",
            cfg=cfg,
            channel_id=channel,
            tier_id="screenplay",
            prices_snapshot={},
            sample_count=3,
            measured_cost_usd=1.0,
            expected_cost_usd=1.0,
            root=billing_root,
            at=_CROSS_MIDNIGHT,
        )
        runlog.append_run(
            channel,
            cfg=cfg,
            root=billing_root,
            moment=_CROSS_MIDNIGHT,
            stage="screenplay",
            source="real",
        )
        produced = sorted(
            path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
        )
        assert produced, "本次落盘面为空（路径夹具失效）"
        assert all(line.startswith("billing/") for line in produced), produced
        for name, before in existing.items():
            assert {path.name for path in (REPO_ROOT / name).rglob("*")} == before


class Test账单与报告路径取自声明值:
    def test_账单可解析且文件名取自声明值(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        spec = cfg.channel(channel)
        normalized = bill.normalize_bill(
            billing_bill_fixture["text"],
            channel_id=channel,
            bill_id=billing_bill_fixture["bill_id"],
            period=billing_bill_fixture["period"],
            currency=billing_bill_fixture["currency"],
            source="export",
            fmt=spec.bill.format_id,
            columns=spec.bill.columns,
        )
        assert len(normalized.entries) == len(billing_bill_fixture["rows"])
        assert normalized.period == billing_bill_fixture["period"]
        assert bill.bill_path(billing_root, channel, normalized.bill_id).name == (
            f"{billing_bill_fixture['bill_id']}.json"
        )
        assert bill.bill_path(billing_root, channel, normalized.bill_id).parent.name == "bills"
        report = reconcile.report_path(billing_root, channel, normalized.period)
        assert report.name == f"{billing_bill_fixture['period']}.json"
        assert report.parent.name == "reports"
