"""告警门禁（功能 019 / US2 / T1934）：契约 C14 + C16 的退出码语义。

- 告警件：`billing/{channel}/alerts.jsonl` **只追加**；字段 `kind`/`at`/`channel_id`/`period`/
  `detail`/`ref`；`kind` 取值域固定六值（域外即报错，不自造类型）；
- 判定信号在 **core**（`ReconciliationReport.has_alerts()`）；CLI 只据此选退出码：
  有告警 ⇒ 1、无告警 ⇒ 0、用法/配置错误 ⇒ 2——供定时工作流"非零退出即告警"；
- 引用完整性：报告含未解释项却返回 0 / 省略未解释项 / 缺 `alerts` 落盘 ⇒ 红。
"""

import json
from pathlib import Path

import pytest
import yaml

from core.billing.bill import normalize_bill
from core.billing.budget import ALERT_KINDS, AlertLog, BudgetLedgerError, alerts_path
from core.billing.reconcile import reconcile, save_report

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs" / "movie.yaml"


def _report(cfg, channel, fixture, ledger):
    spec = cfg.channel(channel).bill
    bill = normalize_bill(
        fixture["text"],
        channel_id=channel,
        bill_id=fixture["bill_id"],
        period=fixture["period"],
        currency=fixture["currency"],
        source="export",
        fmt=spec.format_id,
        columns=spec.columns,
    )
    return reconcile(
        fixture["period"], channel_id=channel, gateway_ledger=ledger, bill=bill, cfg=cfg
    )


def _config_file(tmp_path: Path, **reconcile_overrides) -> Path:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    payload["budget"]["reconcile"].update(reconcile_overrides)
    target = tmp_path / "movie.yaml"
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


