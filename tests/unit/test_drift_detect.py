"""漂移检测器单测（功能 012 US1 / T1109，先于实现编写；契约 C1 场景 1~6）。

- 数据源 = 010 快照序列（conftest 的 write_drift_snapshots 用 010 同源写入器落盘）；
- 基线 = 滑动窗口（最近 window 周期、不含当前周期）；
- 判定 = PSI > psi_threshold 或 max|分位数位移| > quantile_threshold → drift；
  首周期 → no_baseline（记基线不告警）；样本不足 → insufficient（不硬判）；无快照 → no_data；
- 范围：默认仅 judge 类（proxy/rule 显式拒绝，非静默跳过）；
  升版：新版本独立基线（旧版本数据不混入）；
- 记录落盘 calibration/drift/metrics/{agent}/{evaluator_id}/{period}.json。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.calibration.drift_metrics import (
    build_baseline,
    detect_drift,
    in_scope,
    kind_of,
    read_snapshot,
    record_path,
)
from core.calibration.drift_models import DriftVerdict
from core.calibration.errors import DriftOutOfScopeError
from core.evaluators.errors import ValidationError

_KEY = "judge.cinematic@1.0.0"
_AGENT = "visual"
_CURRENT = "2026-W39"


def _record(data_dir, period=_CURRENT, key=_KEY, agent=_AGENT):
    """读回检测记录（JSON）。"""
    path = record_path(data_dir, agent, key, period)
    return json.loads(path.read_text(encoding="utf-8"))


class Test检测范围:
    def test_类别取自_evaluator_id_前缀(self):
        assert kind_of("judge.cinematic@1.0.0") == "judge"
        assert kind_of("proxy.aesthetic@2.0.0") == "proxy"
        assert kind_of("rule.av_sync@1.0.0") == "rule"

    def test_默认仅_judge_类(self, drift_config):
        assert in_scope("judge.cinematic@1.0.0", drift_config) is True
        assert in_scope("proxy.aesthetic@1.0.0", drift_config) is False
        assert in_scope("rule.av_sync@1.0.0", drift_config) is False
        assert in_scope("human.platform_metrics@1.0.0", drift_config) is False

    def test_非_judge_类显式拒绝(self, drift_data_dir, drift_config, write_drift_snapshots):
        """C1 场景 6：proxy/rule 默认关闭 → 不判定（显式拒绝 + 注明，不静默跳过）。"""
        write_drift_snapshots(
            {"2026-W38": [0.5] * 5, _CURRENT: [0.5] * 5},
            evaluator_key="proxy.aesthetic@1.0.0",
        )
        with pytest.raises(DriftOutOfScopeError, match="未纳入"):
            detect_drift(_AGENT, "proxy.aesthetic@1.0.0", _CURRENT, drift_config, drift_data_dir)
        assert not record_path(
            drift_data_dir, _AGENT, "proxy.aesthetic@1.0.0", _CURRENT
        ).exists()  # 不产生记录

    def test_配置纳入后可判定(self, drift_data_dir, drift_config, write_drift_snapshots):
        write_drift_snapshots(
            {"2026-W38": [0.5] * 5, _CURRENT: [0.9] * 5},
            evaluator_key="proxy.aesthetic@1.0.0",
        )
        config = replace(drift_config, scope_kinds=("judge", "proxy"))
        metrics = detect_drift(_AGENT, "proxy.aesthetic@1.0.0", _CURRENT, config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.DRIFT  # 显式纳入后照常判定


class Test场景1_稳定不误报:
    def test_稳定序列判_normal(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.NORMAL
        assert metrics.psi is not None and metrics.psi <= drift_config.psi_threshold
        assert max(abs(v) for v in metrics.quantile_shifts.values()) <= (
            drift_config.quantile_threshold
        )
        assert metrics.note == ""  # 无误报、无触发说明
        assert _record(drift_data_dir)["verdict"] == "normal"


class Test场景2_三形态漂移检出:
    @pytest.mark.parametrize("variant", ["mean_shift", "variance_widen", "bimodal"])
    def test_注入漂移超阈检出(self, drift_data_dir, drift_config, drift_sequence_writer, variant):
        drift_sequence_writer(variant, agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.DRIFT
        assert metrics.psi > drift_config.psi_threshold  # 主指标超阈
        assert "PSI" in metrics.note
        assert "不判原因" in metrics.note  # 只判分布变化，不判原因（诚实边界）

    def test_位移辅指标同样超阈可判(self, drift_data_dir, drift_config, drift_sequence_writer):
        """双维口径：任一瓶颈超阈即判漂移（此处均值平移两维都超阈）。"""
        drift_sequence_writer("mean_shift", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        shift = max(abs(v) for v in metrics.quantile_shifts.values())
        assert shift > drift_config.quantile_threshold
        assert "分位数最大位移" in metrics.note


class Test场景3_首周期记基线:
    def test_首周期_no_baseline_不告警(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("first_period", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.NO_BASELINE
        assert metrics.psi is None and metrics.quantile_shifts == {}
        assert metrics.baseline_ref is None
        assert "首周期无基线" in metrics.note
        assert _record(drift_data_dir)["baseline_ref"] is None  # 记基线不告警

    def test_历史周期构成基线(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("first_period", agent_id=_AGENT, evaluator_key=_KEY)
        detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY, periods=("2026-W38",))
        baseline = build_baseline(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert baseline is not None
        assert baseline.periods == ("2026-W38",)  # 窗口 = 当前周期之前
        assert baseline.period_range == "2026-W38..2026-W38"

    def test_滑动窗口取最近_window_周期(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        baseline = build_baseline(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert baseline.periods == (
            "2026-W34",
            "2026-W35",
            "2026-W36",
            "2026-W37",
            "2026-W38",
        )  # 最近 window=5 个周期，不含当前
        assert baseline.samples == 25 * 5  # 窗口内样本合并
        assert sum(baseline.buckets) == pytest.approx(1.0)  # 合并分布为占比
        assert set(baseline.quantiles) == {"p25", "p50", "p75", "p90"}


class Test场景4_样本不足:
    def test_当前周期样本不足(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("insufficient_current", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.INSUFFICIENT
        assert metrics.samples == 2
        assert "当前周期样本不足" in metrics.note
        assert metrics.psi is None  # 不硬判、不伪造指标
        assert metrics.baseline_ref == "2026-W34..2026-W38"  # 基线引用仍如实记录

    def test_基线窗口样本不足(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("insufficient_baseline", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.INSUFFICIENT
        assert "基线窗口样本不足" in metrics.note
        assert metrics.psi is None


class Test场景5_评估器升版独立基线:
    """版本边界取自 010 台账（快照路径不含版本）；升版点即基线切分点（版本冻结）。"""

    _BASE_WINDOW = ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38")

    def _ledger_versions(self, write_calibration_ledger, versions: dict):
        write_calibration_ledger(
            [
                {
                    "evaluator_key": key,
                    "period": period,
                    "samples": 25,
                    "kendall_tau": 0.5,
                }
                for period, key in versions.items()
            ],
            agent_id=_AGENT,
        )

    def test_新版本无历史也独立记基线(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        write_drift_snapshots,
        write_calibration_ledger,
    ):
        # 旧版本 1.0.0 有 5 周期历史（台账记录版本边界）；新版本 2.0.0 只有当前周期
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        self._ledger_versions(write_calibration_ledger, dict.fromkeys(self._BASE_WINDOW, _KEY))
        write_drift_snapshots(
            {"2026-W39": [0.5, 0.6, 0.7]},
            agent_id=_AGENT,
            evaluator_key="judge.cinematic@2.0.0",
        )
        metrics = detect_drift(
            _AGENT, "judge.cinematic@2.0.0", _CURRENT, drift_config, drift_data_dir
        )
        assert metrics.verdict is DriftVerdict.NO_BASELINE  # 旧版本历史不混入新版本
        assert "升版切分基线" in metrics.note  # 切分点如实标注
        assert (
            build_baseline(_AGENT, "judge.cinematic@2.0.0", _CURRENT, drift_config, drift_data_dir)
            is None
        )

    def test_升版后仅用自有历史(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        write_drift_snapshots,
        write_calibration_ledger,
    ):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        self._ledger_versions(
            write_calibration_ledger,
            {**dict.fromkeys(self._BASE_WINDOW[:4], _KEY), "2026-W38": "judge.cinematic@2.0.0"},
        )
        write_drift_snapshots(
            {"2026-W38": [0.5, 0.6, 0.7], "2026-W39": [0.9, 0.95, 0.98]},
            agent_id=_AGENT,
            evaluator_key="judge.cinematic@2.0.0",
        )
        baseline = build_baseline(
            _AGENT, "judge.cinematic@2.0.0", _CURRENT, drift_config, drift_data_dir
        )
        assert baseline.periods == ("2026-W38",)  # 仅新版本自有周期
        assert baseline.dropped_periods == self._BASE_WINDOW[:4]  # 旧版本周期被切分
        metrics = detect_drift(
            _AGENT, "judge.cinematic@2.0.0", _CURRENT, drift_config, drift_data_dir
        )
        assert metrics.verdict is DriftVerdict.DRIFT


class Test场景6_无数据与缺口:
    def test_无快照_no_data(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, "2026-W40", drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.NO_DATA
        assert "无快照" in metrics.note
        assert metrics.psi is None

    def test_缺口周期如实标注(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("gap", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.NORMAL  # 缺口不改变判定口径
        assert "2026-W36" in metrics.note  # 缺口如实标注（不插值、不编造）
        assert metrics.baseline_ref == "2026-W34..2026-W38"


class Test快照读取与口径:
    def test_读回_010_快照_schema(self, drift_data_dir, drift_config, write_drift_snapshots):
        write_drift_snapshots({"2026-W38": [0.2, 0.4, 0.6, 0.8]}, agent_id=_AGENT)
        snapshot = read_snapshot(drift_data_dir, _AGENT, "judge.cinematic", "2026-W38")
        assert snapshot["agent_id"] == _AGENT
        assert snapshot["evaluator_id"] == "judge.cinematic"
        assert snapshot["period"] == "2026-W38"
        assert snapshot["samples"] == 4
        assert len(snapshot["buckets"]) == 10
        assert set(snapshot["quantiles"]) == {"p25", "p50", "p75", "p90"}
        assert read_snapshot(drift_data_dir, _AGENT, "judge.cinematic", "2026-W99") is None

    def test_记录落盘路径与字段(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("mean_shift", agent_id=_AGENT, evaluator_key=_KEY)
        detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        path = record_path(drift_data_dir, _AGENT, _KEY, _CURRENT)
        assert path == drift_data_dir / "drift" / "metrics" / _AGENT / "judge.cinematic" / (
            f"{_CURRENT}.json"
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        assert set(record) == {
            "agent_id",
            "baseline_ref",
            "detector_version",
            "evaluator_key",
            "note",
            "period",
            "psi",
            "quantile_shifts",
            "samples",
            "thresholds",
            "verdict",
        }
        assert record["evaluator_key"] == _KEY
        assert record["detector_version"].startswith("drift_detector@1.0.0+")
        assert record["thresholds"] == {
            "psi": 0.2,
            "quantile": 0.1,
            "min_samples": 3,
            "window": 5,
        }
        assert set(record["quantile_shifts"]) == {"p25", "p50", "p75", "p90"}

    def test_分桶数与配置不一致报错(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        with pytest.raises(ValidationError, match="buckets"):
            detect_drift(_AGENT, _KEY, _CURRENT, replace(drift_config, buckets=5), drift_data_dir)

    def test_周期标签非法报错(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        with pytest.raises(ValidationError, match="周期"):
            detect_drift(_AGENT, _KEY, "2026-W99", drift_config, drift_data_dir)


def _raw_snapshot(data_dir, period, **overrides):
    """直接落盘一份快照 JSON（健壮性测试用：绕过 010 写入器构造脏数据）。"""
    payload = {
        "agent_id": _AGENT,
        "evaluator_id": "judge.cinematic",
        "period": period,
        "samples": 5,
        "bucket_width": 0.1,
        "buckets": [0, 0, 0, 0, 0, 5, 0, 0, 0, 0],
        "quantiles": {"p25": 0.45, "p50": 0.5, "p75": 0.55, "p90": 0.58},
    }
    payload.update(overrides)
    path = Path(data_dir) / "snapshots" / _AGENT / "judge.cinematic" / f"{period}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class Test健壮性与诚实报错:
    """边界情况：如实报错/如实标注，绝不静默用脏数据或伪造结论。"""

    def test_从未采集的评估器_无数据(self, drift_data_dir, drift_config):
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.NO_DATA
        assert metrics.samples == 0
        assert build_baseline(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir) is None

    def test_周期标签形态非法报错(self, drift_data_dir, drift_config):
        for bad in ("2026-39", "bad", 39, ""):
            with pytest.raises(ValidationError, match="周期"):
                detect_drift(_AGENT, _KEY, bad, drift_config, drift_data_dir)

    def test_evaluator_key_形态非法报错(self, drift_data_dir, drift_config):
        for bad in ("judge.cinematic", "@1.0.0", ""):
            with pytest.raises(ValidationError, match="evaluator_key"):
                detect_drift(_AGENT, bad, _CURRENT, drift_config, drift_data_dir)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"buckets": [1, -1] + [0] * 8},  # 负计数
            {"buckets": "oops"},  # 非列表
            {"quantiles": {"p25": 0.4}},  # 缺分位点
            {"quantiles": {"p25": 0.4, "p50": 0.5, "p75": 0.6, "p90": "high"}},  # 非数值
            {"samples": -1},  # 非法样本量
        ],
    )
    def test_脏快照显式报错(self, drift_data_dir, drift_config, overrides):
        _raw_snapshot(drift_data_dir, _CURRENT, **overrides)
        with pytest.raises(ValidationError):
            detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)

    def test_零样本快照_判样本不足不炸(self, drift_data_dir, drift_config):
        _raw_snapshot(drift_data_dir, "2026-W38", samples=0, buckets=[0] * 10)
        _raw_snapshot(drift_data_dir, _CURRENT)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.verdict is DriftVerdict.INSUFFICIENT
        assert "基线窗口样本不足" in metrics.note
        assert metrics.psi is None
