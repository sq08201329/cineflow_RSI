"""漂移状态机与人工处置留痕单测（功能 012 US2 / T1113，先于实现编写；契约 C3）。

- register_suspect：超阈判定（verdict=drift）→ suspect 登记（trigger_metrics 指向检测记录）；
  非判定类（insufficient/no_baseline/no_data）拒绝登记（不伪造状态）；
- dispose：人工处置（by/at/reason 必填）——确认漂移 → confirmed_drift（停用/换锚点升版）；
  误报 → false_alarm 留痕 → 恢复 normal（action=restore）；
- registry 文件化 `drift/status/{evaluator_key_sanitized}.json`（当前态 + 历史，只增不改）；
  处置留痕 `drift/dispositions/{evaluator_key_sanitized}/{timestamp}.json`（只增不改）；
- **SC-002 机检**：历史中一切非 suspect 状态必须携带人工处置留痕引用
  （系统自动写入仅限 suspect；终态由人工 dispose 写入）。
"""

import json
from datetime import UTC, datetime

import pytest

from core.calibration.drift_metrics import record_path
from core.calibration.drift_models import (
    DriftAction,
    DriftConclusion,
    DriftMetrics,
    DriftState,
    DriftVerdict,
)
from core.calibration.drift_status import (
    current_status,
    dispose,
    dispositions,
    history,
    register_suspect,
    registry_path,
    status_dir,
)
from core.calibration.errors import DriftRecordConflictError
from core.evaluators.errors import ValidationError

_KEY = "judge.cinematic@1.0.0"
_AGENT = "visual"
_PERIOD = "2026-W39"
_AT = "2026-09-21T10:00:00+00:00"
_THRESHOLDS = {"psi": 0.2, "quantile": 0.1, "min_samples": 3, "window": 5}


def _metrics(verdict=DriftVerdict.DRIFT, *, period=_PERIOD, key=_KEY, note="PSI 0.3100 > 0.2"):
    judged = verdict in (DriftVerdict.NORMAL, DriftVerdict.DRIFT)
    baseline_ref = None if verdict is DriftVerdict.NO_BASELINE else "2026-W34..2026-W38"
    return DriftMetrics(
        evaluator_key=key,
        agent_id=_AGENT,
        period=period,
        verdict=verdict,
        samples=12,
        detector_version="drift_detector@1.0.0+0123456789ab",
        psi=0.31 if judged else None,
        quantile_shifts={"p25": 0.2, "p50": 0.2, "p75": 0.2, "p90": 0.2} if judged else {},
        baseline_ref=baseline_ref,
        thresholds=dict(_THRESHOLDS),
        note=note,
    )


def _trigger_ref(data_dir, period=_PERIOD):
    """触发指标引用口径：检测记录相对 data_dir 的路径。"""
    return str(record_path(data_dir, _AGENT, _KEY, period).relative_to(data_dir))


def _registry_payload(data_dir, key=_KEY):
    return json.loads(registry_path(data_dir, key).read_text(encoding="utf-8"))


