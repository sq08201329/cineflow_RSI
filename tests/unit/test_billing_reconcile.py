"""逐项对账（功能 019 / US2 / T1933 + T1936 的产物面）：契约 C13 / C14。

- 配对口径：按 `(档案 id, 周期)` 聚合逐项比对；金额以账单币种为准（异币种先按 `fx` 折算）；
- **固定六类不增不减**，分类输入面 = 账单声明的驱动列（`line_kind` / `amount_sign`），
  映射由配置声明——**禁止**金额启发式；缺失/取值域外 ⇒ `unclassified` ⇒ `unexplained` ⇒ 告警；
- `|delta| ≤ amount_tolerance_usd` 视为零差异，但**仍须分类与备注**；超 `alert_threshold_usd`
  ⇒ `delta_over_threshold`；
- 报告必须含 `bill_refs[]`（网关记账禁止自证）；**无账单 ⇒ 拒绝产出**（不产"零差异"报告）；
- 产物面：报告落 `reports/{period}.json`（同键重产拒绝 + `system_digest` 机检），
  告警逐条追加到 `alerts.jsonl`（只增）。
"""

import json
from pathlib import Path

import pytest
import yaml

from core.billing.bill import normalize_bill, save_bill
from core.billing.budget import AlertLog, alerts_path
from core.billing.reconcile import (
    ACCOUNTING_NOTE,
    CLASSIFICATIONS,
    REPORT_SYSTEM_FIELDS,
    UNCLASSIFIED,
    ReconciliationError,
    load_report,
    reconcile,
    report_path,
    save_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs" / "movie.yaml"


def _channel(cfg) -> str:
    return next(iter(cfg.channels))


def _cfg(budget_config_factory, *, tolerance: float | None = None, threshold: float | None = None):
    """夹具配置（`reconcile` 段整段覆盖：容差与告警阈值是**阈值面**，必须按段声明）。"""
    base = budget_config_factory()
    section = dict(base.reconcile)
    if tolerance is not None:
        section["amount_tolerance_usd"] = tolerance
    if threshold is not None:
        section["alert_threshold_usd"] = threshold
    return budget_config_factory(reconcile=section)


def _bill(cfg, channel, fixture):
    spec = cfg.channel(channel).bill
    return normalize_bill(
        fixture["text"],
        channel_id=channel,
        bill_id=fixture["bill_id"],
        period=fixture["period"],
        currency=fixture["currency"],
        source="export",
        fmt=spec.format_id,
        columns=spec.columns,
    )


def _report(cfg, channel, fixture, ledger, **overrides):
    params = {
        "channel_id": channel,
        "gateway_ledger": ledger,
        "bill": _bill(cfg, channel, fixture),
        "cfg": cfg,
    }
    params.update(overrides)
    return reconcile(fixture["period"], **params)


class Test六类分类:
    def test_逐项分类与期望值一致(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = _cfg(budget_config_factory)
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        by_class: dict[str, float] = {}
        for item in report.items:
            by_class[item.classification] = by_class.get(item.classification, 0.0) + item.bill_usd
        for classification, expected in billing_bill_fixture["expect"].items():
            if classification.endswith("usd"):
                continue
            assert by_class[classification] == pytest.approx(expected), (classification, by_class)
        assert report.bill_total_usd == pytest.approx(
            billing_bill_fixture["expect"]["bill_total_usd"]
        )
        assert report.gateway_total_usd == pytest.approx(2.55)
        # 每条差异必带分类 + delta + 口径备注（零差异也须分类与备注）
        assert report.items
        assert all(item.classification for item in report.items)
        assert all(item.note for item in report.items)
        # 固定六类 + unclassified（不增不减）
        assert set(CLASSIFICATIONS) == {
            "计费口径",
            "未入账",
            "时序错位",
            "免费额度与折扣",
            "币种汇率",
            "未结账",
        }
        assert by_class[UNCLASSIFIED] == pytest.approx(0.03)  # 取值域外行 ⇒ 未分类

    def test_按档案id逐项配对_网关侧金额被正确归位(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        """C13 的配对口径：按**（档案 id, 周期）**聚合——网关侧金额归到同一档案的计费线上。

        账单的档案 id 列必须在配置的 `columns` 里**声明**（否则条目无从配对，网关记账会被
        逐条当成"未入账"，差异全成噪声）。夹具：`deepseek-flash` 网关 2.00 vs 账单 2.05。
        """
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        booking = [
            item
            for item in report.items
            if item.key == "deepseek-flash" and item.classification == "计费口径"
        ]
        assert booking, [item.key for item in report.items]
        assert booking[0].gateway_usd == pytest.approx(2.00)  # 网关侧金额归位（不是 0）
        assert booking[0].bill_usd == pytest.approx(2.05)  # 1.20 + 0.80 + 0.05（异币种折算）
        assert booking[0].delta_usd == pytest.approx(0.05)  # 口径差实测偏差
        assert "deepseek-flash" in booking[0].note or "账单条目" in booking[0].note

    def test_分类只由声明的驱动列决定_不得金额启发式(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        """改**配置里的映射**即改分类：分类输入面是账单列，不是金额大小。"""
        from dataclasses import replace

        cfg = budget_config_factory()
        channel = _channel(cfg)
        base = cfg.channel(channel)
        remapped = base.bill
        strict = replace(
            cfg,
            channels={
                channel: replace(
                    base,
                    bill=replace(
                        remapped,
                        line_kind_classes={**remapped.line_kind_classes, "mystery_kind": "未入账"},
                    ),
                )
            },
        )
        base_report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        remapped_report = _report(
            strict, channel, billing_bill_fixture, billing_gateway_ledger_fixture
        )

        def _by_class(report):
            sums = {name: 0.0 for name in CLASSIFICATIONS}
            for item in report.items:
                if item.classification in sums:
                    sums[item.classification] += item.bill_usd
            return sums

        base_sums = _by_class(base_report)
        remapped_sums = _by_class(remapped_report)
        # 未映射 ⇒ 未分类；把该取值**声明**进映射后同一行即归类（金额没变，分类随声明变）
        assert any(item.classification == UNCLASSIFIED for item in base_report.items)
        assert not any(item.classification == UNCLASSIFIED for item in remapped_report.items)
        assert remapped_sums["未入账"] == pytest.approx(base_sums["未入账"] + 0.03)

    def test_零差异仍须分类与备注(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = _cfg(budget_config_factory, tolerance=10.0)  # 容差大 ⇒ 全视为零差异
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        assert report.deviates is False
        assert all(item.classification for item in report.items)
        assert all("零差异" in item.note for item in report.items)

    def test_超告警阈值即告警(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = _cfg(budget_config_factory, threshold=0.1)
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        assert "delta_over_threshold" in report.alerts
        assert report.unexplained
        assert report.has_alerts() is True

    def test_未分类项_100_进入未解释集合并告警(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        assert any(UNCLASSIFIED in key for key in report.unexplained)
        assert "unexplained_delta" in report.alerts

    def test_折扣与免费额度不静默按_0_记账(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        discounts = [item for item in report.items if item.classification == "免费额度与折扣"]
        assert discounts and all(item.bill_usd < 0 for item in discounts)  # 冲减如实呈现

    def test_时序错位与未结账不得判定网关记账有误(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        assert "不得" in report.accounting_note and "网关记账有误" in report.accounting_note
        assert report.accounting_note == ACCOUNTING_NOTE
        deferred = [
            item.classification
            for item in report.items
            if item.classification in ("时序错位", "未结账")
        ]
        assert deferred  # 两类差异如实分类（留待下期），未被当作网关错误


class Test无账单拒绝产出:
    def test_无账单对象即拒绝(self, budget_config_factory, billing_gateway_ledger_fixture):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        with pytest.raises(ReconciliationError, match="缺账单"):
            reconcile(
                "2026-09",
                channel_id=channel,
                gateway_ledger=billing_gateway_ledger_fixture,
                bill=None,
                cfg=cfg,
            )

    def test_空账单批次即拒绝_不产零差异报告(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        from dataclasses import replace

        cfg = budget_config_factory()
        channel = _channel(cfg)
        bill = _bill(cfg, channel, billing_bill_fixture)
        with pytest.raises(ReconciliationError, match="无条目"):
            reconcile(
                "2026-09",
                channel_id=channel,
                gateway_ledger=billing_gateway_ledger_fixture,
                bill=replace(bill, entries=()),
                cfg=cfg,
            )

    def test_批次不属于该渠道即拒绝(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        from dataclasses import replace

        cfg = budget_config_factory()
        channel = _channel(cfg)
        bill = _bill(cfg, channel, billing_bill_fixture)
        with pytest.raises(ReconciliationError, match="不符"):
            reconcile(
                "2026-09",
                channel_id=channel,
                gateway_ledger=billing_gateway_ledger_fixture,
                bill=replace(bill, channel_id="other-channel"),
                cfg=cfg,
            )


class Test报告与告警产物:
    def test_报告含_bill_refs_且落盘机检(
        self,
        budget_config_factory,
        billing_bill_fixture,
        billing_gateway_ledger_fixture,
        billing_root,
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        assert report.bill_refs, "报告必须引用账单批次（网关记账不得自证）"
        ref = report.bill_refs[0]
        assert ref["bill_id"] == billing_bill_fixture["bill_id"] and ref["raw_ref"]
        path = save_report(report, root=billing_root)
        assert path == report_path(billing_root, channel, "2026-09") and path.is_file()
        payload = load_report("2026-09", channel_id=channel, root=billing_root)
        assert set(REPORT_SYSTEM_FIELDS) <= set(payload)
        assert payload["thresholds_snapshot"]["amount_tolerance_usd"] == pytest.approx(0.01)
        assert payload["unexplained"] and payload["alerts"]
        with pytest.raises(Exception, match="已存在"):  # 同键重产拒绝
            save_report(report, root=billing_root)

    def test_告警逐条追加到_alerts_jsonl(
        self,
        budget_config_factory,
        billing_bill_fixture,
        billing_gateway_ledger_fixture,
        billing_root,
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        save_report(report, root=billing_root)
        entries = AlertLog(alerts_path(billing_root, channel)).entries()
        assert {entry["kind"] for entry in entries} == set(report.alerts)
        assert entries[0]["channel_id"] == channel and entries[0]["period"] == "2026-09"
        assert entries[0]["ref"] == report.report_id
        assert entries[0]["detail"]["bill_refs"]


class Test对抗面_改写与省略:
    def test_改写报告系统字段后读取即报错(
        self,
        budget_config_factory,
        billing_bill_fixture,
        billing_gateway_ledger_fixture,
        billing_root,
    ):
        """省略未解释项 / 改写阈值快照 ⇒ `system_digest` 机检 100% 拒绝。"""
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = _report(cfg, channel, billing_bill_fixture, billing_gateway_ledger_fixture)
        path = save_report(report, root=billing_root)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["unexplained"] = []  # 手工省略未解释项（想蒙过告警门禁）
        payload["alerts"] = []
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        with pytest.raises(Exception, match="完整性校验失败"):
            load_report("2026-09", channel_id=channel, root=billing_root)

    def test_改写账单后再对账_系统字段机检挡住(
        self,
        budget_config_factory,
        billing_bill_fixture,
        billing_gateway_ledger_fixture,
        billing_root,
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        bill = _bill(cfg, channel, billing_bill_fixture)
        path = save_bill(bill, root=billing_root)
        from core.billing.bill import load_bill

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"][0]["amount"] = 0.01  # 改写已落盘账单（想改小差异）
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        with pytest.raises(Exception, match="完整性校验失败"):
            load_bill(bill.bill_id, channel_id=channel, root=billing_root)


class TestCLI_import_bill_reconcile:
    def _run(self, argv, capsys):
        import importlib

        module = importlib.import_module("ops.billing")
        code = module.main(argv)
        return code, json.loads(capsys.readouterr().out.strip())

    def _config(self, tmp_path, **budget_overrides):
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        payload["budget"]["reconcile"].update(budget_overrides)
        target = tmp_path / "movie.yaml"
        target.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return target

    def test_import_bill_不联网且落盘(self, capsys, tmp_path, billing_bill_fixture):
        config = self._config(tmp_path)
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        code, out = self._run(
            [
                "import-bill",
                "--channel",
                "llm",
                "--file",
                str(source),
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--period",
                billing_bill_fixture["period"],
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0 and out["network"] == "none"
        assert out["entries"] == len(billing_bill_fixture["rows"]) and out["raw_ref"]
        # 重复批次 ⇒ 退出码 1（拒绝覆盖）
        code, out = self._run(
            [
                "import-bill",
                "--channel",
                "llm",
                "--file",
                str(source),
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--period",
                billing_bill_fixture["period"],
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "批次幂等" in out["error"]

    def test_import_bill_未注册格式即拒绝零落盘(self, capsys, tmp_path, billing_bill_fixture):
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        payload["budget"]["channels"]["llm"]["bill"]["format"] = "vendor_only"
        config = tmp_path / "movie.yaml"
        config.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        code, out = self._run(
            [
                "import-bill",
                "--channel",
                "llm",
                "--file",
                str(source),
                "--bill-id",
                "unknown",
                "--period",
                "2026-09",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "未注册" in out["error"]
        assert not (tmp_path / "billing" / "llm" / "bills").exists()

    def test_reconcile_有告警退出码_1_无告警_0(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        config = self._config(tmp_path, alert_threshold_usd=0.1)
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
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
        gateway_report = tmp_path / "gateway.json"
        gateway_report.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
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
                str(gateway_report),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1  # 有告警 ⇒ 非零退出（门禁语义）
        assert out["unexplained"] and out["bill_refs"]
        # alert-check 只读入口：同一产物 ⇒ 退出码 1
        code, out = self._run(["alert-check", "--channel", "llm", "--config", str(config)], capsys)
        assert (
            code == 1 and out["has_alerts"] is True and out["alerts_jsonl"].endswith("alerts.jsonl")
        )

    def test_reconcile_无账单即拒绝产出(self, capsys, tmp_path, billing_gateway_ledger_fixture):
        config = self._config(tmp_path)
        gateway_report = tmp_path / "gateway.json"
        gateway_report.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        code, out = self._run(
            [
                "reconcile",
                "--channel",
                "llm",
                "--period",
                "2026-09",
                "--bill-id",
                "ghost-bill",
                "--gateway-report",
                str(gateway_report),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "无账单批次即拒绝产出" in out["error"]

    def test_alert_check_无报告为无告警(self, capsys, tmp_path):
        config = self._config(tmp_path)
        code, out = self._run(["alert-check", "--channel", "llm", "--config", str(config)], capsys)
        assert code == 0 and out["has_alerts"] is False and out["alerts_total"] == 0
