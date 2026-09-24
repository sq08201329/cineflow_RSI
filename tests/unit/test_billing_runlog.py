"""渠道运行记录与 ≥7 天窗口机检（功能 019 / US3 / T1937）：契约 C15。

机检口径 = **覆盖 ∧ 连续双条件**（两项都由配置声明）：

- `covered_days` **只计 `source=real` 的日期**（`simulated`/`fallback` 日不算真实运行日）；
- `meets = covered_days ≥ min_window_days` **∧** `max_gap_days ≤ gap_tolerance_days`——
  "累计够天数但有断档"**恒不通过**（假绿必须被拒）；
- `continuous = not gaps`（**有无断档**，与容差无关）与 `meets` 并列呈现：容差放开时可以
  `meets=true` 而 `continuous=false`，通过不谎报为"连续"；`gaps` 逐段如实列出、**禁止插值补齐**；
- 未达标给出归因（覆盖差值、断档超容差差值、逐段 `gaps`）；
- 记录是**链式摘要**证据：改写/删除任一条即断链报错（不静默取）；当日 `sealed` 后追加拒绝；
- 日期归属 = `peak_windows.timezone`（与峰谷判定、额度 day 窗口同一日历）。
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from core.billing.budget import BudgetConfigError
from core.billing.runlog import (
    RUN_SOURCES,
    RunLogError,
    append_run,
    load_run,
    run_path,
    seal_run,
    window_coverage,
)

_BASE = dt.datetime(2026, 9, 1, 3, 0, tzinfo=dt.UTC)  # 渠道本地（Asia/Shanghai）= 09-01 11:00


def _fill(cfg, channel, root: Path, entries) -> None:
    for params in entries:
        append_run(channel, cfg=cfg, root=root, **params)


def _run_dates(root: Path, channel: str) -> list[str]:
    directory = root / channel / "runs"
    return sorted(path.stem for path in directory.glob("*.json")) if directory.is_dir() else []


class Test窗口机检:
    def test_连续夹具_覆盖与连续双条件同时满足(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("continuous")["entries"])

        coverage = window_coverage(channel, cfg=cfg, root=root, end="2026-09-08")

        assert coverage["covered_days"] == 8
        assert coverage["gaps"] == [] and coverage["max_gap_days"] == 0
        assert coverage["continuous"] is True and coverage["meets"] is True
        assert coverage["coverage_shortfall_days"] == 0 and coverage["gap_shortfall_days"] == 0
        assert coverage["reasons"] == []
        assert coverage["min_window_days"] == 7 and coverage["gap_tolerance_days"] == 0
        assert coverage["start"] == "2026-09-01" and coverage["end"] == "2026-09-08"
        assert coverage["covered_dates"] == [f"2026-09-{day:02d}" for day in range(1, 9)]

    def test_散点夹具_覆盖够但断档即不通过且缺口如实列出(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("scattered")["entries"])

        coverage = window_coverage(channel, cfg=cfg, root=root, end="2026-09-10")

        # 天数累计够（8 ≥ 7）仍不通过：断档 2 天 > 容差 0（假绿被拒）
        assert coverage["covered_days"] == 8
        assert coverage["max_gap_days"] == 2
        assert coverage["meets"] is False and coverage["continuous"] is False
        assert coverage["gaps"] == [{"from": "2026-09-05", "to": "2026-09-06", "days": 2}]
        assert coverage["gap_shortfall_days"] == 2 and coverage["coverage_shortfall_days"] == 0
        assert any("断档超容差" in reason for reason in coverage["reasons"])
        # **无插值补齐**：缺口那两天在记录面根本没有文件
        assert _run_dates(root, channel) == [
            "2026-09-01",
            "2026-09-02",
            "2026-09-03",
            "2026-09-04",
            "2026-09-07",
            "2026-09-08",
            "2026-09-09",
            "2026-09-10",
        ]
        for missing in ("2026-09-05", "2026-09-06"):
            assert not run_path(root, channel, missing).exists()

    def test_容差放开_meets_转真而缺口照旧可见(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("scattered")["entries"])

        strict = window_coverage(channel, cfg=cfg, root=root, end="2026-09-10")
        tolerant = window_coverage(
            channel, cfg=cfg, root=root, end="2026-09-10", gap_tolerance_days=2
        )

        assert tolerant["meets"] is True
        # 通过**不**谎报为连续：缺口与最长断档照旧落在产物里
        assert tolerant["continuous"] is False
        assert tolerant["gaps"] == strict["gaps"]
        assert tolerant["max_gap_days"] == strict["max_gap_days"] == 2
        assert tolerant["gap_shortfall_days"] == 0 and tolerant["reasons"] == []
        assert tolerant["note"] and "不插值" in tolerant["note"]

    def test_仅5天_覆盖不足并给出差值(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("sparse")["entries"])

        coverage = window_coverage(channel, cfg=cfg, root=root, end="2026-09-05")

        assert coverage["covered_days"] == 5 and coverage["meets"] is False
        assert coverage["continuous"] is True and coverage["gaps"] == []
        assert coverage["coverage_shortfall_days"] == 2
        assert any("覆盖不足" in reason for reason in coverage["reasons"])

    def test_回落与模拟日不计入真实运行日且回落必须带原因(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        entries = billing_runlog_factory("fallback")["entries"]
        _fill(cfg, channel, root, entries)

        coverage = window_coverage(channel, cfg=cfg, root=root, end="2026-09-03")

        # 三天都有记录（故无断档），但只有两天是 source=real ⇒ covered_days=2
        assert coverage["covered_days"] == 2 and coverage["continuous"] is True
        assert coverage["covered_dates"] == ["2026-09-01", "2026-09-03"]
        assert coverage["meets"] is False and coverage["coverage_shortfall_days"] == 5
        declared = load_run("2026-09-02", channel_id=channel, root=root)["entries"][0]
        assert declared["source"] == "fallback" and declared["fallback_reason"]
        # 回落是被**声明**的事实：缺原因即拒绝写入
        with pytest.raises(RunLogError, match="fallback_reason"):
            append_run(
                channel,
                cfg=cfg,
                root=root,
                moment=_BASE + dt.timedelta(days=4),
                stage="screenplay",
                source="fallback",
            )
        # 模拟日同样不算真实运行日
        append_run(
            channel,
            cfg=cfg,
            root=root,
            moment=_BASE + dt.timedelta(days=4),
            stage="screenplay",
            source="simulated",
        )
        assert window_coverage(channel, cfg=cfg, root=root, end="2026-09-05")["covered_days"] == 2

    def test_取值域与必填项_来源域外或缺环节即拒绝(self, tmp_path, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        assert RUN_SOURCES == ("real", "simulated", "fallback")
        with pytest.raises(RunLogError, match="运行来源"):
            append_run(
                channel,
                cfg=cfg,
                root=root,
                moment=_BASE,
                stage="screenplay",
                source="http",
            )
        with pytest.raises(RunLogError, match="stage"):
            append_run(channel, cfg=cfg, root=root, moment=_BASE, stage="", source="real")

    def test_渠道未登记即拒绝_不发明渠道(self, tmp_path, budget_config_factory):
        cfg = budget_config_factory()
        root = tmp_path / "runs"
        with pytest.raises(BudgetConfigError, match="未在 budget.channels 登记"):
            window_coverage("no-such-channel", cfg=cfg, root=root, end="2026-09-08")

    def test_日期归属取渠道时区而非UTC(self, tmp_path, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        # UTC 09-01 20:00 = Asia/Shanghai 09-02 04:00 ⇒ 记录落 09-02 分片
        append_run(
            channel,
            cfg=cfg,
            root=root,
            moment=dt.datetime(2026, 9, 1, 20, 0, tzinfo=dt.UTC),
            stage="screenplay",
            source="real",
        )
        assert _run_dates(root, channel) == ["2026-09-02"]
        payload = load_run("2026-09-02", channel_id=channel, root=root)
        assert payload["timezone"] == cfg.peak_windows.timezone == "Asia/Shanghai"
        # 窗口终点的带时区 datetime 同样按渠道日历归一
        assert (
            window_coverage(
                channel,
                cfg=cfg,
                root=root,
                end=dt.datetime(2026, 9, 2, 20, 0, tzinfo=dt.UTC),
            )["end"]
            == "2026-09-03"
        )

    def test_日期非法即报错(self, tmp_path, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        with pytest.raises(RunLogError, match="YYYY-MM-DD"):
            window_coverage(channel, cfg=cfg, root=tmp_path / "runs", end="2026/09/08")


class Test链式摘要与封存:
    def test_改写任一条即断链报错(self, tmp_path, budget_config_factory, billing_runlog_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("continuous")["entries"])

        target = run_path(root, channel, "2026-09-01")
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["entries"][0]["head_digest"] == payload["head_digest"]
        payload["entries"][0]["result"] = "failed"  # 改写一条（金额口径的"事实"被篡改）
        target.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(RunLogError, match="链校验失败"):
            load_run("2026-09-01", channel_id=channel, root=root)
        # 读取侧不静默取：窗口机检同样拒绝采信（被篡改的日期不得计入 covered_days）
        with pytest.raises(RunLogError, match="链校验失败"):
            window_coverage(channel, cfg=cfg, root=root, end="2026-09-08")

    def test_删除条目即断链报错(self, tmp_path, budget_config_factory, billing_runlog_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("continuous")["entries"])

        target = run_path(root, channel, "2026-09-01")
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["entries"] = payload["entries"][:-1]  # 只删尾条、不动 head_digest
        target.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(RunLogError, match="链校验失败"):
            load_run("2026-09-01", channel_id=channel, root=root)

    def test_链式摘要逐条绑定前一条(self, tmp_path, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        first = append_run(
            channel,
            cfg=cfg,
            root=root,
            moment=_BASE,
            stage="screenplay",
            source="real",
            result="ok",
        )
        second = append_run(
            channel,
            cfg=cfg,
            root=root,
            moment=_BASE + dt.timedelta(hours=1),
            stage="screenplay",
            source="real",
            result="ok",
        )
        assert first["head_digest"] != second["head_digest"]
        payload = load_run("2026-09-01", channel_id=channel, root=root)
        assert [entry["head_digest"] for entry in payload["entries"]] == [
            first["head_digest"],
            second["head_digest"],
        ]
        assert payload["head_digest"] == second["head_digest"]
        assert set(payload) == {
            "channel_id",
            "date",
            "timezone",
            "entries",
            "head_digest",
            "sealed",
        }

    def test_当日封存后追加拒绝(self, tmp_path, budget_config_factory, billing_runlog_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        root = tmp_path / "runs"
        _fill(cfg, channel, root, billing_runlog_factory("continuous")["entries"])

        sealed = seal_run("2026-09-01", channel_id=channel, root=root)
        assert sealed["sealed"] is True
        # 封存不改动既有 entries 与链
        after = load_run("2026-09-01", channel_id=channel, root=root)
        assert after["entries"] == sealed["entries"]

        with pytest.raises(RunLogError, match="sealed"):
            append_run(
                channel,
                cfg=cfg,
                root=root,
                moment=_BASE + dt.timedelta(hours=5),
                stage="screenplay",
                source="real",
            )

    def test_记录缺失即报错_不静默返回空(self, tmp_path, budget_config_factory):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        with pytest.raises(RunLogError, match="运行记录不存在"):
            load_run("2026-09-01", channel_id=channel, root=tmp_path / "runs")
