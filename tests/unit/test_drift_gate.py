"""合成门禁与部署证据接口单测（功能 012 US2 / T1114，先于实现编写；契约 C4/C5）。

- gate_weights：分级处置——`suspect` 降权 ×`suspect_weight`（默认 0.5）、
  `confirmed_drift` 排除（权重归零；`confirmed_exclude=false` 时降权）；
  降权后按"可调分量"重新归一（Σ 保持，改变的是**影响力份额**而非总尺度）；
  **权重键无版本**（`judge.cinematic`），状态键带版本（`judge.cinematic@1.0.0`）→
  按 evaluator_id（@ 前缀）匹配；多版本取最严重状态；
- 三态合成结果差异（SC-004）：normal / suspect / confirmed_drift 三态得分互不相同；
- deploy_evidence_verdict：suspect/confirmed_drift 拒绝 + 理由（含状态与处置引用）；
  normal/false_alarm 允许（F9 前置接口，SC-003）。
"""

import pytest

from core.calibration.drift_gate import (
    DriftGate,
    apply_gate,
    deploy_evidence_verdict,
    gate_weights,
)
from core.calibration.drift_models import (
    DriftAction,
    DriftConclusion,
    DriftState,
    DriftStatus,
)
from core.calibration.drift_status import DriftRegistry, dispose, register_suspect
from core.evaluators.base import EvalResult
from core.evaluators.composite import composite_score_versioned

_KEY = "judge.cinematic@1.0.0"
_AT = "2026-09-21T10:00:00+00:00"
_ADJUSTABLE = (
    "proxy.aesthetic",
    "proxy.identity_consistency",
    "proxy.flicker",
    "judge.cinematic",
)
_WEIGHTS = {
    "rule.format_compliance": "gate",
    "proxy.aesthetic": 0.25,
    "proxy.identity_consistency": 0.35,
    "proxy.flicker": 0.15,
    "judge.cinematic": 0.25,
}


def _metrics(period="2026-W39", key=_KEY):
    from core.calibration.drift_models import DriftMetrics, DriftVerdict

    return DriftMetrics(
        evaluator_key=key,
        agent_id="visual",
        period=period,
        verdict=DriftVerdict.DRIFT,
        samples=12,
        detector_version="drift_detector@1.0.0+0123456789ab",
        psi=0.31,
        quantile_shifts={"p25": 0.2, "p50": 0.2, "p75": 0.2, "p90": 0.2},
        baseline_ref="2026-W34..2026-W38",
        thresholds={"psi": 0.2, "quantile": 0.1, "min_samples": 3, "window": 5},
        note="PSI 0.3100 > 0.2",
    )


def _suspect(drift_data_dir, key=_KEY):
    register_suspect(drift_data_dir, key, _metrics(key=key), at=_AT)
    return DriftRegistry.load(drift_data_dir)


def _confirmed(drift_data_dir, key=_KEY):
    _suspect(drift_data_dir, key)
    dispose(
        drift_data_dir,
        key,
        DriftConclusion.CONFIRMED_DRIFT,
        by="calibrator",
        reason="分布持续右移",
        action=DriftAction.DEACTIVATE,
        at="2026-09-21T11:00:00+00:00",
    )
    return DriftRegistry.load(drift_data_dir)


def _normal_scores(weights, judge_score=0.2, proxy_score=0.8):
    """视觉口径合成（与 loop 的 `_weights(config)` 同口径：gate 字面量先归一为 0.0）。"""
    numeric = {
        key: (0.0 if isinstance(value, str) else float(value)) for key, value in weights.items()
    }
    breakdown = {
        "rule.format_compliance@1.0.0": EvalResult(score=1.0),
        "proxy.aesthetic@1.0.0": EvalResult(score=proxy_score),
        "proxy.identity_consistency@1.0.0": EvalResult(score=proxy_score),
        "proxy.flicker@1.0.0": EvalResult(score=proxy_score),
        "judge.cinematic@1.0.0": EvalResult(score=judge_score),
    }
    return composite_score_versioned(breakdown, numeric)


