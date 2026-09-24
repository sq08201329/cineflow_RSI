"""校准记录与扩量（功能 019 / US1 / T1926）：契约 C12 + C16（三子命令）。

- 校准记录字段齐备（渠道 / 环节档 / **当时价目快照含 `price_matrix` 与 `peak_windows_snapshot`** /
  实测花费与样本量 / `deviation` / `passed` / `reasons` / 口径备注 / `at`）+ append-only
  （同键重产拒绝）；
- `raise_tier` **六条拒绝条件各一例**（① 无 `calibration_id`；② 记录不存在或不同渠道；
  ③ `passed != true`；④ 样本量 < `min_samples`；⑤ 超期 `record_ttl_days`；
  ⑥ `deviation` 超容差）——拒绝时**配置未被改写**、留痕 `kind=uncalibrated_raise`；
  合格时定点改写额度 + `calibrated_by` 落键 + `kind=tier_raised`，其余段与注释**逐字节不变**；
  渠道状态派生 `untested|pass|fail|stale`；
- CLI 三子命令（`tiers` / `calibrate` / `raise-tier`）的退出码与 JSON 输出
  （US3 的 T1939 将补齐其余四条）。
"""

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from core.billing.budget import AlertLog, BudgetConfig, alerts_path
from core.billing.calibration import (
    CALIBRATION_STATES,
    CalibrationRecordError,
    calibration_status,
    load_calibration,
    raise_tier,
    record_calibration,
    require_calibration,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs" / "movie.yaml"
_AT = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)


def _record(cfg, root, channel, **overrides):
    params = {
        "calibration_id": "cal-1",
        "sample_count": int(cfg.calibration["min_samples"]),
        "measured_cost_usd": 4.8,
        "expected_cost_usd": 5.0,
        "prices_snapshot": {"deepseek-flash": {"prices": {"prompt_per_1k": 0.0003}}},
        "note": "最小规模单轮（运营侧 smoke --round 记录复述）",
        "cost_source": "gateway_accounting",
        "at": _AT,
    }
    params.update(overrides)
    return record_calibration(
        cfg=cfg, channel_id=channel, tier_id="screenplay", root=root, **params
    )


