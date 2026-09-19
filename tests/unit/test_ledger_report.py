"""校准台账与信度报告单测（功能 010 US2 / T518，先于实现编写；契约 C6 场景 1~2）。

- 台账 append-only：两轮追加后首轮行逐字节不变；
- 最新快照读取（供 US3 提案生效时填 calibration 字段）；
- 报告 schema 四要素：period / agents / target / alerts；per agent per evaluator 的
  相关系数 + samples + meets_target；负相关入 alerts；target 来自配置。
"""

import json

from core.calibration.ledger import append_ledger, read_latest
from core.calibration.models import BiasRecord
from core.calibration.report import build_report


def _record(key, period, *, samples=5, r=None, tau=None, shift=None, note=""):
    return BiasRecord(
        evaluator_key=key,
        period=period,
        samples=samples,
        mean_shift=shift,
        pearson_r=r,
        kendall_tau=tau,
        note=note,
    )


def _ledger_path(data_dir, agent_id, evaluator_id):
    return data_dir / "ledger" / agent_id / f"{evaluator_id}.jsonl"


class Test台账AppendOnly:
    def test_两轮追加首轮行逐字节不变(self, calibration_data_dir):
        first = _record("proxy.aesthetic@1.0.0", "2026-W38", r=0.55, shift=0.1)
        append_ledger(calibration_data_dir, "visual", [first])
        path = _ledger_path(calibration_data_dir, "visual", "proxy.aesthetic")
        lines_before = path.read_text(encoding="utf-8").splitlines(keepends=True)
        assert len(lines_before) == 1

        second = _record("proxy.aesthetic@1.0.0", "2026-W39", r=0.62, shift=0.05)
        append_ledger(calibration_data_dir, "visual", [second])
        lines_after = path.read_text(encoding="utf-8").splitlines(keepends=True)
        assert len(lines_after) == 2
        assert lines_after[0] == lines_before[0]  # 首轮行逐字节不变

    def test_每行是合法_bias_record_json(self, calibration_data_dir):
        record = _record("judge.cinematic@1.0.0", "2026-W39", tau=0.7)
        append_ledger(calibration_data_dir, "visual", [record])
        path = _ledger_path(calibration_data_dir, "visual", "judge.cinematic")
        payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert payload["evaluator_key"] == "judge.cinematic@1.0.0"
        assert payload["kendall_tau"] == 0.7
        assert payload["pearson_r"] is None


class Test最新快照读取:
    def test_read_latest_取末行(self, calibration_data_dir):
        for period, r in (("2026-W38", 0.5), ("2026-W39", 0.62)):
            append_ledger(
                calibration_data_dir, "visual", [_record("proxy.aesthetic@1.0.0", period, r=r)]
            )
        latest = read_latest(calibration_data_dir, "visual", "proxy.aesthetic")
        assert latest["period"] == "2026-W39"
        assert latest["pearson_r"] == 0.62

    def test_无台账返回_none(self, calibration_data_dir):
        assert read_latest(calibration_data_dir, "visual", "proxy.ghost") is None


class Test信度报告:
    def _seed(self, data_dir):
        append_ledger(
            data_dir,
            "visual",
            [
                _record("proxy.aesthetic@1.0.0", "2026-W39", r=0.62, shift=0.05),
                _record("judge.cinematic@1.0.0", "2026-W39", tau=0.5),
            ],
        )
        append_ledger(
            data_dir, "promo", [_record("proxy.ctr_history@1.0.0", "2026-W39", r=0.81)]
        )

    def test_schema_四要素与达标口径(self, calibration_data_dir):
        self._seed(calibration_data_dir)
        report = build_report(calibration_data_dir, "2026-W39", target=0.6)
        assert set(report) == {"period", "agents", "target", "alerts"}
        assert report["period"] == "2026-W39"
        assert report["target"] == 0.6
        visual = report["agents"]["visual"]
        entry = visual["proxy.aesthetic@1.0.0"]
        assert entry["pearson_r"] == 0.62
        assert entry["samples"] == 5
        assert entry["meets_target"] is True
        judge_entry = visual["judge.cinematic@1.0.0"]
        assert judge_entry["kendall_tau"] == 0.5
        assert judge_entry["meets_target"] is False  # 0.5 < 0.6
        assert report["agents"]["promo"]["proxy.ctr_history@1.0.0"]["meets_target"] is True
        assert report["alerts"] == []

    def test_负相关入_alerts(self, calibration_data_dir):
        append_ledger(
            calibration_data_dir,
            "visual",
            [_record("proxy.aesthetic@1.0.0", "2026-W39", r=-0.4, note="负相关")],
        )
        report = build_report(calibration_data_dir, "2026-W39", target=0.6)
        assert report["alerts"] == [
            {"evaluator": "proxy.aesthetic@1.0.0", "reason": "negative_correlation"}
        ]
        entry = report["agents"]["visual"]["proxy.aesthetic@1.0.0"]
        assert entry["meets_target"] is False

    def test_样本不足记录_meets_target_为_false(self, calibration_data_dir):
        append_ledger(
            calibration_data_dir,
            "visual",
            [_record("proxy.aesthetic@1.0.0", "2026-W39", samples=2, note="样本不足")],
        )
        report = build_report(calibration_data_dir, "2026-W39", target=0.6)
        entry = report["agents"]["visual"]["proxy.aesthetic@1.0.0"]
        assert entry["pearson_r"] is None
        assert entry["samples"] == 2
        assert entry["meets_target"] is False

    def test_报告落盘(self, calibration_data_dir):
        self._seed(calibration_data_dir)
        build_report(calibration_data_dir, "2026-W39", target=0.6)
        path = calibration_data_dir / "reports" / "2026-W39.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["period"] == "2026-W39"
        assert set(payload["agents"]) == {"visual", "promo"}

    def test_只统计本周期记录(self, calibration_data_dir):
        append_ledger(
            calibration_data_dir,
            "visual",
            [_record("proxy.aesthetic@1.0.0", "2026-W38", r=0.3)],
        )
        report = build_report(calibration_data_dir, "2026-W39", target=0.6)
        assert "visual" not in report["agents"]  # 上周期的记录不进本周期报告
