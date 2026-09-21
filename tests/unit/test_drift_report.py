"""漂移监控报表与信度联动单测（功能 012 US3 / T1118，先于实现编写；契约 C6/C7/C8）。

- C6 场景 1：多评估器多周期 → items 齐全（指标序列 / 基线引用 / 阈值 / 当前状态 / 处置记录）；
  报表落盘 `drift/reports/{period}.json`；
- C6 场景 2：已处置项 → 状态与处置记录（人/时间/理由/动作）如实呈现；
- C6 场景 3：无检测记录的评估器 → 标注"无数据"（不伪造）；范围外（proxy/rule）→
  标注"非 judge 类未纳入检测"；
- C7：双信号（漂移 ∧ 010 信度低于 target）→ 强化告警（级别升级 + `double_signal: true`）；
  单信号（仅漂移或仅信度下降）→ 常规告警（不误升级别）；开关关闭时不升级；
- C7 场景 3：F6 ScoreConflict **附注口径**——读约定持久化来源（最近一次命中分布文件）；
  无持久化来源时字段为空并注明"无持久化来源"，**附注不参与阈值判定**（不产生/不升级告警）；
- C8 只读：报表只写 drift/reports，010 产物与检测记录零写入；同输入两次逐字节一致。
"""

import json
from dataclasses import replace
from pathlib import Path

from core.calibration.drift_metrics import detect_drift, record_path
from core.calibration.drift_models import DriftAction, DriftConclusion
from core.calibration.drift_report import (
    SCORE_CONFLICT_SOURCE_RELPATH,
    build_report,
    score_conflict_note,
)
from core.calibration.drift_status import dispose, register_suspect
from core.calibration.report import build_report as build_reliability_report

_KEY = "judge.cinematic@1.0.0"
_AGENT = "visual"
_PERIOD = "2026-W39"
_AT = "2026-09-21T10:00:00+00:00"


def _detect(
    drift_data_dir,
    drift_config,
    drift_sequence_writer,
    variant,
    *,
    agent_id=_AGENT,
    evaluator_key=_KEY,
    periods=("2026-W38", "2026-W39"),
    only=None,
):
    drift_sequence_writer(variant, agent_id=agent_id, evaluator_key=evaluator_key)
    metrics = None
    for period in periods:
        if only is not None and period != only:
            continue
        metrics = detect_drift(agent_id, evaluator_key, period, drift_config, drift_data_dir)
    return metrics


def _reliability(
    drift_data_dir,
    write_calibration_ledger,
    *,
    period=_PERIOD,
    agent_id=_AGENT,
    evaluator_key=_KEY,
    tau=0.3,
    target=0.6,
):
    """010 信度报告（judge 走 kendall_tau）：默认低于目标（信度下降信号）。"""
    write_calibration_ledger(
        [
            {
                "evaluator_key": evaluator_key,
                "period": period,
                "samples": 12,
                "kendall_tau": tau,
            }
        ],
        agent_id=agent_id,
    )
    return build_reliability_report(drift_data_dir, period, target=target)


def _item(report, evaluator_key=_KEY, agent_id=_AGENT):
    for item in report.items:
        if item["agent_id"] == agent_id and item["evaluator_key"] == evaluator_key:
            return item
    raise AssertionError(f"报表缺 item：{agent_id}/{evaluator_key}")


