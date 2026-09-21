"""drift 领域模型单测（功能 012 / T1103，先于实现编写；data-model.md §领域模型）。

- frozen 六模型：DriftBaseline / DriftMetrics / DriftStatus / DriftDisposition /
  DriftReport / DeployEvidenceVerdict；
- 校验：占比与阈值域、状态机合法迁移（**系统只写 suspect**——终态构造需显式人工
  处置留痕引用）、detector_version 格式（`drift_detector@1.0.0+{12 位哈希}`）；
- 校验风格与 010 core/calibration/models.py 一致（ValidationError 复用 core/evaluators）。
"""

import pytest

from core.calibration.drift_models import (
    DeployEvidenceVerdict,
    DriftAction,
    DriftBaseline,
    DriftConclusion,
    DriftDisposition,
    DriftMetrics,
    DriftReport,
    DriftState,
    DriftStatus,
    DriftVerdict,
)
from core.evaluators.errors import ValidationError

_DETECTOR_VERSION = "drift_detector@1.0.0+0123456789ab"
_THRESHOLDS = {"psi": 0.2, "quantile": 0.1, "min_samples": 3, "window": 5}
_QUANTILES = {"p25": 0.4, "p50": 0.5, "p75": 0.6, "p90": 0.65}


def _baseline(**overrides):
    fields = {
        "evaluator_key": "judge.cinematic@1.0.0",
        "agent_id": "visual",
        "periods": ("2026-W34", "2026-W35"),
        "buckets": (0.5, 0.5),
        "quantiles": dict(_QUANTILES),
        "samples": 20,
        "detector_version": _DETECTOR_VERSION,
    }
    fields.update(overrides)
    return DriftBaseline(**fields)


def _metrics(**overrides):
    fields = {
        "evaluator_key": "judge.cinematic@1.0.0",
        "agent_id": "visual",
        "period": "2026-W39",
        "verdict": DriftVerdict.NORMAL,
        "samples": 12,
        "detector_version": _DETECTOR_VERSION,
        "psi": 0.01,
        "quantile_shifts": {"p25": 0.0, "p50": 0.01, "p75": 0.0, "p90": 0.0},
        "baseline_ref": "2026-W34..2026-W38",
        "thresholds": dict(_THRESHOLDS),
    }
    fields.update(overrides)
    return DriftMetrics(**fields)


def _disposition(**overrides):
    fields = {
        "evaluator_key": "judge.cinematic@1.0.0",
        "conclusion": DriftConclusion.FALSE_ALARM,
        "by": "calibrator",
        "at": "2026-09-21T10:00:00+00:00",
        "reason": "样本量骤降导致的分布抖动，非真实漂移",
        "action": DriftAction.RESTORE,
    }
    fields.update(overrides)
    return DriftDisposition(**fields)


def _report(**overrides):
    fields = {
        "period": "2026-W39",
        "detector_version": _DETECTOR_VERSION,
        "generated_at": "2026-09-21T10:00:00+00:00",
        "items": (
            {
                "agent_id": "visual",
                "evaluator_key": "judge.cinematic@1.0.0",
                "status": "normal",
            },
        ),
        "double_signal_rules": {"enabled": True, "reliability_target": 0.6},
    }
    fields.update(overrides)
    return DriftReport(**fields)