class Test登记suspect:
    def test_超阈判定登记_suspect(self, drift_data_dir):
        status = register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        assert status.status is DriftState.SUSPECT
        assert status.since == _AT
        assert status.trigger_metrics == _trigger_ref(drift_data_dir)  # 指向检测记录
        assert registry_path(drift_data_dir, _KEY) == (
            status_dir(drift_data_dir) / "judge.cinematic@1.0.0.json"
        )
        payload = _registry_payload(drift_data_dir)
        assert payload["status"] == "suspect"
        assert payload["trigger_metrics"] == _trigger_ref(drift_data_dir)
        assert [entry["status"] for entry in payload["history"]] == ["suspect"]

    def test_非判定类拒绝登记(self, drift_data_dir):
        for verdict in (
            DriftVerdict.NORMAL,
            DriftVerdict.INSUFFICIENT,
            DriftVerdict.NO_BASELINE,
            DriftVerdict.NO_DATA,
        ):
            with pytest.raises(ValidationError, match="drift"):
                register_suspect(drift_data_dir, _KEY, _metrics(verdict), at=_AT)
        assert not registry_path(drift_data_dir, _KEY).exists()  # 不产生状态文件

    def test_重复登记幂等(self, drift_data_dir):
        first = register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        second = register_suspect(drift_data_dir, _KEY, _metrics(), at="2026-09-22T10:00:00+00:00")
        assert second == first  # 已 suspect：不重复登记、不新增历史
        assert len(_registry_payload(drift_data_dir)["history"]) == 1

    def test_恢复后再次超阈追加历史(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="calibrator",
            reason="样本量骤降导致抖动",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        status = register_suspect(
            drift_data_dir, _KEY, _metrics(period="2026-W40"), at="2026-09-28T10:00:00+00:00"
        )
        assert status.status is DriftState.SUSPECT
        assert [entry["status"] for entry in _registry_payload(drift_data_dir)["history"]] == [
            "suspect",
            "false_alarm",
            "normal",
            "suspect",
        ]

    def test_未登记时当前态为_normal(self, drift_data_dir):
        assert current_status(drift_data_dir, _KEY) is None  # 无记录 = 默认 normal


class Test人工处置:
    def test_确认漂移_停用(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        disposition = dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="分布持续右移且人评锚点同步走低",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        assert disposition.conclusion is DriftConclusion.CONFIRMED_DRIFT
        assert disposition.action is DriftAction.DEACTIVATE
        status = current_status(drift_data_dir, _KEY)
        assert status.status is DriftState.CONFIRMED_DRIFT
        assert status.disposition_ref is not None
        assert status.trigger_metrics == _trigger_ref(drift_data_dir)  # 触发引用沿袭

    def test_确认漂移_换锚点升版(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="judge 委员会提示词被上游改写，分布口径已变",
            action=DriftAction.REANCHOR,
            at="2026-09-21T11:00:00+00:00",
        )
        assert current_status(drift_data_dir, _KEY).status is DriftState.CONFIRMED_DRIFT

    def test_误报恢复_normal(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        disposition = dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="calibrator",
            reason="本期锚点分布受样本骤降影响，非真实漂移",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        assert disposition.conclusion is DriftConclusion.FALSE_ALARM
        status = current_status(drift_data_dir, _KEY)
        assert status.status is DriftState.NORMAL  # 误报 → 恢复
        assert status.disposition_ref is not None  # 恢复同样留痕
        assert [entry["status"] for entry in _registry_payload(drift_data_dir)["history"]] == [
            "suspect",
            "false_alarm",
            "normal",
        ]

    def test_误报必须恢复动作(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        with pytest.raises(ValidationError, match="restore"):
            dispose(
                drift_data_dir,
                _KEY,
                DriftConclusion.FALSE_ALARM,
                by="calibrator",
                reason="误报",
                action=DriftAction.DEACTIVATE,
                at="2026-09-21T11:00:00+00:00",
            )

    def test_未登记处置拒绝(self, drift_data_dir):
        with pytest.raises(ValidationError, match="suspect"):
            dispose(
                drift_data_dir,
                _KEY,
                DriftConclusion.CONFIRMED_DRIFT,
                by="calibrator",
                reason="无登记的漂移",
                action=DriftAction.DEACTIVATE,
                at=_AT,
            )

    def test_终态不可再处置(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="确认漂移",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        with pytest.raises(ValidationError, match="不允许"):
            dispose(
                drift_data_dir,
                _KEY,
                DriftConclusion.FALSE_ALARM,
                by="calibrator",
                reason="反悔",
                action=DriftAction.RESTORE,
                at="2026-09-21T12:00:00+00:00",
            )

    def test_人工留痕字段必填(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        for missing in ("by", "reason", "conclusion", "action"):
            fields = {
                "conclusion": DriftConclusion.CONFIRMED_DRIFT,
                "by": "calibrator",
                "reason": "确认漂移",
                "action": DriftAction.DEACTIVATE,
                "at": "2026-09-21T11:00:00+00:00",
            }
            fields[missing] = "" if missing in ("by", "reason") else None
            with pytest.raises((ValidationError, TypeError), match=missing):
                dispose(drift_data_dir, _KEY, **fields)


class Test留痕只增不改:
    def test_留痕落盘与读回(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        disposition = dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="确认漂移",
            action=DriftAction.REANCHOR,
            at="2026-09-21T11:00:00+00:00",
        )
        stored = dispositions(drift_data_dir, _KEY)
        assert len(stored) == 1
        assert stored[0] == disposition  # 读回逐字段一致（不可改写）
        ref = current_status(drift_data_dir, _KEY).disposition_ref
        assert ref is not None and ref.endswith(".json")
        assert (_path := drift_data_dir / ref).is_file()

    def test_同刻不同评估器留痕互不冲突(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        kwargs = {
            "by": "calibrator",
            "reason": "确认漂移",
            "action": DriftAction.DEACTIVATE,
            "at": "2026-09-21T11:00:00+00:00",
        }
        dispose(drift_data_dir, _KEY, DriftConclusion.CONFIRMED_DRIFT, **kwargs)
        other = "judge.script_fit@1.0.0"
        register_suspect(drift_data_dir, other, _metrics(key=other), at=_AT)
        # 同人同时刻同动作的第二条留痕（不同评估器）不冲突
        dispose(drift_data_dir, other, DriftConclusion.CONFIRMED_DRIFT, **kwargs)
        assert len(dispositions(drift_data_dir, _KEY)) == 1
        assert len(dispositions(drift_data_dir, other)) == 1

    def test_同评估器同刻重复留痕拒绝(self, drift_data_dir):
        """留痕只增不改：同评估器同一时刻的留痕文件已存在 → 拒绝改写。"""
        dispose_at = "2026-09-21T11:00:00+00:00"
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="calibrator",
            reason="误报",
            action=DriftAction.RESTORE,
            at=dispose_at,
        )
        register_suspect(
            drift_data_dir, _KEY, _metrics(period="2026-W40"), at="2026-09-28T10:00:00+00:00"
        )
        with pytest.raises(DriftRecordConflictError, match="不可改写"):
            dispose(
                drift_data_dir,
                _KEY,
                DriftConclusion.FALSE_ALARM,
                by="calibrator",
                reason="误报（重放）",
                action=DriftAction.RESTORE,
                at=dispose_at,
            )

    def test_终态不可逆(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="确认漂移",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        before = _registry_payload(drift_data_dir)
        status = register_suspect(
            drift_data_dir, _KEY, _metrics(period="2026-W40"), at="2026-09-28T10:00:00+00:00"
        )
        assert status.status is DriftState.CONFIRMED_DRIFT  # 终态不可逆（新版本另起 key）
        assert _registry_payload(drift_data_dir) == before  # 历史零变更

    def test_历史只增不改(self, drift_data_dir):
        register_suspect(drift_data_dir, _KEY, _metrics(), at=_AT)
        before = _registry_payload(drift_data_dir)["history"]
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="calibrator",
            reason="误报",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        after = _registry_payload(drift_data_dir)["history"]
        assert after[: len(before)] == before  # 既有历史逐字段不变（只追加）
        assert len(after) == len(before) + 2  # false_alarm + normal（恢复）
        assert history(drift_data_dir, _KEY) == tuple(after)


class Test系统只写suspect机检:
    """SC-002：系统自动写入的状态仅 suspect；其余状态必须来自人工处置留痕。"""

    def test_历史中非_suspect_状态必带人工留痕(self, drift_data_dir):
        keys = ["judge.cinematic@1.0.0", "judge.script_fit@1.0.0"]
        for key in keys:
            register_suspect(drift_data_dir, key, _metrics(key=key), at=_AT)
        dispose(
            drift_data_dir,
            keys[0],
            DriftConclusion.CONFIRMED_DRIFT,
            by="calibrator",
            reason="确认漂移",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        dispose(
            drift_data_dir,
            keys[1],
            DriftConclusion.FALSE_ALARM,
            by="calibrator",
            reason="误报",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        for key in keys:
            for entry in _registry_payload(drift_data_dir, key)["history"]:
                if entry["status"] == "suspect":
                    assert entry["trigger_metrics"]  # 超阈判定必有触发引用
                    assert not entry.get("disposition_ref")  # 系统写入不带人工留痕
                else:
                    assert entry["disposition_ref"], entry  # 非 suspect 必为人工写入

    def test_系统路径无终态写法(self):
        """机检：模块仅暴露 register_suspect（写 suspect）与 dispose（人工，需留痕）。"""
        import inspect

        from core.calibration import drift_status

        public = {
            name
            for name in dir(drift_status)
            if not name.startswith("_") and inspect.isfunction(getattr(drift_status, name))
        }
        assert {"register_suspect", "dispose"} <= public
        # 无"直接写终态"的系统入口（终态只能经 dispose → 需 by/reason/action）
        signature = inspect.signature(drift_status.dispose)
        for required in ("by", "reason", "action", "conclusion"):
            assert required in signature.parameters, required


class Test快照时间口径:
    def test_缺省时间取当前_UTC(self, drift_data_dir):
        status = register_suspect(drift_data_dir, _KEY, _metrics())
        assert datetime.fromisoformat(status.since).tzinfo is not None
        assert (
            abs(datetime.fromisoformat(status.since).timestamp() - datetime.now(UTC).timestamp())
            < 60
        )
