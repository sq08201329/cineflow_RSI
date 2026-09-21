"""漂移检测契约聚合（功能 012 / T1121）：C1~C8 全场景端到端断言。

三份契约逐条核对（drift-detect.md C1~C2、drift-gate.md C3~C5、drift-report.md C6~C8），
并承载三个机检门禁：
- **SC-002** 系统自动写入的状态仅 `suspect`（扫描全部状态登记：非 suspect 必带人工留痕引用，
  suspect 必带触发引用且无人工留痕）+ 系统 API 无"直接写终态"通路；
- **SC-003** `suspect`/`confirmed_drift` 的自动部署证据查询 100% 返回拒绝
  （normal/false_alarm 允许）；
- **SC-005** 检测/登记/报表全流程只读：树零写入、评估器与配置零变更、零 LLM/生成调用、
  010 产物（snapshots/ledger/reports）零写入——新增仅限本特性自己的 `drift/` 产物。

口径说明：判定范围默认仅 judge 类（`scope_kinds`）；`suspect` 降权系数默认 0.5、
`confirmed_drift` 排除；双信号 = 漂移 ∧ 010 信度低于 target（配置规则）。
"""

import json
import sys
from pathlib import Path

import pytest

from core.calibration.drift_config import DriftConfig
from core.calibration.drift_gate import deploy_evidence_verdict, gate_weights
from core.calibration.drift_metrics import detect_drift, record_path
from core.calibration.drift_models import (
    DriftAction,
    DriftConclusion,
    DriftState,
    DriftVerdict,
)
from core.calibration.drift_status import (
    DriftRegistry,
    current_status,
    dispose,
    register_suspect,
)
from core.evaluators.base import EvalResult
from core.evaluators.composite import composite_score_versioned

_AGENT = "visual"
_KEY = "judge.cinematic@1.0.0"
_PERIOD = "2026-W39"
_AT = "2026-09-21T10:00:00+00:00"
_VISUAL_WEIGHTS = {
    "rule.format_compliance": "gate",
    "proxy.aesthetic": 0.25,
    "proxy.identity_consistency": 0.35,
    "proxy.flicker": 0.15,
    "judge.cinematic": 0.25,
}


def _fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _registry_history(data_dir) -> list[tuple[str, dict]]:
    """全部状态登记的历史条目（SC-002 机检输入）。"""
    entries: list[tuple[str, dict]] = []
    root = Path(data_dir) / "drift" / "status"
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["history"]:
            entries.append((payload["evaluator_key"], entry))
    return entries


def _detect(drift_data_dir, drift_config, drift_sequence_writer, variant, *, only=None, **kwargs):
    drift_sequence_writer(variant, agent_id=_AGENT, evaluator_key=_KEY)
    metrics = None
    for period in ("2026-W38", _PERIOD):
        if only is not None and period != only:
            continue
        metrics = detect_drift(_AGENT, _KEY, period, drift_config, drift_data_dir)
    return metrics