class TestDriftBaseline:
    def test_合法构造与周期区间(self):
        baseline = _baseline()
        assert baseline.period_range == "2026-W34..2026-W35"
        assert baseline.samples == 20

    def test_空窗口拒绝(self):
        with pytest.raises(ValidationError, match="periods"):
            _baseline(periods=())

    def test_周期元素非空字符串(self):
        with pytest.raises(ValidationError, match="periods"):
            _baseline(periods=("2026-W34", ""))

    def test_分桶占比域(self):
        with pytest.raises(ValidationError, match="buckets"):
            _baseline(buckets=())  # 空分布
        with pytest.raises(ValidationError, match="buckets"):
            _baseline(buckets=(1.2, -0.2))  # 越界
        with pytest.raises(ValidationError, match="buckets"):
            _baseline(buckets=(0.2, 0.3))  # 占比和必须为 1

    def test_样本量非负整数(self):
        for bad in (-1, 1.5, True):
            with pytest.raises(ValidationError, match="samples"):
                _baseline(samples=bad)

    def test_升版切分周期如实记录(self):
        assert _baseline().dropped_periods == ()
        baseline = _baseline(dropped_periods=("2026-W33",))
        assert baseline.dropped_periods == ("2026-W33",)
        with pytest.raises(ValidationError, match="dropped_periods"):
            _baseline(dropped_periods="2026-W33")  # 必须为元组
        with pytest.raises(ValidationError, match="dropped_periods"):
            _baseline(dropped_periods=("",))

    def test_分位数四键齐备且域内(self):
        with pytest.raises(ValidationError, match="quantiles"):
            _baseline(quantiles={"p25": 0.4})  # 缺键
        with pytest.raises(ValidationError, match="quantiles"):
            _baseline(quantiles={**_QUANTILES, "p90": 1.4})  # 越界
        with pytest.raises(ValidationError, match="quantiles"):
            _baseline(quantiles={**_QUANTILES, "p99": 0.9})  # 未登记分位点

    def test_必填非空与口径版本格式(self):
        for bad in ("", None, 1):
            with pytest.raises(ValidationError, match="evaluator_key"):
                _baseline(evaluator_key=bad)
        with pytest.raises(ValidationError, match="detector_version"):
            _baseline(detector_version="drift_detector@1.0.0")  # 缺哈希段


class TestDriftMetrics:
    def test_判定类必须携带指标与基线引用(self):
        with pytest.raises(ValidationError, match="psi"):
            _metrics(psi=None)  # 判定类不得无指标
        with pytest.raises(ValidationError, match="quantile_shifts"):
            _metrics(quantile_shifts={})
        with pytest.raises(ValidationError, match="baseline_ref"):
            _metrics(baseline_ref=None)

    def test_非判定类禁止伪造指标(self):
        for verdict in (DriftVerdict.INSUFFICIENT, DriftVerdict.NO_DATA):
            with pytest.raises(ValidationError, match="psi"):
                _metrics(verdict=verdict)  # 非判定类带 psi = 伪造结论

    def test_psi_域(self):
        for bad in (-0.1, "high", True):
            with pytest.raises(ValidationError, match="psi"):
                _metrics(psi=bad)

    def test_阈值域校验(self):
        with pytest.raises(ValidationError, match="thresholds"):
            _metrics(thresholds={"psi": 0.2})  # 缺键
        with pytest.raises(ValidationError, match="thresholds"):
            _metrics(thresholds={**_THRESHOLDS, "psi": 0.0})  # 阈值必须 > 0
        with pytest.raises(ValidationError, match="thresholds"):
            _metrics(thresholds={**_THRESHOLDS, "min_samples": 0})
        with pytest.raises(ValidationError, match="thresholds"):
            _metrics(thresholds={**_THRESHOLDS, "window": True})

    def test_detector_version_格式(self):
        with pytest.raises(ValidationError, match="detector_version"):
            _metrics(detector_version="psi@1.0.0+zzzz")

    def test_no_baseline_不携带基线引用(self):
        metrics = _metrics(
            verdict=DriftVerdict.NO_BASELINE,
            psi=None,
            quantile_shifts={},
            baseline_ref=None,
            note="首周期无基线",
        )
        assert metrics.verdict is DriftVerdict.NO_BASELINE
        with pytest.raises(ValidationError, match="baseline_ref"):
            _metrics(
                verdict=DriftVerdict.NO_BASELINE,
                psi=None,
                quantile_shifts={},
                baseline_ref="2026-W34..2026-W38",
            )

    def test_样本不足保留基线引用(self):
        metrics = _metrics(
            verdict=DriftVerdict.INSUFFICIENT,
            psi=None,
            quantile_shifts={},
            samples=2,
            note="样本不足",
        )
        assert metrics.baseline_ref == "2026-W34..2026-W38"

    def test_verdict_枚举与样本量(self):
        with pytest.raises(ValidationError, match="verdict"):
            _metrics(verdict="oops")
        with pytest.raises(ValidationError, match="samples"):
            _metrics(samples=-1)

    def test_枚举取值对齐_data_model(self):
        assert [v.value for v in DriftVerdict] == [
            "normal",
            "drift",
            "insufficient",
            "no_baseline",
            "no_data",
        ]