def _fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TestC6场景1_items齐全:
    def test_多评估器多周期_items_齐全(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        write_drift_snapshots,
        write_calibration_ledger,
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable")
        # 非 judge 类（proxy）只有快照、不检测 → 标注未纳入
        write_drift_snapshots(
            {"2026-W39": [0.5, 0.6, 0.7]}, agent_id=_AGENT, evaluator_key="proxy.aesthetic@1.0.0"
        )
        # 另一 Agent 的 judge
        _detect(
            drift_data_dir,
            drift_config,
            drift_sequence_writer,
            "stable",
            agent_id="screenplay",
            evaluator_key="judge.dramatic_tension@1.0.0",
        )
        # 010 台账版本记录（proxy 无检测记录 → 版本取自台账；检测在其后补写不影响判定）
        write_calibration_ledger(
            [
                {
                    "evaluator_key": "proxy.aesthetic@1.0.0",
                    "period": _PERIOD,
                    "samples": 3,
                    "pearson_r": 0.8,
                }
            ],
            agent_id=_AGENT,
        )
        report = build_report(_PERIOD, drift_config, drift_data_dir)

        assert report.period == _PERIOD
        assert report.detector_version.startswith("drift_detector@1.0.0+")
        assert [item["agent_id"] for item in report.items] == ["screenplay", "visual", "visual"]
        assert report.double_signal_rules == {
            "enabled": True,
            "reliability_target": 0.6,
            "base_level": "warning",
            "escalated_level": "critical",
        }

        judge = _item(report)
        # 指标序列（周期 × 记录）：本周期及以前的全部检测记录
        assert [entry["period"] for entry in judge["metrics"]] == ["2026-W38", "2026-W39"]
        assert {entry["verdict"] for entry in judge["metrics"]} == {"normal"}
        assert all(
            set(entry) >= {"psi", "quantile_shifts", "samples"} for entry in judge["metrics"]
        )
        assert judge["baseline"] == "2026-W34..2026-W38"  # 基线引用（最近一条）
        assert judge["thresholds"] == {
            "psi": 0.2,
            "quantile": 0.1,
            "min_samples": 3,
            "window": 5,
        }
        assert judge["status"]["status"] == "normal"  # 未登记 → 默认 normal
        assert judge["dispositions"] == []
        assert judge["data_state"] == "ok"
        assert judge["detector_version"] == report.detector_version

        proxy = _item(report, "proxy.aesthetic@1.0.0")
        assert proxy["data_state"] == "out_of_scope"
        assert "非 judge 类未纳入" in proxy["note"]
        assert proxy["metrics"] == []

    def test_报表落盘路径与内容(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_drift_snapshots
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable")
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        path = drift_data_dir / "drift" / "reports" / f"{_PERIOD}.json"
        assert path.is_file()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["period"] == _PERIOD
        assert payload["detector_version"] == report.detector_version
        assert payload["items"] == [dict(item) for item in report.items]
        assert payload["residual_signals"] == report.residual_signals
        assert payload["alerts"] == [dict(alert) for alert in report.alerts]

    def test_报表期取自检测记录口径(self, drift_data_dir, drift_config, drift_sequence_writer):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable", only="2026-W38")
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert [entry["period"] for entry in judge["metrics"]] == ["2026-W38"]  # 只含 ≤ 报表期
        assert "本期无记录" in judge["note"]  # 如实标注：指标来自更早周期


class TestC6场景2_已处置项如实呈现:
    def test_处置状态与留痕如实呈现(self, drift_data_dir, drift_config, drift_sequence_writer):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="校准负责人",
            reason="分布持续右移，人评锚点同步走低",
            action=DriftAction.REANCHOR,
            at="2026-09-21T11:00:00+00:00",
        )
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert judge["status"]["status"] == "confirmed_drift"
        assert judge["status"]["disposition_ref"]  # 处置留痕引用
        assert len(judge["dispositions"]) == 1
        disposition = judge["dispositions"][0]
        assert disposition["by"] == "校准负责人"
        assert disposition["reason"] == "分布持续右移，人评锚点同步走低"
        assert disposition["action"] == "reanchor"
        assert disposition["conclusion"] == "confirmed_drift"

    def test_误报恢复后状态为_normal(self, drift_data_dir, drift_config, drift_sequence_writer):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="校准负责人",
            reason="样本骤降导致分布抖动",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert judge["status"]["status"] == "normal"  # 误报 → 恢复
        assert len(judge["dispositions"]) == 1
        assert judge["dispositions"][0]["action"] == "restore"


class TestC6场景3_无数据:
    def test_无检测记录标注无数据(
        self, drift_data_dir, drift_config, write_drift_snapshots, write_calibration_ledger
    ):
        write_drift_snapshots({"2026-W39": [0.5, 0.6, 0.7]}, agent_id=_AGENT)
        # 010 台账含版本记录（版本边界来源）；但无漂移检测记录 → "无数据"
        write_calibration_ledger(
            [{"evaluator_key": _KEY, "period": _PERIOD, "samples": 3, "kendall_tau": 0.5}],
            agent_id=_AGENT,
        )
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert judge["data_state"] == "no_data"
        assert "无数据" in judge["note"]
        assert judge["metrics"] == []
        assert judge["status"]["status"] == "normal"  # 不伪造状态
        assert judge["baseline"] is None


class TestC7双信号联动:
    def test_漂移_且_信度低于目标_强化告警(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_calibration_ledger
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD)
        _reliability(drift_data_dir, write_calibration_ledger, tau=0.3)
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert judge["signals"] == ["drift", "reliability_below_target"]
        alert = judge["alert"]
        assert alert["level"] == drift_config.double_signal.escalated_level  # 级别升级
        assert alert["double_signal"] is True
        assert "双信号" in alert["note"]
        assert report.alerts == (alert,)

    def test_仅漂移_常规告警(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_calibration_ledger
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD)
        _reliability(drift_data_dir, write_calibration_ledger, tau=0.9)  # 信度达标
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert judge["signals"] == ["drift"]
        assert judge["alert"]["level"] == drift_config.double_signal.base_level
        assert judge["alert"]["double_signal"] is False
        assert "双信号" not in judge["alert"]["note"]

    def test_仅信度下降_常规告警(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_calibration_ledger
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable", only=_PERIOD)
        _reliability(drift_data_dir, write_calibration_ledger, tau=0.3)
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        judge = _item(report)
        assert judge["signals"] == ["reliability_below_target"]
        assert judge["alert"]["level"] == drift_config.double_signal.base_level
        assert judge["alert"]["double_signal"] is False

    def test_双信号开关关闭时不升级(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_calibration_ledger
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD)
        _reliability(drift_data_dir, write_calibration_ledger, tau=0.3)
        disabled = replace(
            drift_config,
            double_signal=replace(drift_config.double_signal, enabled=False),
        )
        judge = _item(build_report(_PERIOD, disabled, drift_data_dir))
        assert judge["signals"] == ["drift", "reliability_below_target"]
        assert judge["alert"]["level"] == drift_config.double_signal.base_level  # 不升级
        assert judge["alert"]["double_signal"] is False

    def test_无信号时无告警(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_calibration_ledger
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable", only=_PERIOD)
        _reliability(drift_data_dir, write_calibration_ledger, tau=0.9)
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        assert _item(report)["alert"] is None
        assert report.alerts == ()


class TestC7场景3_ScoreConflict附注:
    def test_无持久化来源_字段为空并注明(self, drift_data_dir, drift_config):
        note = score_conflict_note(drift_data_dir)
        assert note["available"] is False
        assert note["source"] is None
        assert note["conflicts"] == []
        assert note["note"] == "无持久化来源（F6 未落盘命中分布）"
        assert note["participates_in_judgement"] is False  # 附注不参与阈值判定
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        assert report.residual_signals == {"score_conflict": note}

    def test_有持久化来源_附注如实引用(self, drift_data_dir, drift_config, tmp_path):
        root = tmp_path / SCORE_CONFLICT_SOURCE_RELPATH
        root.mkdir(parents=True)
        (root / "merged-pool-2026-W38.json").write_text(
            json.dumps(
                {
                    "generated_at": "2026-09-20T00:00:00+00:00",
                    "conflicts": [{"structure_key": '{"temperature": 0.5}', "scores": [0.6, 0.8]}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        note = score_conflict_note(drift_data_dir)
        assert note["available"] is True
        assert note["source"].endswith("merged-pool-2026-W38.json")
        assert note["conflicts"] == [
            {"structure_key": '{"temperature": 0.5}', "scores": [0.6, 0.8]}
        ]
        assert note["participates_in_judgement"] is False  # 附注不参与阈值判定
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        assert report.residual_signals["score_conflict"]["available"] is True

    def test_附注不参与阈值判定(
        self, drift_data_dir, drift_config, drift_sequence_writer, tmp_path
    ):
        """存在冲突附注 → 不产生告警、不改变判定与告警（避免与 judge 漂移混判）。"""
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable", only=_PERIOD)
        without = build_report(_PERIOD, drift_config, drift_data_dir)
        root = tmp_path / SCORE_CONFLICT_SOURCE_RELPATH
        root.mkdir(parents=True)
        (root / "merged-pool-2026-W39.json").write_text(
            json.dumps({"conflicts": [{"structure_key": "k", "scores": [0.1, 0.9]}]}),
            encoding="utf-8",
        )
        with_conflicts = build_report(_PERIOD, drift_config, drift_data_dir)
        assert [entry["verdict"] for entry in _item(with_conflicts)["metrics"]] == ["normal"]
        assert with_conflicts.alerts == without.alerts == ()
        assert _item(with_conflicts) == _item(without)  # items 逐字段一致（附注不改判定）


class TestC8只读与确定性:
    def test_报表只写_drift_reports(self, drift_data_dir, drift_config, drift_sequence_writer):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable")
        before = _fingerprint(drift_data_dir)
        build_report(_PERIOD, drift_config, drift_data_dir)
        after = _fingerprint(drift_data_dir)
        changed = {name for name, meta in after.items() if before.get(name) != meta}
        assert changed == {f"drift/reports/{_PERIOD}.json"}  # 010 产物与检测记录零写入

    def test_同输入两次报表逐字节一致(self, drift_data_dir, drift_config, drift_sequence_writer):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable")
        first = build_report(_PERIOD, drift_config, drift_data_dir, generated_at=_AT)
        path = drift_data_dir / "drift" / "reports" / f"{_PERIOD}.json"
        payload = path.read_bytes()
        second = build_report(_PERIOD, drift_config, drift_data_dir, generated_at=_AT)
        assert second.items == first.items
        assert path.read_bytes() == payload  # 逐字节一致（含告警与附注）
        assert record_path(drift_data_dir, _AGENT, _KEY, _PERIOD).is_file()  # 检测记录未被改写