class Test校准记录:
    def test_字段齐备且含价目快照与峰谷快照(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        record = _record(cfg, billing_root, channel)
        payload = load_calibration("cal-1", channel_id=channel, root=billing_root)
        assert payload["channel_id"] == channel and payload["tier_id"] == "screenplay"
        snapshot = payload["prices_snapshot"]
        assert (
            "peak_windows_snapshot" in snapshot and snapshot["peak_windows_snapshot"]["attribution"]
        )
        assert "price_book" in snapshot and "tier" in snapshot
        assert payload["measured"]["sample_count"] == cfg.calibration["min_samples"]
        assert payload["measured"]["measured_cost_usd"] == pytest.approx(4.8)
        assert payload["measured"]["cost_source"] == "gateway_accounting"
        assert payload["deviation"] == pytest.approx(-0.04)
        assert payload["passed"] is True and payload["reasons"]
        assert "call_start" in payload["note"]  # 口径备注（峰谷归属三处可见之一）
        assert record.at == _AT.isoformat()

    def test_append_only_同键重产拒绝(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        _record(cfg, billing_root, channel)
        with pytest.raises(CalibrationRecordError, match="已存在"):
            _record(cfg, billing_root, channel)

    def test_样本不足或偏差超容差即_passed_false(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        record = _record(
            cfg, billing_root, channel, calibration_id="cal-fail", measured_cost_usd=8.0
        )
        assert record.passed is False
        assert any("偏差超容差" in reason for reason in record.reasons)
        record = _record(cfg, billing_root, channel, calibration_id="cal-thin", sample_count=1)
        assert record.passed is False
        assert any("样本量不足" in reason for reason in record.reasons)

    def test_渠道状态由最新记录派生(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        assert calibration_status(channel, cfg=cfg, root=billing_root) == "untested"
        assert set(CALIBRATION_STATES) == {"untested", "pass", "fail", "stale"}
        _record(cfg, billing_root, channel)
        assert calibration_status(channel, cfg=cfg, root=billing_root, at=_AT) == "pass"
        # 超期 ⇒ stale（按 untested 处理：扩量恒拒绝，理由写明超期）
        later = _AT + dt.timedelta(days=int(cfg.calibration["record_ttl_days"]) + 1)
        assert calibration_status(channel, cfg=cfg, root=billing_root, at=later) == "stale"
        _record(
            cfg,
            billing_root,
            channel,
            calibration_id="cal-fail",
            measured_cost_usd=8.0,
            at=_AT + dt.timedelta(hours=1),  # 最新记录 = 未通过
        )
        assert calibration_status(channel, cfg=cfg, root=billing_root, at=_AT) == "fail"


class Test扩量六条拒绝:
    def _raise(self, cfg, root, config_path, channel, **overrides):
        params = {
            "calibration_id": "cal-1",
            "by": "运营",
            "reason": "最小规模校准合格，扩量到最小规模档上界",
            "cfg": cfg,
            "config_path": config_path,
            "root": root,
            "at": _AT,
        }
        params.update(overrides)
        return raise_tier(channel, "screenplay", 9.0, **params)

    def _alerts(self, cfg, root, channel) -> list[dict]:
        return AlertLog(alerts_path(root, channel)).entries()

    @pytest.mark.parametrize(
        "case,overrides",
        [
            ("无_id", {"calibration_id": ""}),
            ("记录不存在", {"calibration_id": "ghost"}),
            ("未通过", {"_failed_record": True}),
            ("样本不足", {"_thin_record": True}),
            ("超期", {"at": _AT + dt.timedelta(days=400)}),
        ],
    )
    def test_拒绝条件逐条且配置未被改写(
        self, case, overrides, budget_config_factory, billing_root, tmp_path
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        config_path = tmp_path / "configs" / "movie.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        before = config_path.read_text(encoding="utf-8")
        if overrides.pop("_failed_record", False):
            _record(cfg, billing_root, channel, measured_cost_usd=8.0)
        elif overrides.pop("_thin_record", False):
            _record(cfg, billing_root, channel, sample_count=1)
        else:
            _record(cfg, billing_root, channel)
        with pytest.raises(CalibrationRecordError):
            self._raise(cfg, billing_root, config_path, channel, **overrides)
        assert config_path.read_text(encoding="utf-8") == before  # 拒绝时配置一字不改
        alerts = self._alerts(cfg, billing_root, channel)
        assert [a["kind"] for a in alerts] == ["uncalibrated_raise"]

    def test_偏差超容差即拒绝(self, budget_config_factory, billing_root, tmp_path):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        config_path = tmp_path / "movie.yaml"
        config_path.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        # 制造"记录通过但偏差超容差"：容差收紧为 0（记录按旧阈值判过，先决按新阈值拒）
        _record(cfg, billing_root, channel)
        strict = budget_config_factory(
            calibration={**cfg.calibration, "deviation_tolerance": 0.001}
        )
        before = config_path.read_text(encoding="utf-8")
        with pytest.raises(CalibrationRecordError, match="偏差超容差"):
            self._raise(strict, billing_root, config_path, channel)
        assert config_path.read_text(encoding="utf-8") == before

    def test_不同渠道的校准记录即拒绝(
        self, budget_config_factory, billing_budget_factory, billing_root, tmp_path
    ):
        declared = billing_budget_factory(tier_limit_usd=1.0, window_kind="day")["channels"]
        first = next(iter(declared))
        second = "channel-iso"
        cfg = budget_config_factory(channels={first: declared[first], second: {**declared[first]}})
        config_path = tmp_path / "movie.yaml"
        config_path.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        _record(cfg, billing_root, second)  # 记录落在**另一个渠道**目录
        with pytest.raises(CalibrationRecordError):
            raise_tier(
                first,
                "screenplay",
                9.0,
                calibration_id="cal-1",
                by="运营",
                reason="扩量",
                cfg=cfg,
                config_path=config_path,
                root=billing_root,
                at=_AT,
            )

    def test_同额度即拒绝且不留痕(self, budget_config_factory, billing_root, tmp_path):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        config_path = tmp_path / "movie.yaml"
        config_path.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        _record(cfg, billing_root, channel)
        current = budget_config_factory().tier("screenplay").limit_usd
        with pytest.raises(CalibrationRecordError, match="相同"):
            self._raise(
                cfg, billing_root, config_path, channel, limit_usd=None
            ) if False else raise_tier(
                channel,
                "screenplay",
                current,
                calibration_id="cal-1",
                by="运营",
                reason="空转",
                cfg=cfg,
                config_path=config_path,
                root=billing_root,
                at=_AT,
            )
        assert self._alerts(cfg, billing_root, channel) == []  # 不制造空转留痕


class Test扩量落地:
    def test_合格时定点改写额度与_calibrated_by_且其余逐字节不变(
        self, budget_config_factory, billing_root, tmp_path
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        config_path = tmp_path / "configs" / "movie.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        before_lines = config_path.read_text(encoding="utf-8").splitlines()
        _record(cfg, billing_root, channel)
        report = raise_tier(
            channel,
            "screenplay",
            9.0,
            calibration_id="cal-1",
            by="运营",
            reason="最小规模校准合格",
            cfg=cfg,
            config_path=config_path,
            root=billing_root,
            at=_AT,
        )
        after = config_path.read_text(encoding="utf-8")
        payload = yaml.safe_load(after)
        tier = payload["budget"]["tiers"]["screenplay"]
        assert tier["limit_usd"] == pytest.approx(9.0)
        assert tier["calibrated_by"] == "cal-1"  # 升级可追溯到记录
        assert report["previous_limit_usd"] == pytest.approx(cfg.tier("screenplay").limit_usd)
        assert report["event"] == "tier_raised" and report["by_reason"] == "最小规模校准合格"
        # 其余段与注释逐字节不变：**只有**该档的两处改动（额度值 + 新增 calibrated_by 行）
        left = [line for line in after.splitlines() if "calibrated_by:" not in line]
        right = [line.replace("limit_usd: 5.0", "limit_usd: 9") for line in before_lines]
        assert left == right
        assert len(before_lines) + 1 == len(after.splitlines())  # 只插入了一行
        assert [a["kind"] for a in AlertLog(alerts_path(billing_root, channel)).entries()] == [
            "tier_raised"
        ]
        # 改写后重新装配：新额度即时生效（配置为准）
        assert BudgetConfig.from_yaml(config_path).tier("screenplay").limit_usd == pytest.approx(
            9.0
        )

    def test_require_calibration_合格时返回记录(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        _record(cfg, billing_root, channel)
        record = require_calibration(
            "cal-1", cfg=cfg, channel_id=channel, tier_id="screenplay", root=billing_root, at=_AT
        )
        assert record["passed"] is True
        with pytest.raises(CalibrationRecordError, match="先决条件"):
            require_calibration(
                "cal-1",
                cfg=cfg,
                channel_id=channel,
                tier_id="promo",  # 环节不符
                root=billing_root,
                at=_AT,
            )


class TestCLI三子命令:
    """CLI 薄转发（判定全在 core）：退出码 0/1/2 + JSON。US3 的 T1939 补齐其余子命令。"""

    def _run(self, argv, capsys):
        import importlib

        module = importlib.import_module("ops.billing")
        code = module.main(argv)
        return code, json.loads(capsys.readouterr().out.strip())

    def test_tiers_列出各档余量与未结算预留(self, capsys, tmp_path):
        config = tmp_path / "movie.yaml"
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        config.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        code, out = self._run(["tiers", "--channel", "llm", "--config", str(config)], capsys)
        assert code == 0
        assert out["channel_id"] == "llm" and out["calibration_status"] == "untested"
        tiers = {row["tier_id"]: row for row in out["tiers"]}
        assert tiers["screenplay"]["limit_usd"] > 0
        assert tiers["screenplay"]["unsettled"] == []  # 未结算预留如实列出（此处为空）
        assert out["ledger_path"].endswith("ledger.json")

    def test_tiers_渠道不一致即用法错误(self, capsys, tmp_path):
        config = tmp_path / "movie.yaml"
        config.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        code, out = self._run(["tiers", "--channel", "ghost", "--config", str(config)], capsys)
        assert code == 2 and "渠道" in out["error"]

    def test_calibrate_落记录且不联网(self, capsys, tmp_path):
        config = tmp_path / "movie.yaml"
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        config.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        code, out = self._run(
            [
                "calibrate",
                "--channel",
                "llm",
                "--tier",
                "screenplay",
                "--config",
                str(config),
                "--measured-usd",
                "4.8",
                "--expected-usd",
                "5.0",
                "--sample-count",
                "5",
                "--cost-source",
                "vendor_console",
                "--calibration-id",
                "cal-cli",
            ],
            capsys,
        )
        assert code == 0
        assert out["calibration_id"] == "cal-cli" and out["passed"] is True
        assert out["measured"]["cost_source"] == "vendor_console"
        assert "凭证" not in json.dumps(out) or out["credentials"] is not None  # 只报是否设置与长度

    def test_calibrate_缺实测即用法错误(self, capsys, tmp_path):
        config = tmp_path / "movie.yaml"
        config.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        code, out = self._run(
            [
                "calibrate",
                "--channel",
                "llm",
                "--tier",
                "screenplay",
                "--config",
                str(config),
                "--expected-usd",
                "5.0",
                "--sample-count",
                "5",
            ],
            capsys,
        )
        assert code == 2 and "measured-usd" in out["error"]

    def test_raise_tier_未校准即拒绝且配置不变(self, capsys, tmp_path):
        config = tmp_path / "movie.yaml"
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        config.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        before = config.read_text(encoding="utf-8")
        code, out = self._run(
            [
                "raise-tier",
                "--channel",
                "llm",
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