class TestDriftStatus:
    def test_状态枚举值(self):
        assert [s.value for s in DriftState] == [
            "normal",
            "suspect",
            "confirmed_drift",
            "false_alarm",
        ]

    def test_suspect_需触发指标引用(self):
        with pytest.raises(ValidationError, match="trigger_metrics"):
            DriftStatus(evaluator_key="judge.cinematic@1.0.0", status=DriftState.SUSPECT)

    def test_system_only_writes_suspect(self):
        """系统路径（无人工标记）只能写 suspect；终态构造必须带人工处置留痕引用。"""
        for state in (DriftState.CONFIRMED_DRIFT, DriftState.FALSE_ALARM):
            with pytest.raises(ValidationError, match="disposition_ref"):
                DriftStatus(evaluator_key="judge.cinematic@1.0.0", status=state, since="t1")

    def test_终态可构造_带人工处置引用(self):
        status = DriftStatus(
            evaluator_key="judge.cinematic@1.0.0",
            status=DriftState.CONFIRMED_DRIFT,
            since="2026-09-21T10:00:00+00:00",
            trigger_metrics="calibration/drift/metrics/visual/judge.cinematic/2026-W39.json",
            disposition_ref="calibration/drift/dispositions/judge.cinematic@1.0.0/1.json",
        )
        assert status.status is DriftState.CONFIRMED_DRIFT

    def test_normal_到_suspect_系统可写(self):
        status = DriftStatus(evaluator_key="judge.cinematic@1.0.0", since="2026-09-14T00:00:00Z")
        suspect = status.transition(
            DriftState.SUSPECT,
            since="2026-09-21T10:00:00+00:00",
            trigger_metrics="calibration/drift/metrics/visual/judge.cinematic/2026-W39.json",
        )
        assert suspect.status is DriftState.SUSPECT
        assert status.status is DriftState.NORMAL  # frozen：原对象不变

    def test_登记_suspect_必须携带触发指标(self):
        status = DriftStatus(evaluator_key="judge.cinematic@1.0.0", since="t0")
        with pytest.raises(ValidationError, match="trigger_metrics"):
            status.transition(DriftState.SUSPECT, since="t1")

    def test_suspect_到终态需人工处置标记(self):
        suspect = DriftStatus(
            evaluator_key="judge.cinematic@1.0.0",
            status=DriftState.SUSPECT,
            since="t1",
            trigger_metrics="calibration/drift/metrics/visual/judge.cinematic/2026-W39.json",
        )
        with pytest.raises(ValidationError, match="人工"):
            suspect.transition(DriftState.CONFIRMED_DRIFT, since="t2")  # 系统不得写终态
        confirmed = suspect.transition(
            DriftState.CONFIRMED_DRIFT,
            since="t2",
            by_human=True,
            disposition_ref="calibration/drift/dispositions/judge.cinematic@1.0.0/1.json",
        )
        assert confirmed.status is DriftState.CONFIRMED_DRIFT
        assert confirmed.disposition_ref.endswith("1.json")
        assert confirmed.trigger_metrics == suspect.trigger_metrics  # 触发指标沿袭

    def test_非法迁移拒绝(self):
        status = DriftStatus(evaluator_key="judge.cinematic@1.0.0", since="t0")
        with pytest.raises(ValidationError, match="不允许"):
            status.transition(
                DriftState.CONFIRMED_DRIFT,
                since="t1",
                by_human=True,
                disposition_ref="ref",
            )
        with pytest.raises(ValidationError, match="不允许"):
            status.transition(DriftState.NORMAL, since="t1")

    def test_终态无出边(self):
        confirmed = DriftStatus(
            evaluator_key="judge.cinematic@1.0.0",
            status=DriftState.CONFIRMED_DRIFT,
            since="t1",
            trigger_metrics="metrics-ref",
            disposition_ref="disposition-ref",
        )
        with pytest.raises(ValidationError, match="不允许"):
            confirmed.transition(
                DriftState.FALSE_ALARM, since="t2", by_human=True, disposition_ref="ref2"
            )

    def test_误报恢复_normal_需人工(self):
        false_alarm = DriftStatus(
            evaluator_key="judge.cinematic@1.0.0",
            status=DriftState.FALSE_ALARM,
            since="t1",
            trigger_metrics="metrics-ref",
            disposition_ref="disposition-ref",
        )
        with pytest.raises(ValidationError, match="人工"):
            false_alarm.transition(DriftState.NORMAL, since="t2")
        restored = false_alarm.transition(
            DriftState.NORMAL, since="t2", by_human=True, disposition_ref="restore-ref"
        )
        assert restored.status is DriftState.NORMAL
        assert restored.disposition_ref == "restore-ref"