class TestC1一轮检测:
    def test_六场景端到端(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        write_drift_snapshots,
        write_calibration_ledger,
    ):
        # 场景 1：稳定分布 → normal（0 误报）
        stable = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "stable", only=_PERIOD
        )
        assert stable.verdict is DriftVerdict.NORMAL
        assert stable.psi <= drift_config.psi_threshold

        # 场景 2：三形态注入 → 100% 判漂移（超阈）
        for index, variant in enumerate(("mean_shift", "variance_widen", "bimodal")):
            agent = f"agent-{index}"
            drift_sequence_writer(variant, agent_id=agent, evaluator_key=_KEY)
            metrics = detect_drift(agent, _KEY, _PERIOD, drift_config, drift_data_dir)
            assert metrics.verdict is DriftVerdict.DRIFT, variant
            assert metrics.psi > drift_config.psi_threshold

        # 场景 3：首周期 → no_baseline（记基线不告警）
        drift_sequence_writer(
            "first_period", agent_id="fresh", evaluator_key=_KEY, data_dir=drift_data_dir
        )
        fresh = detect_drift("fresh", _KEY, _PERIOD, drift_config, drift_data_dir)
        assert fresh.verdict is DriftVerdict.NO_BASELINE
        assert fresh.psi is None and fresh.baseline_ref is None

        # 场景 4：样本不足 → insufficient（不硬判）
        drift_sequence_writer("insufficient_current", agent_id="thin", evaluator_key=_KEY)
        thin = detect_drift("thin", _KEY, _PERIOD, drift_config, drift_data_dir)
        assert thin.verdict is DriftVerdict.INSUFFICIENT
        assert thin.psi is None

        # 场景 5：评估器升版 → 新版本独立基线（旧版本数据不混入；版本边界取自 010 台账）
        drift_sequence_writer("stable", agent_id="upgraded", evaluator_key=_KEY)
        write_calibration_ledger(
            [
                {"evaluator_key": _KEY, "period": period, "samples": 25, "kendall_tau": 0.5}
                for period in ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38")
            ],
            agent_id="upgraded",
        )
        upgraded_key = "judge.cinematic@2.0.0"
        write_drift_snapshots(
            {"2026-W39": [0.5, 0.6, 0.7]},
            agent_id="upgraded",
            evaluator_key=upgraded_key,
        )
        upgraded = detect_drift("upgraded", upgraded_key, _PERIOD, drift_config, drift_data_dir)
        assert upgraded.verdict is DriftVerdict.NO_BASELINE

        # 场景 6：proxy/rule 默认关闭 → 显式拒绝（非静默跳过）
        from core.calibration.errors import DriftOutOfScopeError

        write_drift_snapshots(
            {"2026-W38": [0.5] * 5, _PERIOD: [0.9] * 5},
            agent_id="proxy-agent",
            evaluator_key="proxy.aesthetic@1.0.0",
        )
        with pytest.raises(DriftOutOfScopeError, match="未纳入"):
            detect_drift(
                "proxy-agent", "proxy.aesthetic@1.0.0", _PERIOD, drift_config, drift_data_dir
            )

    def test_记录落盘路径契约(self, drift_data_dir, drift_config, drift_sequence_writer):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable", only=_PERIOD)
        path = record_path(drift_data_dir, _AGENT, _KEY, _PERIOD)
        assert path == (
            drift_data_dir / "drift" / "metrics" / _AGENT / "judge.cinematic" / f"{_PERIOD}.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert set(payload) == {
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


class TestC2口径版本化:
    def test_同输入逐字节一致且历史不改写(
        self, drift_data_dir, drift_config, drift_sequence_writer
    ):
        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable")
        path = record_path(drift_data_dir, _AGENT, _KEY, _PERIOD)
        before = path.read_bytes()
        same = detect_drift(_AGENT, _KEY, _PERIOD, drift_config, drift_data_dir)
        assert path.read_bytes() == before
        assert same.detector_version.startswith("drift_detector@1.0.0+")

        upgraded = DriftConfig.from_dict(
            {
                "calibration": {
                    "drift": {
                        "window": 5,
                        "buckets": 10,
                        "psi_threshold": 0.05,  # 判定口径变更 → 新版本
                        "quantile_threshold": 0.1,
                        "min_samples": 3,
                        "suspect_weight": 0.5,
                        "confirmed_exclude": True,
                        "scope_kinds": ["judge"],
                        "double_signal": {
                            "enabled": True,
                            "reliability_target": 0.6,
                            "base_level": "warning",
                            "escalated_level": "critical",
                        },
                    }
                }
            }
        )
        assert upgraded.psi_threshold != drift_config.psi_threshold
        newer = detect_drift(_AGENT, _KEY, "2026-W40", upgraded, drift_data_dir)
        assert newer.detector_version != same.detector_version
        assert path.read_bytes() == before  # 历史记录不被回溯改写


class TestC3状态机:
    def test_系统路径只写_suspect_终态仅人工(
        self, drift_data_dir, drift_config, drift_sequence_writer
    ):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        # 场景 1：超阈 → suspect 登记（trigger_metrics 引用检测记录）
        suspect = register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        assert suspect.status is DriftState.SUSPECT
        assert suspect.trigger_metrics == f"drift/metrics/{_AGENT}/judge.cinematic/{_PERIOD}.json"

        # 场景 2：人工确认漂移 → confirmed_drift + 留痕（停用）
        disposition = dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="校准负责人",
            reason="分布持续右移且人评锚点同步走低",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        assert disposition.action is DriftAction.DEACTIVATE
        confirmed = current_status(drift_data_dir, _KEY)
        assert confirmed.status is DriftState.CONFIRMED_DRIFT
        assert confirmed.disposition_ref and (drift_data_dir / confirmed.disposition_ref).is_file()

        # 场景 4：系统尝试直接写终态 → 模型层拒绝（机检）
        from core.evaluators.errors import ValidationError

        with pytest.raises(ValidationError, match="disposition_ref"):
            type(confirmed)(evaluator_key=_KEY, status=DriftState.CONFIRMED_DRIFT, since=_AT)

        # 场景 3：另一评估器人工判误报 → false_alarm → 恢复 normal
        other = "judge.script_fit@1.0.0"
        other_metrics = (
            detect_drift(_AGENT, other, _PERIOD, drift_config, drift_data_dir)
            if (drift_data_dir / "snapshots" / _AGENT / "judge.script_fit").is_dir()
            else None
        )
        if other_metrics is None:
            drift_sequence_writer(
                "mean_shift", agent_id=_AGENT, evaluator_key=other, data_dir=drift_data_dir
            )
            other_metrics = detect_drift(_AGENT, other, _PERIOD, drift_config, drift_data_dir)
        register_suspect(drift_data_dir, other, other_metrics, at=_AT)
        dispose(
            drift_data_dir,
            other,
            DriftConclusion.FALSE_ALARM,
            by="校准负责人",
            reason="样本量骤降导致分布抖动，非真实漂移",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        assert current_status(drift_data_dir, other).status is DriftState.NORMAL

    def test_SC002_机检_系统自动写入仅_suspect(
        self, drift_data_dir, drift_config, drift_sequence_writer
    ):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.FALSE_ALARM,
            by="校准负责人",
            reason="误报",
            action=DriftAction.RESTORE,
            at="2026-09-21T11:00:00+00:00",
        )
        entries = _registry_history(drift_data_dir)
        assert entries, "状态历史不得为空"
        for evaluator_key, entry in entries:
            if entry["status"] == "suspect":
                assert entry["trigger_metrics"], evaluator_key  # 系统写入必带触发引用
                assert not entry["disposition_ref"], evaluator_key  # 且无人工留痕
            else:
                assert entry["disposition_ref"], (evaluator_key, entry)  # 非 suspect 必人工
        assert [entry["status"] for _, entry in entries] == [
            "suspect",
            "false_alarm",
            "normal",
        ]


class TestC4合成门禁:
    def test_三态权重与合成分数可区分(self, drift_data_dir, drift_config, drift_sequence_writer):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        registry = DriftRegistry.load(drift_data_dir)
        normal_weights = gate_weights(_VISUAL_WEIGHTS, registry, drift_config)
        assert normal_weights == _VISUAL_WEIGHTS  # 场景 1：normal → 权重不变

        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        registry = DriftRegistry.load(drift_data_dir)
        suspect_weights = gate_weights(_VISUAL_WEIGHTS, registry, drift_config)
        assert suspect_weights["judge.cinematic"] == pytest.approx(0.25 * 0.5 / 0.875)

        dispose(
            drift_data_dir,
            _KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="校准负责人",
            reason="确认漂移",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        registry = DriftRegistry.load(drift_data_dir)
        confirmed_weights = gate_weights(_VISUAL_WEIGHTS, registry, drift_config)
        assert confirmed_weights["judge.cinematic"] == 0.0

        breakdown = {
            "rule.format_compliance@1.0.0": EvalResult(score=1.0),
            "proxy.aesthetic@1.0.0": EvalResult(score=0.8),
            "proxy.identity_consistency@1.0.0": EvalResult(score=0.8),
            "proxy.flicker@1.0.0": EvalResult(score=0.8),
            "judge.cinematic@1.0.0": EvalResult(score=0.2),
        }

        def score(weights):
            numeric = {
                key: (0.0 if isinstance(value, str) else float(value))
                for key, value in weights.items()
            }
            return composite_score_versioned(breakdown, numeric)

        scores = [
            score(normal_weights),
            score(suspect_weights),
            score(confirmed_weights),
        ]
        assert scores[0] < scores[1] < scores[2]  # judge 低分 → 降权/排除后抬升，三态可区分


class TestC5部署证据接口:
    def test_SC003_拒绝率100pct(self, drift_data_dir, drift_config, drift_sequence_writer):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        other = "judge.script_fit@1.0.0"
        drift_sequence_writer(
            "mean_shift", agent_id=_AGENT, evaluator_key=other, data_dir=drift_data_dir
        )
        other_metrics = detect_drift(_AGENT, other, _PERIOD, drift_config, drift_data_dir)
        register_suspect(drift_data_dir, other, other_metrics, at=_AT)
        dispose(
            drift_data_dir,
            other,
            DriftConclusion.CONFIRMED_DRIFT,
            by="校准负责人",
            reason="确认漂移",
            action=DriftAction.REANCHOR,
            at="2026-09-21T11:00:00+00:00",
        )
        registry = DriftRegistry.load(drift_data_dir)

        rejected = 0
        for evaluator_key, status in registry.current().items():
            verdict = deploy_evidence_verdict(evaluator_key, registry)
            if status.status in (DriftState.SUSPECT, DriftState.CONFIRMED_DRIFT):
                rejected += 1
                assert verdict.allow is False, evaluator_key
                assert verdict.reason, evaluator_key
                assert status.status.value in verdict.reason
            else:  # normal / false_alarm → 允许
                assert verdict.allow is True, evaluator_key
        assert rejected == 2  # suspect + confirmed_drift 全部拒绝（100%）

        # 无登记（默认 normal）与 false_alarm → 允许
        assert deploy_evidence_verdict("judge.other@1.0.0", registry).allow is True
        false_alarm = {
            "judge.legacy@1.0.0": registry.current()[_KEY].__class__(
                evaluator_key="judge.legacy@1.0.0",
                status=DriftState.FALSE_ALARM,
                since=_AT,
                trigger_metrics="drift/metrics/visual/judge.legacy/2026-W39.json",
                disposition_ref="drift/dispositions/judge.legacy@1.0.0/t.json",
            )
        }
        assert deploy_evidence_verdict("judge.legacy@1.0.0", false_alarm).allow is True


class TestC6报表:
    def test_SC006_字段齐全率100pct与无数据标注(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        write_drift_snapshots,
        write_calibration_ledger,
    ):
        from core.calibration.drift_report import build_report

        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable")
        write_drift_snapshots(
            {"2026-W39": [0.5, 0.6, 0.7]}, agent_id=_AGENT, evaluator_key="proxy.aesthetic@1.0.0"
        )
        write_calibration_ledger(
            [
                {"evaluator_key": _KEY, "period": _PERIOD, "samples": 12, "kendall_tau": 0.3},
                {
                    "evaluator_key": "proxy.aesthetic@1.0.0",
                    "period": _PERIOD,
                    "samples": 3,
                    "pearson_r": 0.8,
                },
            ],
            agent_id=_AGENT,
        )
        from core.calibration.report import build_report as build_reliability_report

        build_reliability_report(drift_data_dir, _PERIOD, target=0.6)

        report = build_report(_PERIOD, drift_config, drift_data_dir)
        required = {
            "agent_id",
            "evaluator_key",
            "kind",
            "in_scope",
            "metrics",
            "baseline",
            "thresholds",
            "status",
            "dispositions",
            "detector_version",
            "data_state",
            "signals",
            "alert",
            "note",
        }
        for item in report.items:
            assert required <= set(item), item["evaluator_key"]
            assert set(item["status"]) == {
                "status",
                "since",
                "trigger_metrics",
                "disposition_ref",
            }
        judge = next(item for item in report.items if item["evaluator_key"] == _KEY)
        assert [entry["period"] for entry in judge["metrics"]] == ["2026-W38", "2026-W39"]
        proxy = next(item for item in report.items if item["evaluator_key"].startswith("proxy"))
        assert proxy["data_state"] == "out_of_scope"
        assert "非 judge 类未纳入" in proxy["note"]
        assert report.items, "报表至少一个 item"
        # 落盘可机读
        payload = json.loads(
            (drift_data_dir / "drift" / "reports" / f"{_PERIOD}.json").read_text(encoding="utf-8")
        )
        assert payload["period"] == _PERIOD


class TestC7双信号与附注:
    def test_强化与常规两路径(
        self, drift_data_dir, drift_config, drift_sequence_writer, write_calibration_ledger
    ):
        from core.calibration.drift_report import build_report
        from core.calibration.report import build_report as build_reliability_report

        drift_metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, drift_metrics, at=_AT)
        write_calibration_ledger(
            [{"evaluator_key": _KEY, "period": _PERIOD, "samples": 12, "kendall_tau": 0.3}],
            agent_id=_AGENT,
        )
        build_reliability_report(drift_data_dir, _PERIOD, target=0.6)

        escalated = build_report(_PERIOD, drift_config, drift_data_dir)
        alert = next(alert for alert in escalated.alerts if alert["evaluator_key"] == _KEY)
        assert alert["double_signal"] is True
        assert alert["level"] == drift_config.double_signal.escalated_level
        assert "双信号" in alert["note"]

        # 信度达标 → 单信号常规告警（不误升级别）
        write_calibration_ledger(
            [{"evaluator_key": _KEY, "period": _PERIOD, "samples": 12, "kendall_tau": 0.9}],
            agent_id=_AGENT,
        )
        build_reliability_report(drift_data_dir, _PERIOD, target=0.6)
        regular = build_report(_PERIOD, drift_config, drift_data_dir)
        alert = next(alert for alert in regular.alerts if alert["evaluator_key"] == _KEY)
        assert alert["double_signal"] is False
        assert alert["level"] == drift_config.double_signal.base_level

    def test_附注无持久化来源_不参与阈值判定(
        self, drift_data_dir, drift_config, drift_sequence_writer
    ):
        from core.calibration.drift_report import build_report

        _detect(drift_data_dir, drift_config, drift_sequence_writer, "stable", only=_PERIOD)
        report = build_report(_PERIOD, drift_config, drift_data_dir)
        note = report.residual_signals["score_conflict"]
        assert note["available"] is False
        assert note["source"] is None
        assert note["conflicts"] == []
        assert "无持久化来源" in note["note"]
        assert note["participates_in_judgement"] is False
        assert report.alerts == ()  # 附注不产生告警


class TestC8只读审计:
    def test_SC005_全流程只读(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        tree_store,
        build_calibration_tree,
        mock_gateway,
    ):
        from core.calibration.drift_report import build_report

        tree, _ = build_calibration_tree([(0.5, {"judge.cinematic@1.0.0": {"score": 0.6}})])
        drift_sequence_writer("mean_shift", agent_id=_AGENT, evaluator_key=_KEY)

        source_dirs = (
            Path(__file__).resolve().parents[2] / "core" / "evaluators",
            Path(__file__).resolve().parents[2] / "configs",
            Path(__file__).resolve().parents[2] / "calibration",
        )
        nodes_before = len(tree_store.nodes_of(tree.tree_id))
        before = _fingerprint(drift_data_dir)
        sources_before = {path: _fingerprint(path) for path in source_dirs}

        metrics = detect_drift(_AGENT, _KEY, _PERIOD, drift_config, drift_data_dir)
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        build_report(_PERIOD, drift_config, drift_data_dir)

        after = _fingerprint(drift_data_dir)
        changed = {name for name, meta in after.items() if before.get(name) != meta}
        added_010 = {name for name in changed if not name.startswith("drift/")}
        assert not added_010, added_010  # ① 010 产物（snapshots/ledger/reports）零写入
        assert changed, "全流程必须落盘本特性产物"
        assert len(tree_store.nodes_of(tree.tree_id)) == nodes_before  # ② 树零写入
        for path in source_dirs:
            assert _fingerprint(path) == sources_before[path]  # ③ 评估器/配置零变更
        assert mock_gateway.call_count == 0  # ④ 零 LLM 调用
        assert mock_gateway.total_cost_usd == 0.0  # 零生成成本


class TestCLI端到端:
    """CLI 一轮检测（quickstart 命令形态）：检测 → 登记 suspect → 报表 → 处置。"""

    def _cli(self, monkeypatch, capsys, *argv):
        from ops.calibrate import main

        monkeypatch.setattr(sys, "argv", ["calibrate.py", *argv])
        code = main()
        captured = capsys.readouterr()
        return code, json.loads(captured.out)

    def test_检测与报表退出码0(
        self,
        monkeypatch,
        capsys,
        tmp_path,
        drift_data_dir,
        drift_sequence_writer,
        write_calibration_ledger,
    ):
        drift_sequence_writer("mean_shift", agent_id=_AGENT, evaluator_key=_KEY)
        write_calibration_ledger(
            [
                {"evaluator_key": _KEY, "period": p, "samples": 12, "kendall_tau": 0.4}
                for p in ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38", _PERIOD)
            ],
            agent_id=_AGENT,
        )
        code, payload = self._cli(
            monkeypatch,
            capsys,
            "drift",
            "--period",
            _PERIOD,
            "--config",
            str(Path(__file__).resolve().parents[2] / "configs" / "movie.yaml"),
            "--data-dir",
            str(drift_data_dir),
        )
        assert code == 0
        judged = [entry for entry in payload["detected"] if entry["evaluator_key"] == _KEY]
        assert judged and judged[0]["verdict"] == "drift"
        assert any(_KEY in entry for entry in payload["registered_suspect"])
        assert Path(payload["report_path"]).is_file()
        assert current_status(drift_data_dir, _KEY).status is DriftState.SUSPECT

    def test_处置入口留痕后状态可见(
        self,
        monkeypatch,
        capsys,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
    ):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        code, payload = self._cli(
            monkeypatch,
            capsys,
            "drift",
            "--period",
            _PERIOD,
            "--config",
            str(Path(__file__).resolve().parents[2] / "configs" / "movie.yaml"),
            "--data-dir",
            str(drift_data_dir),
            "--dispose",
            _KEY,
            "--conclusion",
            "confirmed_drift",
            "--action",
            "reanchor",
            "--by",
            "校准负责人",
            "--reason",
            "分布持续右移，换锚点升版",
        )
        assert code == 0
        assert payload["report_path"]
        status = current_status(drift_data_dir, _KEY)
        assert status.status is DriftState.CONFIRMED_DRIFT
        assert status.disposition_ref and (drift_data_dir / status.disposition_ref).is_file()
        report = json.loads(
            (drift_data_dir / "drift" / "reports" / f"{_PERIOD}.json").read_text(encoding="utf-8")
        )
        item = next(item for item in report["items"] if item["evaluator_key"] == _KEY)
        assert item["status"]["status"] == "confirmed_drift"
        assert item["dispositions"][0]["action"] == "reanchor"

    def test_处置缺留痕要素报错(
        self, monkeypatch, capsys, drift_data_dir, drift_config, drift_sequence_writer
    ):
        metrics = _detect(
            drift_data_dir, drift_config, drift_sequence_writer, "mean_shift", only=_PERIOD
        )
        register_suspect(drift_data_dir, _KEY, metrics, at=_AT)
        code, payload = self._cli(
            monkeypatch,
            capsys,
            "drift",
            "--period",
            _PERIOD,
            "--config",
            str(Path(__file__).resolve().parents[2] / "configs" / "movie.yaml"),
            "--data-dir",
            str(drift_data_dir),
            "--dispose",
            _KEY,
            "--conclusion",
            "confirmed_drift",
        )
        assert code == 2
        assert "reason" in payload["error"]
        assert current_status(drift_data_dir, _KEY).status is DriftState.SUSPECT  # 零变更