class Test告警件结构:
    def test_只追加且字段齐备(self, billing_root, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        log = AlertLog(alerts_path(billing_root, channel))
        first = log.record(
            kind="unexplained_delta",
            at="2026-09-30T00:00:00+00:00",
            channel_id=channel,
            period="2026-09",
            detail={"unexplained": ["a:unclassified"]},
            ref="llm:2026-09",
        )
        second = log.record(
            kind="over_limit",
            at="2026-09-30T01:00:00+00:00",
            channel_id=channel,
        )
        assert set(first) == {"kind", "at", "channel_id", "period", "detail", "ref"}
        entries = log.entries()
        assert [entry["kind"] for entry in entries] == ["unexplained_delta", "over_limit"]
        assert entries[0]["ref"] == "llm:2026-09" and entries[1]["detail"] == {}
        assert second["period"] == ""  # 缺省空串（字段齐备，不留 None）
        raw = alerts_path(billing_root, channel).read_text(encoding="utf-8").splitlines()
        assert len(raw) == 2 and all(json.loads(line) for line in raw)  # JSONL 只增

    def test_kind_取值域固定六值(self, billing_root, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        log = AlertLog(alerts_path(billing_root, channel))
        assert set(ALERT_KINDS) == {
            "budget_refused",
            "over_limit",
            "unexplained_delta",
            "delta_over_threshold",
            "tier_raised",
            "uncalibrated_raise",
        }
        with pytest.raises(BudgetLedgerError, match="kind"):
            log.record(kind="made_up_kind", at="2026-09-30T00:00:00+00:00", channel_id=channel)
        with pytest.raises(BudgetLedgerError, match="渠道"):
            log.record(kind="over_limit", at="2026-09-30T00:00:00+00:00", channel_id="")


class Test判定信号:
    def test_有未解释项即有告警信号(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        assert report.unexplained and report.has_alerts() is True
        assert "unexplained_delta" in report.alerts

    def test_无告警时信号为空(self, budget_config_factory):
        """干净报告 ⇒ `has_alerts()` 为空（否则门禁会永远红，等于没有门禁）。"""
        from dataclasses import replace

        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        spec = cfg.channel(channel).bill
        # 账单与网关账目**逐档案一致**：计费口径零差异、无未分类项
        text = (
            "entry_id,amount,currency,period,line_kind,amount_sign,model_ref,"
            "fx_rate,fx_source,fx_at,note\n"
            'b-1,2.00,USD,2026-09,usage,charge,deepseek-flash,,,,"与网关记账一致"\n'
        )
        bill = normalize_bill(
            text,
            channel_id=channel,
            bill_id="clean-2026-09",
            period="2026-09",
            currency="USD",
            source="export",
            fmt=spec.format_id,
            columns=spec.columns,
        )
        report = reconcile(
            "2026-09",
            channel_id=channel,
            gateway_ledger={"by_profile": {"deepseek-flash": {"calls": 1, "cost_usd": 2.00}}},
            bill=bill,
            cfg=cfg,
        )
        assert report.has_alerts() is False
        assert report.alerts == () and report.unexplained == ()
        assert report.deviates is False
        # 零差异仍须分类与备注
        assert report.items[0].classification == "计费口径"
        assert "零差异" in report.items[0].note
        assert replace(report).has_alerts() is False


class Test告警落盘与门禁退出码:
    def _run(self, argv, capsys):
        import importlib

        module = importlib.import_module("ops.billing")
        code = module.main(argv)
        return code, json.loads(capsys.readouterr().out.strip())

    def test_保存报告即逐条落_alerts_jsonl(
        self,
        budget_config_factory,
        billing_bill_fixture,
        billing_gateway_ledger_fixture,
        billing_root,
    ):
        cfg = budget_config_factory(
            reconcile={**budget_config_factory().reconcile, "alert_threshold_usd": 0.1}
        )
        channel = next(iter(cfg.channels))
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        save_report(report, root=billing_root)
        entries = AlertLog(alerts_path(billing_root, channel)).entries()
        assert {entry["kind"] for entry in entries} == set(report.alerts) <= set(ALERT_KINDS)
        assert all(
            entry["channel_id"] == channel and entry["period"] == "2026-09" for entry in entries
        )

    def test_报告含未解释项却返回_0_即红(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        """C14 的关键断言：告警信号与退出码**必须一致**（否则门禁形同虚设）。"""
        config = _config_file(tmp_path, alert_threshold_usd=0.1)
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        assert (
            self._run(
                [
                    "import-bill",
                    "--channel",
                    "llm",
                    "--file",
                    str(source),
                    "--bill-id",
                    billing_bill_fixture["bill_id"],
                    "--period",
                    "2026-09",
                    "--config",
                    str(config),
                ],
                capsys,
            )[0]
            == 0
        )
        code, out = self._run(
            [
                "reconcile",
                "--channel",
                "llm",
                "--period",
                "2026-09",
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--gateway-report",
                str(gateway),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert out["unexplained"], "夹具必须构造出未解释项（否则本断言无牙齿）"
        assert code == 1
        code, out = self._run(["alert-check", "--channel", "llm", "--config", str(config)], capsys)
        assert out["has_alerts"] is True and code == 1

    def test_省略未解释项的报告被机检挡住(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        """手工省略未解释项以图"无告警"⇒ 报告完整性机检失败 ⇒ alert-check 判有告警。"""
        config = _config_file(tmp_path, alert_threshold_usd=0.1)
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        self._run(
            [
                "import-bill",
                "--channel",
                "llm",
                "--file",
                str(source),
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--period",
                "2026-09",
                "--config",
                str(config),
            ],
            capsys,
        )
        self._run(
            [
                "reconcile",
                "--channel",
                "llm",
                "--period",
                "2026-09",
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--gateway-report",
                str(gateway),
                "--config",
                str(config),
            ],
            capsys,
        )
        report_path = tmp_path / "billing" / "llm" / "reports" / "2026-09.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload["unexplained"] = []  # 省略未解释项
        payload["alerts"] = []
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        code, out = self._run(["alert-check", "--channel", "llm", "--config", str(config)], capsys)
        assert code == 1 and out["has_alerts"] is True
        assert any("完整性校验失败" in line for line in out["alerting"])

    def test_since_只看增量(self, capsys, tmp_path):
        """`--since` 只判该时刻之后的**新增**门禁类告警（避免历史告警让门禁永久红）。"""
        config = _config_file(tmp_path)
        channel = "llm"
        from core.billing.budget import AlertLog, BudgetConfig, alerts_path

        cfg = BudgetConfig.from_yaml(config)
        AlertLog(alerts_path(cfg.ledger_root(), channel)).record(
            kind="budget_refused",
            at="2026-09-01T00:00:00+00:00",
            channel_id=channel,
            detail={"reason": "over_limit"},
        )
        code, out = self._run(
            ["alert-check", "--channel", channel, "--config", str(config)], capsys
        )
        assert code == 0 and out["has_alerts"] is False  # 只有留痕、无报告面告警 ⇒ 放行
        code, out = self._run(
            [
                "alert-check",
                "--channel",
                channel,
                "--since",
                "2026-09-15T00:00:00+00:00",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0 and out["alerts_total"] == 1  # 旧告警不在增量内
        code, out = self._run(
            [
                "alert-check",
                "--channel",
                channel,
                "--since",
                "2026-08-01T00:00:00+00:00",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and out["has_alerts"] is True  # 增量内有门禁类告警 ⇒ 告警

    def test_用法错误退出码_2(self, capsys, tmp_path):
        config = _config_file(tmp_path)
        code, out = self._run(
            ["alert-check", "--channel", "ghost", "--config", str(config)], capsys
        )
        assert code == 2 and "渠道" in out["error"]