class Test分级处置权重:
    def test_normal_权重不变(self, drift_data_dir, drift_config):
        gated = gate_weights(_WEIGHTS, DriftRegistry.load(drift_data_dir), drift_config)
        assert gated == _WEIGHTS  # 逐字段一致（含 gate 字面量）
        assert gated is not _WEIGHTS  # 返回新映射（不就地改写入参）

    def test_无状态登记_权重不变(self, drift_config):
        assert gate_weights(_WEIGHTS, None, drift_config) == _WEIGHTS

    def test_suspect_降权与归一(self, drift_data_dir, drift_config):
        gated = gate_weights(_WEIGHTS, _suspect(drift_data_dir), drift_config)
        assert gated["rule.format_compliance"] == "gate"  # 硬规则门禁不动
        assert gated["judge.cinematic"] == pytest.approx(0.25 * 0.5 / 0.875)
        # 其余分量按比例上升（影响力份额重分配），Σ 保持 1.0
        assert gated["proxy.aesthetic"] == pytest.approx(0.25 / 0.875)
        assert sum(gated[key] for key in _ADJUSTABLE) == pytest.approx(1.0)
        assert gated["judge.cinematic"] < _WEIGHTS["judge.cinematic"]

    def test_confirmed_drift_排除(self, drift_data_dir, drift_config):
        gated = gate_weights(_WEIGHTS, _confirmed(drift_data_dir), drift_config)
        assert gated["judge.cinematic"] == 0.0  # 不参与合成
        assert sum(gated[key] for key in _ADJUSTABLE) == pytest.approx(1.0)
        assert gated["proxy.aesthetic"] == pytest.approx(0.25 / 0.75)

    def test_按_evaluator_id_匹配无版本权重键(self, drift_data_dir, drift_config):
        """状态键带版本、权重键无版本 → 按 @ 前缀匹配（本特性已交付的注意点）。"""
        gated = gate_weights(_WEIGHTS, _suspect(drift_data_dir), drift_config)
        assert gated["judge.cinematic"] != _WEIGHTS["judge.cinematic"]
        # 其他分量键（无对应状态）不受直接处置，仅参与归一
        assert gated["proxy.flicker"] == pytest.approx(0.15 / 0.875)

    def test_多版本取最严重状态(self, drift_data_dir, drift_config):
        registry = _suspect(drift_data_dir, "judge.cinematic@1.0.0")
        _confirmed(drift_data_dir, "judge.cinematic@2.0.0")
        gated = gate_weights(_WEIGHTS, registry, drift_config)
        assert gated["judge.cinematic"] == 0.0  # confirmed_drift（更严重）优先

    def test_其他评估器键不受本次处置影响(self, drift_data_dir, drift_config):
        # judge.cinematic 处于 suspect：proxy 键只参与归一，不因自身状态被改
        gated = gate_weights(_WEIGHTS, _suspect(drift_data_dir), drift_config)
        assert gated["proxy.aesthetic"] > _WEIGHTS["proxy.aesthetic"]  # 归一抬升
        assert gated["proxy.aesthetic"] / gated["proxy.identity_consistency"] == pytest.approx(
            _WEIGHTS["proxy.aesthetic"] / _WEIGHTS["proxy.identity_consistency"]
        )

    def test_配置驱动降权系数(self, drift_data_dir, drift_config):
        from dataclasses import replace

        registry = _suspect(drift_data_dir)
        half = gate_weights(_WEIGHTS, registry, drift_config)
        quarter = gate_weights(_WEIGHTS, registry, replace(drift_config, suspect_weight=0.25))
        assert quarter["judge.cinematic"] < half["judge.cinematic"]

    def test_配置驱动排除开关(self, drift_data_dir, drift_config):
        from dataclasses import replace

        registry = _confirmed(drift_data_dir)
        not_excluded = gate_weights(
            _WEIGHTS, registry, replace(drift_config, confirmed_exclude=False)
        )
        assert not_excluded["judge.cinematic"] == pytest.approx(0.25 * 0.5 / 0.875)  # 降权而非归零
        assert gate_weights(_WEIGHTS, registry, drift_config)["judge.cinematic"] == 0.0  # 默认排除

    def test_空权重与全归零不炸(self, drift_data_dir, drift_config):
        assert gate_weights({}, _suspect(drift_data_dir), drift_config) == {}
        zeroed = {"judge.cinematic": 0.25, "rule.x": "gate"}
        assert gate_weights(zeroed, _confirmed(drift_data_dir), drift_config) == {
            "judge.cinematic": 0.0,
            "rule.x": "gate",
        }


