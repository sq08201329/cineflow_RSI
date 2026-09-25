"""`ops/billing.py` 七子命令的参数解析与退出码语义（功能 019 / US3 / T1939）：契约 C16。

- 七子命令齐备（命名与契约 C16 逐字一致）：`tiers` / `calibrate` / `raise-tier` /
  `import-bill` / `reconcile` / `alert-check` / `runs`；
- 退出码：`0` 成功（`reconcile`/`alert-check` 兼"无告警"）｜`1` 执行失败或**拒绝**或有告警
  ｜`2` 用法或配置错误（argparse 缺参 ⇒ `SystemExit(2)`；配置类错误 ⇒ CLI 返回 2）；
- JSON 输出；凭证只报"是否设置 + 长度"、**绝不回显值**；
- `import-bill` **不联网**（本用例把 socket 打桩成"一碰即炸"来举证）；
- `reconcile` 有告警/无告警两态；`runs` 达标 0 / 未达标 1（未达标如实报缺口与差值）；
- `raise-tier` 走**定点改写**（其余段与注释逐字节不变）。

CLI 只做参数解析与结果打印，判定全在 `core/billing/`——故本文件的断言都是"薄转发是否忠实"。
"""

import datetime as dt
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.billing.budget import AlertLog, BudgetConfig, alerts_path, assemble_guard
from core.billing.calibration import load_calibration

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs" / "movie.yaml"
SUBCOMMANDS = (
    "tiers",
    "calibrate",
    "raise-tier",
    "import-bill",
    "reconcile",
    "alert-check",
    "runs",
)
_MOMENT = dt.datetime(2026, 9, 1, 3, 0, tzinfo=dt.UTC)  # 渠道本地 = 09-01 11:00


def _run(argv, capsys):
    """调 CLI 并解析 JSON 输出（薄转发的判定面 = 退出码 + stdout）。"""
    module = importlib.import_module("ops.billing")
    code = module.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


def _config(base: Path, **overrides) -> Path:
    """形态配置的派生：账本根落 tmp；样本量下限压到 1（夹具只跑一次真实调用）。"""
    base.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["budget"]["ledger"]["root"] = str(base / "billing")
    payload["budget"]["calibration"]["min_samples"] = 1
    payload["budget"].update(overrides)
    target = base / "movie.yaml"
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _channel(config: Path) -> str:
    return next(iter(BudgetConfig.from_yaml(config).channels))


def _seed_real_call(config: Path, *, amount: float = 0.5, stage: str = "screenplay") -> str:
    """造一次"已发生的真实调用"：账本入账 + 一条 `source=real` 运行记录（不联网）。"""
    from core.billing.runlog import append_run

    cfg = BudgetConfig.from_yaml(config)
    channel = next(iter(cfg.channels))
    assembly = assemble_guard(config)
    assembly.guard.check(
        SimpleNamespace(channel_id=channel, stage=stage, estimated_usd=amount)
    ).settle(amount)
    append_run(
        channel,
        cfg=cfg,
        root=cfg.ledger_root(),
        moment=_MOMENT,
        stage=stage,
        source="real",
        adapter_ref="pilot_llm",
        profile_id="deepseek-flash",
        result="ok",
        cost_source="gateway_accounting",
    )
    return channel


def _bill_file(tmp_path: Path, text: str, name: str = "bill.csv") -> Path:
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    return target


_CLEAN_BILL = (
    "entry_id,amount,currency,period,line_kind,amount_sign,model_ref,"
    "fx_rate,fx_source,fx_at,note\n"
    'b-1,2.00,USD,2026-09,usage,charge,deepseek-flash,,,,"与网关记账一致"\n'
)
_CLEAN_GATEWAY = {
    "by_profile": {"deepseek-flash": {"calls": 1, "cost_usd": 2.00}},
    "total_usd": 2.00,
    "accounting_note": "夹具网关账目：折算值，≠ 厂商账单",
}


class Test七子命令面:
    def test_七子命令齐备且命名与契约一致(self, capsys):
        module = importlib.import_module("ops.billing")
        for name in SUBCOMMANDS:
            with pytest.raises(SystemExit) as exit_info:
                module.main([name, "--help"])
            assert exit_info.value.code == 0, name
        with pytest.raises(SystemExit) as exit_info:
            module.main(["no-such-command"])
        assert exit_info.value.code == 2
        capsys.readouterr()

    def test_缺必填参数即用法错误退出码2(self, capsys, tmp_path):
        module = importlib.import_module("ops.billing")
        with pytest.raises(SystemExit) as exit_info:
            module.main(["tiers"])  # 缺 --channel
        assert exit_info.value.code == 2
        with pytest.raises(SystemExit) as exit_info:
            # 缺 --gateway-report（网关内存账目不在产物面，故以文件给出）
            module.main(["reconcile", "--channel", "llm", "--period", "2026-09"])
        assert exit_info.value.code == 2
        capsys.readouterr()


