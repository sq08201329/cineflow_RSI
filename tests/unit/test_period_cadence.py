"""周期量纲与半开窗口单测（功能 020 契约 C1/C2/C3/C5，T2008 先行测试）。

- C1 周期标签由 cadence 派生：周级与 `core/calibration/rounds.iso_week_label` **逐字节相同**
  （该断言必须先于标签接线落地并常驻，`plan.md:265` 的风险缓解）；日级 ⇒ 日期形态；
  其余 cadence ⇒ `ValidationError`「未支持的 cadence」；
- C2 窗口统一半开 `[start, start + period_days)`：`end - start == period_days`，
  缺省窗口跨 cadence 天、`period_days + 1` 天窗口出现次数恒 0；
  `build_blind_list(..., period_days=)` 必填（缺参 ⇒ `TypeError`）、跨度不符 ⇒ `ValidationError`；
- C3 快照 = 周期物化：同周期多轮物化后该目录文件数**不随轮数增长**；
- C5 口径进产物：报告写 `window_semantics == "half_open"` + `period_days` + `window{start,end}`；
  取值域外 ⇒ **拒绝落盘**；跨口径变更日的窗口在 `note` 显式标注。
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.evaluators.errors import ValidationError

_MOVIE_YAML = Path(__file__).resolve().parents[2] / "configs" / "movie.yaml"
_WEEKLY_SAMPLES = (
    # 既有周级夹具样本（测试与规格里真实出现过的周期端点，如 2026-09-14~2026-09-20 = 2026-W38）
    "2026-09-14",
    "2026-09-20",
    "2026-09-25",
    # ISO 年边界：跨年周与第 1 周
    "2025-12-29",
    "2026-01-01",
    "2026-01-04",
    "2026-12-31",
    "2027-01-03",
)


def _all_days(start: str, end: str):
    day = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while day <= stop:
        yield day
        day += timedelta(days=1)


class TestC1标签由cadence派生:
    def test_周级与_iso_week_label_逐字节相同(self):
        """① 逐字节相同：`period_label(day, 7) == iso_week_label(day)`（既有读取点路径）。"""
        from core.calibration.periods import period_label
        from core.calibration.rounds import iso_week_label

        samples = [day.isoformat() for day in _all_days("2025-12-15", "2027-01-10")]
        samples += [s for s in _WEEKLY_SAMPLES if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)]
        assert len(samples) > 300
        for day in samples:
            assert period_label(day, 7) == iso_week_label(day), day

    def test_日级标签为日期形态(self):
        """② 日级 ⇒ 日期形态（不是 ISO 周）。"""
        from core.calibration.periods import period_label

        assert period_label("2026-09-25", 1) == "2026-09-25"
        assert period_label(date(2026, 9, 25), 1) == "2026-09-25"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", period_label("2026-09-25", 1))
        assert re.fullmatch(r"\d{4}-W\d{2}", period_label("2026-09-25", 7))

    def test_标签正则形态(self):
        """③ 标签正则：周级逐字节等于 drift_metrics 的现值；日级不匹配周级正则。"""
        from core.calibration.drift_metrics import _PERIOD_RE
        from core.calibration.periods import period_regex

        assert period_regex(7).pattern == r"^(\d{4})-W(\d{2})$"
        assert period_regex(7).pattern == _PERIOD_RE.pattern
        assert period_regex(1).fullmatch("2026-09-25") is not None
        assert period_regex(1).fullmatch("2026-W39") is None
        assert period_regex(7).fullmatch("2026-09-25") is None

    def test_往返恒等与_cadence_双射(self):
        """④ 往返恒等 + 标签形态 ⇒ cadence 双射（n ∈ {1,7}）。"""
        from core.calibration.periods import cadence_of, period_label, period_start

        for period_days in (1, 7):
            for day in ("2026-09-14", "2026-09-20", "2026-09-25", "2026-01-01"):
                label = period_label(day, period_days)
                assert cadence_of(label) == period_days
                assert period_label(period_start(label, period_days), period_days) == label

    def test_未支持的_cadence_显式拒绝(self):
        """⑤ 取值域只有 {1,7}：其余值一律拒绝（不发明第三档量纲）。"""
        from core.calibration.periods import period_label, period_regex, period_start, period_window

        for bad in (2, 3, 30, 0, -1):
            with pytest.raises(ValidationError, match="未支持的 cadence"):
                period_label("2026-09-25", bad)
            with pytest.raises(ValidationError, match="未支持的 cadence"):
                period_window(date(2026, 9, 25), bad)
            with pytest.raises(ValidationError, match="未支持的 cadence"):
                period_regex(bad)
            with pytest.raises(ValidationError, match="未支持的 cadence"):
                period_start("2026-W39", bad)

    def test_标签与_cadence_不匹配即报错(self):
        """反例：日级标签喂给周级派生、非法或不存在的标签一律报错（不猜测）。"""
        from core.calibration.periods import cadence_of, period_start

        with pytest.raises(ValidationError, match="未支持的 cadence"):
            period_start("2026-09-25", 7)
        with pytest.raises(ValidationError, match="未支持的 cadence"):
            period_start("2026-W39", 1)
        for bad in ("2025-W53", "2026-W99", "2026-W00", "2026-13-01", "2026/09/14", "", "W39"):
            with pytest.raises(ValidationError):
                cadence_of(bad)

    def test_缺失_json_标签不存在(self):
        """不存在的 ISO 周（2025-W53）⇒ 报错（不静默解析）。"""
        from core.calibration.periods import period_start

        with pytest.raises(ValidationError):
            period_start("2025-W53", 7)
        assert period_start("2026-W39", 7).isoformat() == "2026-09-21"
        # 2026 确有第 53 周 ⇒ 合法（不误拒真实存在的 ISO 周）
        assert period_start("2026-W53", 7).isoformat() == "2026-12-28"


class TestC2半开窗口:
    def test_窗口恒为半开且跨度等于_cadence(self):
        """⑥ `period_window` = [start, start + period_days)，按日历日。"""
        from core.calibration.periods import period_window

        assert period_window(date(2026, 9, 14), 7) == (date(2026, 9, 14), date(2026, 9, 21))
        assert period_window(date(2026, 9, 25), 1) == (date(2026, 9, 25), date(2026, 9, 26))
        for period_days in (1, 7):
            for day in ("2026-09-14", "2026-09-20", "2026-09-25"):
                start, end = period_window(date.fromisoformat(day), period_days)
                assert (end - start).days == period_days

    def test_缺省窗口跨_cadence_天且无加一天窗口(self):
        """⑦ 缺省窗口 = 含首尾跨 period_days 天；`period_days + 1` 天窗口出现次数恒 0。"""
        from core.calibration.periods import (
            default_period_bounds,
            period_window,
            window_timestamps,
        )
        from core.calibration.selection import _period_window

        today = date(2026, 9, 25)
        spans: list[int] = []
        for period_days in (1, 7):
            start, end = default_period_bounds(None, None, period_days, today=today)
            assert end == today.isoformat()
            assert (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 == period_days
            spans.append((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
            # 秒级适配层与半开窗口同一口径
            start_ts, end_ts = _period_window(start, end, period_days)
            assert (end_ts - start_ts) / 86400 == period_days
            spans.append(int((end_ts - start_ts) / 86400))
            # 半开窗口端点（裸函数）与缺省窗口自洽
            start_day, open_end = period_window(date.fromisoformat(start), period_days)
            assert open_end.isoformat() == (date.fromisoformat(end) + timedelta(days=1)).isoformat()
            assert window_timestamps(start, period_days)[1] == end_ts
        assert spans == [1, 1, 7, 7]
        assert [span for span in spans if span == 3] == []  # 周级 +1 天窗口：0 次
        assert [span for span in spans if span == 2] == []  # 日级 +1 天窗口：0 次

    def test_缺省窗口在_cadence_为_7_时与旧含首尾口径同端点(self):
        """周级缺省窗口的**秒级端点**与旧口径（period_end + 1 天）逐字节相同。"""
        from datetime import UTC, datetime

        from core.calibration.periods import default_period_bounds
        from core.calibration.selection import _period_window

        today = date(2026, 9, 20)
        start, end = default_period_bounds(None, None, 7, today=today)
        assert (start, end) == ("2026-09-14", "2026-09-20")
        assert _period_window(start, end, 7) == (
            datetime.fromisoformat("2026-09-14").replace(tzinfo=UTC).timestamp(),
            datetime.fromisoformat("2026-09-20").replace(tzinfo=UTC).timestamp() + 86400.0,
        )

    def test_build_blind_list_的_period_days_必填(self):
        """⑧ `build_blind_list(..., period_days=)` 必填：缺参 ⇒ `TypeError`（不取码内默认）。"""
        from core.calibration.selection import build_blind_list

        with pytest.raises(TypeError):
            build_blind_list(
                None,
                agent_id="visual",
                period_start="2026-09-14",
                period_end="2026-09-20",
                top_k=3,
                data_dir="ghost",
            )

    def test_窗口跨度与_cadence_不符即报错(self):
        """⑨ 8 天跨度（含首尾）配 period_days=7 ⇒ `ValidationError`（禁止静默截断）。"""
        from core.calibration.selection import build_blind_list

        with pytest.raises(ValidationError):
            build_blind_list(
                None,
                agent_id="visual",
                period_start="2026-09-14",
                period_end="2026-09-21",
                top_k=3,
                data_dir="ghost",
                period_days=7,
            )
        with pytest.raises(ValidationError):
            build_blind_list(
                None,
                agent_id="visual",
                period_start="2026-09-20",
                period_end="2026-09-14",
                top_k=3,
                data_dir="ghost",
                period_days=7,
            )
        with pytest.raises(ValidationError):
            build_blind_list(
                None,
                agent_id="visual",
                period_start="2026/09/14",
                period_end="2026-09-20",
                top_k=3,
                data_dir="ghost",
                period_days=7,
            )


class TestC3快照周期物化:
    def test_同周期多轮物化不增文件数(self, tmp_path):
        """C3：快照主键 =（评估器, 周期）⇒ 目录文件数不随轮数增长。"""
        from core.calibration.ledger import write_anchor_snapshots
        from core.calibration.models import PairingRecord

        data_dir = tmp_path / "calibration"
        for round_index in range(3):
            pairs = [
                PairingRecord(
                    anchor_id=f"r{round_index}-a{i}",
                    evaluator_key="proxy.aesthetic@1.0.0",
                    anchor_score=0.5 + 0.05 * i,
                    auto_score=0.5,
                )
                for i in range(4)
            ]
            write_anchor_snapshots(data_dir, "visual", "2026-W39", pairs)
        snapshots = sorted((data_dir / "snapshots").rglob("*.json"))
        assert len(snapshots) == 1
        assert snapshots[0].name == "2026-W39.json"


class TestC9覆盖窗口口径:
    """`coverage_window` = "覆盖 ∧ 连续"判定的**唯一实现**（019 口径的纯函数化）。

    输入即归属日集合与两个阈值：有记录的日期参与断档枚举，含 `real` 来源者计覆盖
    （模拟/回落日不算真实运行日），缺口**逐段如实列出**、**不插值补齐**。
    """

    @staticmethod
    def _days(start: str, count: int, *, source: str = "real"):
        first = date.fromisoformat(start)
        return {first + timedelta(days=index): {source} for index in range(count)}

    def test_连续十五天真实即达标(self):
        """短剧态（min_window_days=14、gap_tolerance_days=0）：连续 15 天真实 ⇒ meets。"""
        from core.calibration.periods import coverage_window

        days = self._days("2026-09-01", 15)
        coverage = coverage_window(
            days, end=date(2026, 9, 15), min_window_days=14, gap_tolerance_days=0
        )
        assert coverage["covered_days"] == 15
        assert coverage["gaps"] == [] and coverage["max_gap_days"] == 0
        assert coverage["continuous"] is True and coverage["meets"] is True
        assert coverage["coverage_shortfall_days"] == 0 and coverage["gap_shortfall_days"] == 0
        assert coverage["reasons"] == []
        assert coverage["covered_dates"][0] == min(days).isoformat()  # 起始端点由归属日派生

    def test_十五天真实缺两天则不达标且缺口逐段列出(self):
        """累计够天数但有断档 ⇒ `meets is False`，缺口**逐段**（from/to/days）如实列出。"""
        from core.calibration.periods import coverage_window

        days = self._days("2026-09-01", 15)
        for missing in (date(2026, 9, 4), date(2026, 9, 11)):
            del days[missing]
        coverage = coverage_window(
            days, end=date(2026, 9, 15), min_window_days=14, gap_tolerance_days=0
        )
        assert coverage["covered_days"] == 13
        assert coverage["continuous"] is False
        assert coverage["meets"] is False
        assert [gap["days"] for gap in coverage["gaps"]] == [1, 1]
        assert coverage["gaps"] == [
            {"from": "2026-09-04", "to": "2026-09-04", "days": 1},
            {"from": "2026-09-11", "to": "2026-09-11", "days": 1},
        ]
        assert coverage["max_gap_days"] == 1 and coverage["gap_shortfall_days"] == 1
        assert "断档超容差" in "；".join(coverage["reasons"])
        # 不插值：缺口日期不得出现在覆盖列表里
        assert set(coverage["covered_dates"]) & {gap["from"] for gap in coverage["gaps"]} == set()
        assert set(coverage["covered_dates"]) <= {day.isoformat() for day in days}

    def test_长断档逐段报出且段数等于缺口段数(self):
        """一段 3 天断档 ⇒ 单段 `days == 3`（逐段报，不合并成"最长断档"一句话）。"""
        from core.calibration.periods import coverage_window

        days = self._days("2026-09-01", 18)
        for missing in (date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 7)):
            del days[missing]
        coverage = coverage_window(
            days, end=date(2026, 9, 18), min_window_days=14, gap_tolerance_days=0
        )
        assert coverage["covered_days"] == 15
        assert coverage["gaps"] == [{"from": "2026-09-05", "to": "2026-09-07", "days": 3}]
        assert coverage["max_gap_days"] == 3
        # 容差放开后 meets 可以真而 continuous 仍为假（两者并列呈现，不互相冒充）
        lenient = coverage_window(
            days, end=date(2026, 9, 18), min_window_days=14, gap_tolerance_days=3
        )
        assert lenient["meets"] is True and lenient["continuous"] is False
        strict = coverage_window(
            days, end=date(2026, 9, 18), min_window_days=14, gap_tolerance_days=0
        )
        assert strict["meets"] is False  # 同一窗口：容差不同 ⇒ 判定不同，缺口逐段可归因
        assert strict["gaps"] == lenient["gaps"]

    def test_模拟来源不计入覆盖(self):
        """模拟/回落日不算真实运行日：`covered_days` 只计 `real`。"""
        from core.calibration.periods import coverage_window

        days = self._days("2026-09-01", 15, source="simulated")
        coverage = coverage_window(
            days, end=date(2026, 9, 15), min_window_days=14, gap_tolerance_days=0
        )
        assert coverage["covered_days"] == 0
        assert coverage["meets"] is False
        assert coverage["continuous"] is True  # 有记录（非断档）但无真实覆盖

    def test_电影态下限七天复跑一遍(self):
        """两形态各有下限：电影态 `min_window_days=7` 连续 7 天 ⇒ 达标。"""
        from core.calibration.periods import coverage_window

        days = self._days("2026-09-01", 7)
        coverage = coverage_window(
            days, end=date(2026, 9, 7), min_window_days=7, gap_tolerance_days=0
        )
        assert coverage["covered_days"] == 7 and coverage["meets"] is True
        short = coverage_window(
            days, end=date(2026, 9, 7), min_window_days=14, gap_tolerance_days=0
        )
        assert short["meets"] is False  # 短剧态 14 天下限：同样 7 天不达标
        assert short["coverage_shortfall_days"] == 7

    def test_无记录窗口不成判且按_end_截断(self):
        """`days` 中晚于 `end` 的记录不入窗口；空输入 ⇒ 覆盖 0、缺口为整段。"""
        from core.calibration.periods import coverage_window

        days = self._days("2026-09-01", 10)
        coverage = coverage_window(
            days, end=date(2026, 9, 3), min_window_days=1, gap_tolerance_days=0
        )
        assert coverage["end"] == "2026-09-03"
        assert coverage["covered_days"] == 3
        empty = coverage_window({}, end=date(2026, 9, 3), min_window_days=1, gap_tolerance_days=0)
        assert empty["covered_days"] == 0
        assert empty["gaps"] == [{"from": "2026-09-03", "to": "2026-09-03", "days": 1}]
        assert "coverage_window" not in coverage  # 纯函数：不落盘、不返回自身


class TestC5口径进产物:
    def _build(self, data_dir, **overrides):
        return self._build_period(data_dir, "2026-W39", **overrides)

    def _build_period(self, data_dir, period, **overrides):
        from core.calibration.report import build_report

        kwargs = {
            "target": 0.6,
            "window_semantics": "half_open",
            "window_semantics_change_date": "2026-09-25",
        }
        kwargs.update(overrides)
        return build_report(data_dir, period, **kwargs)

    def test_报告写窗口口径且窗口与标签自洽(self, tmp_path):
        """⑩ 口径进产物：`window_semantics` / `period_days` / `window{start,end,period_days}`。"""
        from core.calibration.periods import cadence_of, period_start

        report = self._build(tmp_path)
        assert report["window_semantics"] == "half_open"
        assert report["period_days"] == cadence_of(report["period"]) == 7
        window = report["window"]
        assert window["start"] == period_start(report["period"], 7).isoformat()
        assert window["period_days"] == 7
        assert (
            date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])
        ).days == window["period_days"]
        assert report["window_semantics_change_date"] == "2026-09-25"
        assert report["run_id"] is None

    def test_取值域外拒绝落盘(self, tmp_path):
        """⑩ 取值域外 ⇒ 该产物拒绝落盘（不是落盘后补）。"""
        with pytest.raises(ValidationError):
            self._build(tmp_path, window_semantics="inclusive")
        with pytest.raises(ValidationError):
            self._build(tmp_path, window_semantics="half-open")
        assert not (tmp_path / "reports" / "2026-W39.json").exists()
        assert list((tmp_path / "reports").glob("*.json")) == []

    def test_跨口径变更日的窗口必须标注(self, tmp_path):
        """C5：窗口横跨变更日 ⇒ `note` 含该变更日与「口径变更日」字样（禁止静默）。"""
        report = self._build(tmp_path)
        assert "2026-09-25" in report["note"]
        assert "口径变更日" in report["note"]
        # 周级：窗口 [2026-09-21, 2026-09-28) 含 2026-09-25 ⇒ spans_change_date 为真
        assert report["window"]["start"] < "2026-09-25" < report["window"]["end"]
        # 变更日之后的窗口：口径一致 ⇒ 不误标"横跨"
        after = self._build_period(tmp_path, "2026-W40")
        assert after["window"]["start"] >= "2026-09-25"
        assert "横跨" not in after["note"]
        # 变更日之前的窗口：标注"早于"（读者知道自己在比什么）
        before = self._build_period(tmp_path, "2026-W38")
        assert before["window"]["end"] <= "2026-09-25"
        assert "早于" in before["note"]

    def test_run_id_决定路径且缺省走兼容别名(self, tmp_path):
        """路径规则单点：给定 run_id ⇒ `reports/{period}-{run_id}.json`；缺省 ⇒ 兼容别名。"""
        from core.calibration.report import latest_report_path, report_path

        alias = report_path(tmp_path, "2026-W39", None)
        assert alias == tmp_path / "reports" / "2026-W39.json"
        assert report_path(tmp_path, "2026-W39", "r1") == tmp_path / "reports" / "2026-W39-r1.json"
        assert latest_report_path(tmp_path, "2026-W39") is None
        self._build(tmp_path, run_id="r1")
        assert not alias.exists()
        assert (tmp_path / "reports" / "2026-W39-r1.json").is_file()
        assert latest_report_path(tmp_path, "2026-W39") == tmp_path / "reports" / "2026-W39-r1.json"
        self._build(tmp_path)
        assert alias.is_file()
        assert latest_report_path(tmp_path, "2026-W39") == tmp_path / "reports" / "2026-W39-r1.json"

    def test_缺省别名回退可被读取点读到(self, tmp_path):
        """别名回退：只有兼容别名（`run_id=None` 写出）时，读取口取到的就是它。"""
        from core.calibration.report import latest_report_path, report_path

        self._build(tmp_path)  # run_id 缺省 ⇒ 只写兼容别名
        alias = report_path(tmp_path, "2026-W39")
        assert alias.is_file()
        assert latest_report_path(tmp_path, "2026-W39") == alias
        assert json.loads(alias.read_text(encoding="utf-8"))["run_id"] is None
        # 另一周期不受影响（按周期定位，不跨周期取文件）
        assert latest_report_path(tmp_path, "2026-W40") is None

    def test_同周期多轮取最新且规则确定(self, tmp_path):
        """同周期多份轮级报告 ⇒ 取 `run_id` 字符串序末者（确定性规则，与文件系统顺序无关）。"""
        from core.calibration.report import latest_report_path

        # uuid7 形状的轮标识（时间有序）：字符串序 == 写入序 ⇒ 末者即最新
        first = "01a0d6ac-0000-7000-8000-000000000001"
        second = "01a0d6ac-0000-7000-8000-000000000002"
        self._build(tmp_path, run_id=first)
        assert latest_report_path(tmp_path, "2026-W39").name == f"2026-W39-{first}.json"
        self._build(tmp_path, run_id=second)
        assert latest_report_path(tmp_path, "2026-W39").name == f"2026-W39-{second}.json"
        # 再写一份"字符串序更靠后"的轮报告（不依赖 mtime 与目录遍历顺序）
        self._build(tmp_path, run_id="01a0d6ac-0000-7000-8000-00000000000a")
        newest = tmp_path / "reports" / "2026-W39-01a0d6ac-0000-7000-8000-00000000000a.json"
        assert latest_report_path(tmp_path, "2026-W39") == newest
        # 末者缺失时回退到**存在的**次末者
        newest.unlink()
        assert latest_report_path(tmp_path, "2026-W39").name == f"2026-W39-{second}.json"

    def test_缺口径参数即_TypeError(self, tmp_path):
        """T2024①：两个新增**必填**关键字参数缺任一 ⇒ `TypeError`。"""
        from core.calibration.report import build_report

        with pytest.raises(TypeError):
            build_report(tmp_path, "2026-W39", target=0.6)
        with pytest.raises(TypeError):
            build_report(tmp_path, "2026-W39", target=0.6, window_semantics="half_open")
        with pytest.raises(TypeError):
            build_report(
                tmp_path,
                "2026-W39",
                target=0.6,
                window_semantics_change_date="2026-09-25",
            )


class Test配置声明口径:
    def test_两形态均声明窗口口径(self):
        """FR-014：两形态都声明 `window_semantics` / `window_semantics_change_date`。"""
        from core.calibration.config import CalibrationConfig

        for name in ("movie.yaml", "shortdrama.yaml"):
            config = CalibrationConfig.from_yaml(_MOVIE_YAML.parent / name)
            assert config.window_semantics == "half_open"
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", config.window_semantics_change_date)

    def test_缺口径键即报错(self, tmp_path):
        """T2026：`from_dict` 必需读取 ⇒ 缺项即报错（不取码内默认）。"""
        import yaml

        from core.calibration.config import CalibrationConfig
        from core.calibration.errors import CalibrationConfigError

        section = yaml.safe_load(_MOVIE_YAML.read_text(encoding="utf-8"))["calibration"]
        for missing in ("window_semantics", "window_semantics_change_date"):
            path = tmp_path / f"missing-{missing}.yaml"
            path.write_text(
                yaml.safe_dump(
                    {"calibration": {k: v for k, v in section.items() if k != missing}},
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            with pytest.raises(CalibrationConfigError, match=missing):
                CalibrationConfig.from_yaml(path)

    def test_window_semantics_取值域单元素(self, tmp_path):
        """取值域单元素：其它值 ⇒ 配置报错（不静默接受）。"""
        import yaml

        from core.calibration.config import CalibrationConfig
        from core.calibration.errors import CalibrationConfigError

        section = yaml.safe_load(_MOVIE_YAML.read_text(encoding="utf-8"))["calibration"]
        for bad in ("inclusive", "half-open", "", None):
            path = tmp_path / "bad.yaml"
            path.write_text(
                yaml.safe_dump(
                    {"calibration": {**section, "window_semantics": bad}}, allow_unicode=True
                ),
                encoding="utf-8",
            )
            with pytest.raises(CalibrationConfigError, match="window_semantics"):
                CalibrationConfig.from_yaml(path)