class Test三态合成差异:
    """SC-004：分级处置效果可区分（前/中/后三态对比）。"""

    def test_三态得分互相可区分(self, drift_data_dir, drift_config):
        normal = _normal_scores(
            gate_weights(_WEIGHTS, DriftRegistry.load(drift_data_dir), drift_config)
        )
        suspect = _normal_scores(gate_weights(_WEIGHTS, _suspect(drift_data_dir), drift_config))
        confirmed = _normal_scores(gate_weights(_WEIGHTS, _confirmed(drift_data_dir), drift_config))
        assert normal != suspect != confirmed
        # judge 得分低于其他分量 → 降权/排除后总分抬升（judge 不再主导）
        assert normal < suspect < confirmed
        assert confirmed == pytest.approx(0.8)  # judge 完全退出合成

    def test_编辑口径归一合成同样可区分(self, drift_data_dir, drift_config):
        from agents.editing.evaluators.composite import composite_editing

        breakdown = {
            "rule.duration_compliance@1.0.0": {"score": 1.0, "diagnostics": {}},
            "proxy.pacing_curve@1.0.0": {"score": 0.8, "diagnostics": {}},
            "judge.narrative_flow@1.0.0": {"score": 0.2, "diagnostics": {}},
        }
        weights = {
            "rule.duration_compliance": "gate",
            "proxy.pacing_curve": 0.6,
            "judge.narrative_flow": 0.4,
        }
        registry = _confirmed(drift_data_dir, "judge.narrative_flow@1.0.0")
        gated = gate_weights(weights, registry, drift_config)
        assert gated["judge.narrative_flow"] == 0.0
        assert composite_editing(breakdown, gated) > composite_editing(breakdown, weights)


class Test部署证据接口:
    def test_suspect_拒绝且带理由(self, drift_data_dir):
        verdict = deploy_evidence_verdict(_KEY, _suspect(drift_data_dir))
        assert verdict.allow is False
        assert "suspect" in verdict.reason
        assert "未处置" in verdict.reason or "处置" in verdict.reason

    def test_confirmed_drift_拒绝且带理由与留痕引用(self, drift_data_dir):
        registry = _confirmed(drift_data_dir)
        verdict = deploy_evidence_verdict(_KEY, registry)
        assert verdict.allow is False
        assert "confirmed_drift" in verdict.reason
        assert "dispositions" in verdict.reason  # 处置留痕引用进理由（可追溯）

    def test_normal_允许(self, drift_data_dir):
        verdict = deploy_evidence_verdict(_KEY, DriftRegistry.load(drift_data_dir))
        assert verdict.allow is True
        assert "normal" in verdict.reason

    def test_false_alarm_允许(self):
        # false_alarm 已由人工判为非漂移 → 允许（接口按状态判定，不按历史）
        registry = {
            _KEY: DriftStatus(
                evaluator_key=_KEY,
                status=DriftState.FALSE_ALARM,
                since=_AT,
                trigger_metrics="drift/metrics/visual/judge.cinematic/2026-W39.json",
                disposition_ref="drift/dispositions/judge.cinematic@1.0.0/false-alarm.json",
            )
        }
        verdict = deploy_evidence_verdict(_KEY, registry)
        assert verdict.allow is True
        assert "false_alarm" in verdict.reason

    def test_误报恢复后可部署(self, drift_data_dir):
        _suspect(drift_data_dir)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="calibrator",
            reason="样本骤降导致抖动，非真实漂移",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        verdict = deploy_evidence_verdict(_KEY, DriftRegistry.load(drift_data_dir))
        assert verdict.allow is True

    def test_接口先就位_未登记视为_normal(self):
        assert deploy_evidence_verdict(_KEY, {}).allow is True


class Test接线便捷函数:
    def test_未接线时权重原样(self, drift_data_dir, drift_config):
        assert apply_gate(_WEIGHTS, None) == _WEIGHTS

    def test_接线后等价于_gate_weights(self, drift_data_dir, drift_config):
        gate = DriftGate(cfg=drift_config, registry=_suspect(drift_data_dir))
        assert apply_gate(_WEIGHTS, gate) == gate_weights(_WEIGHTS, gate.registry, drift_config)

    def test_gate_装配(self, drift_data_dir, drift_config):
        _suspect(drift_data_dir)
        gate = DriftGate.load(drift_data_dir, drift_config)
        assert isinstance(gate.registry, DriftRegistry)
        assert gate.apply_weights(_WEIGHTS)["judge.cinematic"] < _WEIGHTS["judge.cinematic"]