class TestTiers:
    def test_输出各档余量与未结算预留(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        assembly = assemble_guard(config)
        # 占额但**不结算** ⇒ 崩溃残留的未结算预留必须如实列出（不自动清零）
        assembly.guard.check(
            SimpleNamespace(channel_id=channel, stage="screenplay", estimated_usd=0.5)
        )

        code, out = _run(["tiers", "--channel", channel, "--config", str(config)], capsys)

        assert code == 0
        row = next(item for item in out["tiers"] if item["tier_id"] == "screenplay")
        assert row["limit_usd"] == pytest.approx(5.0)
        assert row["spent_usd"] == 0.0 and row["reserved_usd"] == pytest.approx(0.5)
        assert row["remaining_usd"] == pytest.approx(4.5)
        assert row["refusals"] == 0 and row["last_refusal"] is None
        assert len(row["unsettled"]) == 1
        assert row["unsettled"][0]["estimated_usd"] == pytest.approx(0.5)
        assert row["unsettled"][0]["at"] and row["unsettled"][0]["reservation_id"]
        assert out["channel_id"] == channel and out["calibration_status"] == "untested"
        assert out["ledger_path"].endswith("ledger.json") and out["alerts_path"].endswith(
            "alerts.jsonl"
        )

    def test_渠道与配置不一致即配置错误退出码2(self, capsys, tmp_path):
        config = _config(tmp_path)
        code, out = _run(["tiers", "--channel", "no-such-channel", "--config", str(config)], capsys)
        assert code == 2 and "不一致" in out["error"]


class TestCalibrate:
    def test_无运行记录即拒绝退出码1(self, capsys, tmp_path):
        config = _config(tmp_path)
        code, out = _run(
            [
                "calibrate",
                "--channel",
                _channel(config),
                "--tier",
                "screenplay",
                "--from-records",
                "--measured-usd",
                "0.5",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "没有该环节的真实调用" in out["error"]

    def test_从既有记录落校准记录并只读复述(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _seed_real_call(config, amount=0.5)
        code, out = _run(
            [
                "calibrate",
                "--channel",
                channel,
                "--tier",
                "screenplay",
                "--from-records",
                "--measured-usd",
                "0.5",
                "--cost-source",
                "gateway_accounting",
                "--calibration-id",
                "cal-cli",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0
        assert out["passed"] is True and out["deviation"] == pytest.approx(0.0)
        assert out["measured"]["sample_count"] == 1
        assert out["measured"]["expected_cost_usd"] == pytest.approx(0.5)
        assert "ledger_gateway_accounting" in out["note"]  # 折算来源如实标注
        assert "运行记录 source=real 条数=1" in out["note"]
        stored = load_calibration(
            "cal-cli", channel_id=channel, root=BudgetConfig.from_yaml(config).ledger_root()
        )
        assert stored["measured"]["measured_cost_usd"] == pytest.approx(0.5)
        assert stored["prices_snapshot"]["peak_windows_snapshot"]["attribution"] == "call_start"

    def test_同键重产即拒绝退出码1(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _seed_real_call(config)
        argv = [
            "calibrate",
            "--channel",
            channel,
            "--tier",
            "screenplay",
            "--expected-usd",
            "1.0",
            "--measured-usd",
            "1.0",
            "--sample-count",
            "1",
            "--calibration-id",
            "cal-dup",
            "--config",
            str(config),
        ]
        assert _run(argv, capsys)[0] == 0
        code, out = _run(argv, capsys)
        assert code == 1 and "校准记录落盘失败" in out["error"]

    def test_缺必填项即用法错误退出码2(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        base = ["calibrate", "--channel", channel, "--tier", "screenplay", "--config", str(config)]
        code, out = _run([*base, "--sample-count", "1", "--expected-usd", "1.0"], capsys)
        assert code == 2 and "measured-usd" in out["error"]
        code, out = _run([*base, "--measured-usd", "1.0", "--expected-usd", "1.0"], capsys)
        assert code == 2 and "sample-count" in out["error"]
        code, out = _run(
            [
                "calibrate",
                "--channel",
                channel,
                "--tier",
                "ghost",
                "--measured-usd",
                "1.0",
                "--sample-count",
                "1",
                "--expected-usd",
                "1.0",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 2 and "ghost" in out["error"]  # 缺档即拒绝（不发明档位）

    def test_凭证只报是否设置与长度绝不回显(self, capsys, monkeypatch, tmp_path):
        secret = "sk-super-secret-value-123456"
        monkeypatch.setenv("DEMO_VENDOR_API_KEY", secret)
        monkeypatch.setenv("DEMO_OTHER_KEY", "another-secret-value")
        config = _config(tmp_path)
        channel = _seed_real_call(config)

        code, out = _run(
            [
                "calibrate",
                "--channel",
                channel,
                "--tier",
                "screenplay",
                "--from-records",
                "--measured-usd",
                "0.5",
                "--calibration-id",
                "cal-cred",
                "--config",
                str(config),
            ],
            capsys,
        )

        assert code == 0
        assert out["credentials"]["DEMO_VENDOR_API_KEY"] == {"set": True, "length": len(secret)}
        assert out["credentials"]["DEMO_OTHER_KEY"] == {
            "set": True,
            "length": len("another-secret-value"),
        }
        rendered = json.dumps(out, ensure_ascii=False)
        assert secret not in rendered and "another-secret-value" not in rendered


class TestRaiseTier:
    def test_合格记录走定点改写且其余逐字节不变(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _seed_real_call(config, amount=0.5)
        assert (
            _run(
                [
                    "calibrate",
                    "--channel",
                    channel,
                    "--tier",
                    "screenplay",
                    "--from-records",
                    "--measured-usd",
                    "0.5",
                    "--calibration-id",
                    "cal-raise",
                    "--config",
                    str(config),
                ],
                capsys,
            )[0]
            == 0
        )
        before_lines = config.read_text(encoding="utf-8").splitlines()

        code, out = _run(
            [
                "raise-tier",
                "--channel",
                channel,
                "--tier",
                "screenplay",
                "--limit-usd",
                "9.0",
                "--calibration",
                "cal-raise",
                "--by",
                "运营",
                "--reason",
                "最小规模校准合格后扩量",
                "--config",
                str(config),
            ],
            capsys,
        )

        assert code == 0 and out["ok"] is True and out["reason"] == "tier_raised"
        after_lines = config.read_text(encoding="utf-8").splitlines()
        removed = [line for line in before_lines if line not in after_lines]
        added = [line for line in after_lines if line not in before_lines]
        # **定点改写**：只动该档的额度值（并新增 calibrated_by 键），其余段与注释逐行逐字节不变
        assert len(removed) == 1 and "limit_usd" in removed[0]
        assert len(added) == 2
        assert {line.strip().split(":")[0] for line in added} == {"limit_usd", "calibrated_by"}
        assert [line for line in after_lines if line not in added] == [
            line for line in before_lines if line not in removed
        ]
        payload = yaml.safe_load(config.read_text(encoding="utf-8"))
        # 020（C11）：档位在**渠道内** —— 定点改写的路径随档位形状（真实配置 = 新形状）
        tier = payload["budget"]["channels"][channel]["tiers"]["screenplay"]
        assert tier["limit_usd"] == pytest.approx(9.0) and tier["calibrated_by"] == "cal-raise"
        assert BudgetConfig.from_yaml(config).tiers_of(channel)[
            "screenplay"
        ].limit_usd == pytest.approx(9.0)
        assert [
            entry["kind"]
            for entry in AlertLog(
                alerts_path(BudgetConfig.from_yaml(config).ledger_root(), channel)
            ).entries()
        ] == ["tier_raised"]

    def test_未校准即拒绝且配置一字不改(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        before = config.read_text(encoding="utf-8")
        code, out = _run(
            [
                "raise-tier",
                "--channel",
                channel,
                "--tier",
                "screenplay",
                "--limit-usd",
                "9.0",
                "--calibration",
                "ghost",
                "--by",
                "运营",
                "--reason",
                "扩量",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and out["reason"] == "uncalibrated_raise"
        assert out["config_unchanged"] is True
        assert config.read_text(encoding="utf-8") == before


class TestImportBill:
    def test_不联网且落盘(self, capsys, monkeypatch, tmp_path, billing_bill_fixture):
        import socket

        def _no_network(*args, **kwargs):
            raise AssertionError("import-bill 不得联网（导出形态为人工上传）")

        monkeypatch.setattr(socket, "socket", _no_network)
        monkeypatch.setattr(socket, "create_connection", _no_network)
        monkeypatch.setattr(socket, "getaddrinfo", _no_network)
        config = _config(tmp_path)
        channel = _channel(config)
        source = _bill_file(tmp_path, billing_bill_fixture["text"])

        code, out = _run(
            [
                "import-bill",
                "--channel",
                channel,
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
        assert out["entries"] == len(billing_bill_fixture["rows"])
        assert out["raw_ref"] and out["fetched_at"] and out["system_digest"]
        assert out["source"] == "export" and out["currency"] == "USD"

    def test_未注册格式零落盘退出码1(self, capsys, tmp_path, billing_bill_fixture):
        base = tmp_path / "unregistered"
        config = _config(base)
        channel = _channel(config)
        payload = yaml.safe_load(config.read_text(encoding="utf-8"))
        payload["budget"]["channels"][channel]["bill"]["format"] = "vendor_only"
        config.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        source = _bill_file(tmp_path, billing_bill_fixture["text"])

        code, out = _run(
            [
                "import-bill",
                "--channel",
                channel,
                "--file",
                str(source),
                "--bill-id",
                "vendor-only",
                "--period",
                billing_bill_fixture["period"],
                "--config",
                str(config),
            ],
            capsys,
        )

        assert code == 1 and "未注册" in out["error"]
        assert not (base / "billing" / channel / "bills").exists()

    def test_重复批次拒绝退出码1(self, capsys, tmp_path, billing_bill_fixture):
        config = _config(tmp_path)
        channel = _channel(config)
        source = _bill_file(tmp_path, billing_bill_fixture["text"])
        argv = [
            "import-bill",
            "--channel",
            channel,
            "--file",
            str(source),
            "--bill-id",
            billing_bill_fixture["bill_id"],
            "--period",
            billing_bill_fixture["period"],
            "--config",
            str(config),
        ]
        assert _run(argv, capsys)[0] == 0
        code, out = _run(argv, capsys)
        assert code == 1 and "批次幂等" in out["error"]


class TestReconcile:
    def test_无账单即拒绝产出退出码1(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        gateway_file = tmp_path / "gateway.json"
        gateway_file.write_text(json.dumps(_CLEAN_GATEWAY), encoding="utf-8")

        code, out = _run(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                "2026-09",
                "--bill-id",
                "never-imported",
                "--gateway-report",
                str(gateway_file),
                "--config",
                str(config),
            ],
            capsys,
        )

        assert code == 1 and "无账单批次即拒绝产出" in out["error"]
        assert not (tmp_path / "billing" / channel / "reports" / "2026-09.json").exists()

    def test_无告警退出码0(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        self._import(config, channel, _CLEAN_BILL, capsys)
        gateway_file = tmp_path / "gateway.json"
        gateway_file.write_text(json.dumps(_CLEAN_GATEWAY), encoding="utf-8")

        code, out = _run(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                "2026-09",
                "--bill-id",
                "vendor-2026-09",
                "--gateway-report",
                str(gateway_file),
                "--config",
                str(config),
            ],
            capsys,
        )

        assert code == 0
        assert out["unexplained"] == [] and out["alerts"] == []
        assert out["items"][0]["classification"] == "计费口径"
        assert out["items"][0]["delta_usd"] == pytest.approx(0.0)

    def test_有未解释项即有告警退出码1(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        text = _CLEAN_BILL + 'b-2,0.03,USD,2026-09,mystery,charge,deepseek-flash,,,,"取值域外"\n'
        self._import(config, channel, text, capsys)
        gateway_file = tmp_path / "gateway.json"
        gateway_file.write_text(json.dumps(_CLEAN_GATEWAY), encoding="utf-8")

        code, out = _run(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                "2026-09",
                "--bill-id",
                "vendor-2026-09",
                "--gateway-report",
                str(gateway_file),
                "--config",
                str(config),
            ],
            capsys,
        )

        assert code == 1 and out["unexplained"] and "unexplained_delta" in out["alerts"]
        assert out["bill_refs"][0]["bill_id"] == "vendor-2026-09"
        assert "时序错位" in out["accounting_note"]
        # 同周期重产拒绝（append-only）
        code, out = _run(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                "2026-09",
                "--bill-id",
                "vendor-2026-09",
                "--gateway-report",
                str(gateway_file),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "同周期重产" in out["error"]

    @staticmethod
    def _import(config: Path, channel: str, text: str, capsys) -> None:
        source = _bill_file(config.parent, text, name="fixture-bill.csv")
        code, _ = _run(
            [
                "import-bill",
                "--channel",
                channel,
                "--file",
                str(source),
                "--bill-id",
                "vendor-2026-09",
                "--period",
                "2026-09",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0


class TestAlertCheck:
    def test_无报告无增量即无告警退出码0(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        code, out = _run(["alert-check", "--channel", channel, "--config", str(config)], capsys)
        assert code == 0 and out["has_alerts"] is False
        assert out["reports"] == [] and out["alerts_total"] == 0
        assert out["alerts_jsonl"].endswith("alerts.jsonl")

    def test_告警留痕按_since_判增量(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        cfg = BudgetConfig.from_yaml(config)
        AlertLog(alerts_path(cfg.ledger_root(), channel)).record(
            kind="budget_refused",
            at="2026-09-10T00:00:00+00:00",
            channel_id=channel,
            detail={"reason": "over_limit"},
        )
        # 无报告时只看增量：留痕逐条列出但**不长期判红**
        code, out = _run(["alert-check", "--channel", channel, "--config", str(config)], capsys)
        assert code == 0 and out["has_alerts"] is False and out["alerts_total"] == 1
        # `--since` 早于告警时刻 ⇒ 增量含门禁类告警 ⇒ 退出码 1
        code, out = _run(
            [
                "alert-check",
                "--channel",
                channel,
                "--since",
                "2026-09-01T00:00:00+00:00",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and out["has_alerts"] is True and "新增门禁类告警" in out["alerting"][0]
        # `--since` 晚于告警时刻 ⇒ 零增量
        code, out = _run(
            [
                "alert-check",
                "--channel",
                channel,
                "--since",
                "2027-01-01T00:00:00+00:00",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0 and out["has_alerts"] is False

    def test_报告含未解释项即退出码1(self, capsys, tmp_path):
        config = _config(tmp_path)
        channel = _channel(config)
        text = _CLEAN_BILL + 'b-2,0.03,USD,2026-09,mystery,charge,deepseek-flash,,,,"取值域外"\n'
        TestReconcile._import(config, channel, text, capsys)
        gateway_file = tmp_path / "gateway.json"
        gateway_file.write_text(json.dumps(_CLEAN_GATEWAY), encoding="utf-8")
        assert (
            _run(
                [
                    "reconcile",
                    "--channel",
                    channel,
                    "--period",
                    "2026-09",
                    "--bill-id",
                    "vendor-2026-09",
                    "--gateway-report",
                    str(gateway_file),
                    "--config",
                    str(config),
                ],
                capsys,
            )[0]
            == 1
        )
        code, out = _run(
            ["alert-check", "--channel", channel, "--period", "2026-09", "--config", str(config)],
            capsys,
        )
        assert code == 1 and out["has_alerts"] is True
        assert out["reports"][0]["period"] == "2026-09" and out["reports"][0]["unexplained"]
        # 指定的周期没有报告 ⇒ 用法错误（不静默放行）
        code, out = _run(
            ["alert-check", "--channel", channel, "--period", "2020-01", "--config", str(config)],
            capsys,
        )
        assert code == 2 and "报告不存在" in out["error"]


class TestRuns:
    def _fill(self, config: Path, entries) -> str:
        from core.billing.runlog import append_run

        cfg = BudgetConfig.from_yaml(config)
        channel = next(iter(cfg.channels))
        for params in entries:
            append_run(channel, cfg=cfg, root=cfg.ledger_root(), **params)
        return channel

    def test_达标退出码0(self, capsys, tmp_path, billing_runlog_factory):
        config = _config(tmp_path)
        channel = self._fill(config, billing_runlog_factory("continuous")["entries"])
        code, out = _run(
            ["runs", "--channel", channel, "--end", "2026-09-08", "--config", str(config)], capsys
        )
        assert code == 0
        assert out["meets"] is True and out["covered_days"] == 8
        assert out["gaps"] == [] and out["max_gap_days"] == 0 and out["continuous"] is True

    def test_未达标退出码1并如实报缺口与差值(self, capsys, tmp_path, billing_runlog_factory):
        config = _config(tmp_path / "scattered")
        channel = self._fill(config, billing_runlog_factory("scattered")["entries"])
        code, out = _run(
            ["runs", "--channel", channel, "--end", "2026-09-10", "--config", str(config)], capsys
        )
        assert code == 1
        assert out["meets"] is False and out["covered_days"] == 8 and out["max_gap_days"] == 2
        assert out["gaps"] == [{"from": "2026-09-05", "to": "2026-09-06", "days": 2}]
        assert out["gap_shortfall_days"] == 2 and out["reasons"]
        # 两个旋钮都可覆盖：覆盖下限与断档容差
        code, out = _run(
            [
                "runs",
                "--channel",
                channel,
                "--end",
                "2026-09-10",
                "--window-days",
                "8",
                "--gap-tolerance",
                "2",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0 and out["meets"] is True and out["continuous"] is False
        assert out["gaps"] == [{"from": "2026-09-05", "to": "2026-09-06", "days": 2}]