class TestDriftDisposition:
    def test_合法留痕(self):
        disposition = DriftDisposition(
            evaluator_key="judge.cinematic@1.0.0",
            conclusion=DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            at="2026-09-21T10:00:00+00:00",
            reason="分布持续右移，人评锚点同步走低",
            action=DriftAction.REANCHOR,
        )
        assert disposition.conclusion is DriftConclusion.CONFIRMED_DRIFT
        assert disposition.action is DriftAction.REANCHOR

    def test_结论与动作枚举(self):
        assert [c.value for c in DriftConclusion] == ["confirmed_drift", "false_alarm"]
        assert [a.value for a in DriftAction] == ["deactivate", "reanchor", "restore"]
        with pytest.raises(ValidationError, match="conclusion"):
            _disposition(conclusion="oops")
        with pytest.raises(ValidationError, match="action"):
            _disposition(action="oops")

    def test_人时间理由缺一不可(self):
        for key in ("by", "at", "reason"):
            with pytest.raises(ValidationError, match=key):
                _disposition(**{key: ""})


class TestDriftReport:
    def test_合法构造(self):
        report = _report()
        assert report.period == "2026-W39"
        assert report.double_signal_rules["enabled"] is True

    def test_必填非空与版本格式(self):
        for key in ("period", "generated_at"):
            with pytest.raises(ValidationError, match=key):
                _report(**{key: ""})
        with pytest.raises(ValidationError, match="detector_version"):
            _report(detector_version="oops")

    def test_items_为非空映射元组(self):
        with pytest.raises(ValidationError, match="items"):
            _report(items=[{"agent_id": "visual"}])  # 必须为元组（frozen）
        with pytest.raises(ValidationError, match="items"):
            _report(items=({},))

    def test_alerts_字段齐全与类型(self):
        assert _report().alerts == ()  # 默认无告警
        alert = {
            "agent_id": "visual",
            "evaluator_key": "judge.cinematic@1.0.0",
            "level": "critical",
            "double_signal": True,
        }
        assert _report(alerts=(alert,)).alerts == (alert,)
        with pytest.raises(ValidationError, match="alerts"):
            _report(alerts=[alert])  # 必须为元组
        with pytest.raises(ValidationError, match="alerts"):
            _report(alerts=(alert, {}))  # 空映射
        for missing in ("agent_id", "evaluator_key", "level"):
            with pytest.raises(ValidationError, match=missing):
                _report(alerts=({**alert, missing: ""},))
        with pytest.raises(ValidationError, match="double_signal"):
            _report(alerts=({**alert, "double_signal": "yes"},))

    def test_残余信号附注字段(self):
        assert _report().residual_signals == {}
        report = _report(
            residual_signals={
                "score_conflict": {
                    "available": False,
                    "source": None,
                    "conflicts": [],
                    "note": "无持久化来源（F6 未落盘命中分布）",
                }
            }
        )
        assert report.residual_signals["score_conflict"]["available"] is False
        with pytest.raises(ValidationError, match="residual_signals"):
            _report(residual_signals=[])


class TestDeployEvidenceVerdict:
    def test_拒绝必带理由(self):
        verdict = DeployEvidenceVerdict(
            evaluator_key="judge.cinematic@1.0.0", allow=False, reason="状态 suspect（未处置）"
        )
        assert verdict.allow is False
        with pytest.raises(ValidationError, match="reason"):
            DeployEvidenceVerdict(evaluator_key="judge.cinematic@1.0.0", allow=False, reason="")

    def test_允许(self):
        verdict = DeployEvidenceVerdict(
            evaluator_key="judge.cinematic@1.0.0", allow=True, reason="状态 normal，无漂移"
        )
        assert verdict.allow is True

    def test_allow_必须为布尔(self):
        with pytest.raises(ValidationError, match="allow"):
            DeployEvidenceVerdict(evaluator_key="judge.cinematic@1.0.0", allow="yes", reason="理由")

    def test_评估器键非空(self):
        with pytest.raises(ValidationError, match="evaluator_key"):
            DeployEvidenceVerdict(evaluator_key="", allow=True, reason="理由")
