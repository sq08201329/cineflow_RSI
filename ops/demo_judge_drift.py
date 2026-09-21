#!/usr/bin/env python
"""端到端演示：judge 漂移自动检测（quickstart.md 六步，里程碑验收线 SC-001 / 立项书 F7）。

流程（确定性夹具 + 临时数据目录 + configs 副本，全程离线、零 LLM、零生成）：
  1. **基线建立**：连续若干周期锚点分布快照 → 滑动窗口基线；首周期 `no_baseline`
     （记基线不告警），随后周期判 `normal`；
  2. **漂移检出**：注入均值平移 / 方差展宽 / 双峰化三形态 → 100% 判漂移（超阈）
     + 自动登记 `suspect`（系统唯一自动写入）；
  3. **稳定不误报**：稳定序列 → `normal`（0 误报）；
  4. **分级处置**：`suspect` 降权 ×0.5（合成分数差异断言）→ 人工确认漂移 → `confirmed_drift`
     排除（权重归零，合成再变）；
  5. **证据接口**：`suspect` / `confirmed_drift` → 部署证据查询 100% 拒绝 + 理由；
     `normal` → 允许（F9 前置，SC-003）；
  6. **报表与联动**：周期报表（items 齐全 + 处置留痕如实呈现）+ 双信号强化告警
     （漂移 ∧ 010 信度低于目标）+ F6 ScoreConflict 附注（无持久化来源即注明）。

生产切换：临时数据目录换 `calibration/`（git 版本化）、配置副本换真实 configs、
快照由 010 `close_round` 周期性产出——代码路径不变，仅装配层替换。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


from core.calibration.drift_config import DriftConfig  # noqa: E402
from core.calibration.drift_gate import (  # noqa: E402
    deploy_evidence_verdict,
    gate_weights,
)
from core.calibration.drift_metrics import detect_drift  # noqa: E402
from core.calibration.drift_models import (  # noqa: E402
    DriftAction,
    DriftConclusion,
    DriftState,
    DriftVerdict,
)
from core.calibration.drift_report import build_report  # noqa: E402
from core.calibration.drift_status import (  # noqa: E402
    DriftRegistry,
    current_status,
    dispose,
    register_suspect,
)
from core.calibration.ledger import append_ledger, write_anchor_snapshots  # noqa: E402
from core.calibration.models import BiasRecord, PairingRecord  # noqa: E402
from core.calibration.report import build_report as build_reliability_report  # noqa: E402
from core.evaluators.base import EvalResult  # noqa: E402
from core.evaluators.composite import composite_score_versioned  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
PERIODS = ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38", "2026-W39")
CURRENT = PERIODS[-1]
AT = "2026-09-21T10:00:00+00:00"
VISUAL_WEIGHTS = {
    "rule.format_compliance": "gate",
    "proxy.aesthetic": 0.25,
    "proxy.identity_consistency": 0.35,
    "proxy.flicker": 0.15,
    "judge.cinematic": 0.25,
}
# 基线型窄峰分布（围绕 0.5、σ≈0.08；确定性，无随机源）
_Z_GRID = (
    -1.6,
    -1.3,
    -1.1,
    -0.9,
    -0.8,
    -0.7,
    -0.6,
    -0.5,
    -0.4,
    -0.3,
    -0.2,
    -0.1,
    0.0,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.8,
    0.9,
    1.1,
    1.3,
    1.6,
)


def _assert(condition, message):
    if not condition:
        raise AssertionError(f"演示断言失败：{message}")


def _step(number, title):
    print(f"\n=== 步骤 {number}：{title} ===")


def _scores(period_index: int, *, spread: float = 0.1, shift: float = 0.0) -> list[float]:
    """确定性分布夹具：中心 0.5、σ≈spread、整体平移 shift（形态 = 参数差异）。"""
    return [
        round(0.5 + shift + spread * z + 0.002 * ((i * 7 + period_index * 3) % 5 - 2), 6)
        for i, z in enumerate(_Z_GRID)
    ]


def _bimodal() -> list[float]:
    return [0.15] * 13 + [0.85] * 12


def _write_snapshot(data_dir, agent_id, evaluator_key, period, scores):
    """按 010 口径落盘锚点分布快照（真实 schema：分桶计数 + p25/p50/p75/p90）。"""
    pairs = [
        PairingRecord(
            anchor_id=f"{period}-a{index}",
            evaluator_key=evaluator_key,
            anchor_score=float(score),
            auto_score=0.5,
        )
        for index, score in enumerate(scores)
    ]
    return write_anchor_snapshots(data_dir, agent_id, period, pairs)


def _write_series(data_dir, agent_id, evaluator_key, scores_by_period):
    for period, scores in scores_by_period.items():
        _write_snapshot(data_dir, agent_id, evaluator_key, period, scores)


def _config_copy(path: Path) -> Path:
    """configs 副本：读真实 movie.yaml 原文照抄（演示不触碰真实配置与数据目录）。"""
    path.write_text(MOVIE_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _composite(weights, judge_score=0.2):
    """视觉权重口径的合成分（gate 字面量 → 0.0，与 loop 的 `_weights` 一致）。"""
    breakdown = {
        "rule.format_compliance@1.0.0": EvalResult(score=1.0),
        "proxy.aesthetic@1.0.0": EvalResult(score=0.8),
        "proxy.identity_consistency@1.0.0": EvalResult(score=0.8),
        "proxy.flicker@1.0.0": EvalResult(score=0.8),
        "judge.cinematic@1.0.0": EvalResult(score=judge_score),
    }
    numeric = {
        key: (0.0 if isinstance(value, str) else float(value)) for key, value in weights.items()
    }
    return composite_score_versioned(breakdown, numeric)


def _bootstrap(data_dir, cfg):
    """步骤 1：基线建立（首周期 no_baseline → 滑动窗口基线 → normal）。"""
    _step(1, "基线建立（首周期记基线不告警 → 滑动窗口基线）")
    key = "judge.cinematic@1.0.0"
    _write_snapshot(data_dir, "visual", key, PERIODS[0], _scores(0))
    first = detect_drift("visual", key, PERIODS[0], cfg, data_dir)
    _assert(first.verdict is DriftVerdict.NO_BASELINE, "首周期未记基线")
    _assert(first.psi is None and first.baseline_ref is None, "首周期不得携带指标/基线引用")
    print(f"首周期 {PERIODS[0]}：verdict={first.verdict.value}；{first.note}")

    _write_series(
        data_dir,
        "visual",
        key,
        {period: _scores(index) for index, period in enumerate(PERIODS[1:-1], start=1)},
    )
    baseline_period = PERIODS[-2]
    stable = detect_drift("visual", key, baseline_period, cfg, data_dir)
    _assert(stable.verdict is DriftVerdict.NORMAL, "稳定历史序列未判 normal")
    print(
        f"{baseline_period}：verdict={stable.verdict.value} PSI={stable.psi:.4f} "
        f"基线={stable.baseline_ref}"
    )
    return key, stable


def _detect_forms(data_dir, cfg):
    """步骤 2/3：三形态 100% 检出 + 稳定 0 误报。"""
    _step(2, "漂移检出（均值平移 / 方差展宽 / 双峰化 → 100% 超阈 + suspect 登记）")
    forms = (
        ("visual", "judge.cinematic@1.0.0", _scores(5, shift=0.2), "均值平移"),
        ("editing", "judge.narrative_flow@1.0.0", _scores(5, spread=0.18), "方差展宽"),
        ("storyboard", "judge.script_fit@1.0.0", _bimodal(), "双峰化"),
    )
    detected = []
    for agent_id, evaluator_key, current_scores, label in forms:
        _write_series(
            data_dir,
            agent_id,
            evaluator_key,
            {period: _scores(index) for index, period in enumerate(PERIODS[:-1], start=1)}
            | {CURRENT: current_scores},
        )
        metrics = detect_drift(agent_id, evaluator_key, CURRENT, cfg, data_dir)
        _assert(metrics.verdict is DriftVerdict.DRIFT, f"{label} 未检出漂移")
        _assert(metrics.psi > cfg.psi_threshold, f"{label} PSI 未超阈")
        register_suspect(data_dir, evaluator_key, metrics, at=AT)
        print(
            f"{agent_id}/{evaluator_key}：{label} → verdict={metrics.verdict.value} "
            f"PSI={metrics.psi:.4f}；{metrics.note.split('；')[0]}"
        )
        detected.append((agent_id, evaluator_key, metrics))
    _assert(len(detected) == 3, "三形态未全部检出")
    registry = DriftRegistry.load(data_dir)
    _assert(
        all(registry.lookup(key).status is DriftState.SUSPECT for _, key, _ in detected),
        "超阈未登记 suspect",
    )

    _step(3, "稳定不误报（稳定序列 → normal）")
    stable_key = "judge.dramatic_tension@1.0.0"
    _write_series(
        data_dir,
        "screenplay",
        stable_key,
        {period: _scores(index) for index, period in enumerate(PERIODS, start=1)},
    )
    stable = detect_drift("screenplay", stable_key, CURRENT, cfg, data_dir)
    _assert(stable.verdict is DriftVerdict.NORMAL, "稳定序列误报")
    print(f"screenplay/{stable_key}：verdict={stable.verdict.value} PSI={stable.psi:.4f}（0 误报）")
    return detected, stable_key


def _dispose_and_gate(data_dir, cfg, detected):
    """步骤 4：分级处置（suspect 降权 → 人工确认 → confirmed_drift 排除）。"""
    _step(4, "分级处置（suspect 降权 ×0.5 → 人工确认 → confirmed_drift 排除）")
    registry = DriftRegistry.load(data_dir)
    normal_weights = gate_weights(VISUAL_WEIGHTS, DriftRegistry(Path(data_dir) / "none"), cfg)
    _assert(normal_weights == VISUAL_WEIGHTS, "normal 态权重被改动")
    normal_score = _composite(normal_weights)

    suspect_weights = gate_weights(VISUAL_WEIGHTS, registry, cfg)
    suspect_score = _composite(suspect_weights)
    _assert(
        suspect_weights["judge.cinematic"] < VISUAL_WEIGHTS["judge.cinematic"],
        "suspect 未降权",
    )
    _assert(suspect_score != normal_score, "suspect 合成结果与正常态不可区分")
    print(
        f"suspect 降权：judge.cinematic {VISUAL_WEIGHTS['judge.cinematic']} → "
        f"{suspect_weights['judge.cinematic']:.6f}；合成分 "
        f"{normal_score:.6f} → {suspect_score:.6f}（judge 低分不再主导）"
    )

    key = detected[0][1]
    disposition = dispose(
        data_dir,
        key,
        DriftConclusion.CONFIRMED_DRIFT,
        by="校准负责人",
        reason="分布持续右移且人评锚点同步走低，换锚点升版",
        action=DriftAction.REANCHOR,
        at="2026-09-21T11:00:00+00:00",
    )
    confirmed_weights = gate_weights(VISUAL_WEIGHTS, DriftRegistry.load(data_dir), cfg)
    confirmed_score = _composite(confirmed_weights)
    _assert(confirmed_weights["judge.cinematic"] == 0.0, "confirmed_drift 未排除")
    _assert(confirmed_score != suspect_score, "confirmed_drift 合成结果与 suspect 不可区分")
    print(
        f"人工确认漂移（{disposition.action.value}，留痕 {disposition.by}）→ "
        f"confirmed_drift 权重归零；合成分 {confirmed_score:.6f}"
    )
    return {"normal": normal_score, "suspect": suspect_score, "confirmed": confirmed_score}


def _evidence(data_dir):
    """步骤 5：部署证据接口（SC-003：suspect/confirmed_drift 100% 拒绝）。"""
    _step(5, "部署证据接口（F9 前置：拒绝 + 理由；SC-003）")
    registry = DriftRegistry.load(data_dir)
    rejected, allowed = [], []
    for evaluator_key, status in sorted(registry.current().items()):
        verdict = deploy_evidence_verdict(evaluator_key, registry)
        if status.status in (DriftState.SUSPECT, DriftState.CONFIRMED_DRIFT):
            _assert(verdict.allow is False, f"{evaluator_key} 未拒绝")
            _assert(verdict.reason, f"{evaluator_key} 拒绝无理由")
            rejected.append(evaluator_key)
            print(f"拒绝：{evaluator_key}（{status.status.value}）——{verdict.reason[:48]}…")
        else:
            _assert(verdict.allow is True, f"{evaluator_key} 未允许")
            allowed.append(evaluator_key)
    unregistered = deploy_evidence_verdict("judge.unknown@1.0.0", registry)
    _assert(unregistered.allow is True, "未登记（默认 normal）应允许")
    _assert(len(rejected) == 3, "拒绝数不等于已登记漂移数（100% 拒绝被破坏）")
    print(f"拒绝 {len(rejected)} / 允许 {len(allowed) + 1}（含未登记默认 normal）——拒绝率 100%")
    return rejected


def _report(data_dir, cfg):
    """步骤 6：周期报表 + 双信号强化告警 + F6 附注。"""
    _step(6, "报表与联动（items 齐全 / 双信号强化告警 / F6 附注）")
    append_ledger(
        data_dir,
        "visual",
        [
            BiasRecord(
                evaluator_key="judge.cinematic@1.0.0",
                period=CURRENT,
                samples=12,
                kendall_tau=0.3,  # 低于信度目标 0.6 → 信度下降信号
            )
        ],
    )
    reliability = build_reliability_report(data_dir, CURRENT, target=0.6)
    report = build_report(CURRENT, cfg, data_dir)
    _assert(report.items, "报表 items 为空")
    required = {"metrics", "baseline", "thresholds", "status", "dispositions"}
    for item in report.items:
        _assert(required <= set(item), f"{item['evaluator_key']} 字段缺失")
    escalated = [alert for alert in report.alerts if alert["double_signal"]]
    _assert(len(escalated) == 1, "双信号强化告警路径未触发")
    alert = escalated[0]
    _assert(alert["level"] == cfg.double_signal.escalated_level, "双信号未升级级别")
    print(
        f"信度报告 target={reliability['target']}；强化告警 {alert['evaluator_key']} "
        f"level={alert['level']}（{alert['signals']}）"
    )
    note = report.residual_signals["score_conflict"]
    _assert(note["available"] is False, "F6 无持久化来源时不得伪造冲突")
    _assert("无持久化来源" in note["note"], "F6 附注未注明无持久化来源")
    print(f"F6 ScoreConflict 附注：{note['note']}（不参与阈值判定）")
    path = Path(data_dir) / "drift" / "reports" / f"{CURRENT}.json"
    _assert(path.is_file(), "报表未落盘")
    return report, path


def main() -> int:
    started = time.perf_counter()
    report: dict = {"steps": {}}
    with tempfile.TemporaryDirectory(prefix="cineflow-drift-demo-") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "calibration"
        for sub in ("snapshots", "ledger", "reports"):
            (data_dir / sub).mkdir(parents=True)
        for sub in ("metrics", "status", "dispositions", "reports"):
            (data_dir / "drift" / sub).mkdir(parents=True)

        config_path = _config_copy(tmp_path / "movie.yaml")
        cfg = DriftConfig.from_yaml(config_path)
        print(
            f"配置副本 {config_path.name}：window={cfg.window} buckets={cfg.buckets} "
            f"psi_threshold={cfg.psi_threshold} quantile_threshold={cfg.quantile_threshold} "
            f"min_samples={cfg.min_samples} scope_kinds={list(cfg.scope_kinds)}"
        )

        key, stable = _bootstrap(data_dir, cfg)
        detected, stable_key = _detect_forms(data_dir, cfg)
        scores = _dispose_and_gate(data_dir, cfg, detected)
        rejected = _evidence(data_dir)
        drift_report, report_path = _report(data_dir, cfg)

        report["steps"] = {
            "baseline": {"first_period": PERIODS[0], "verdict": "no_baseline"},
            "detected": [
                {"agent_id": agent_id, "evaluator_key": key, "psi": metrics.psi}
                for agent_id, key, metrics in detected
            ],
            "stable": {"evaluator_key": stable_key, "verdict": stable.verdict.value},
            "dispositions": {"composite_scores": scores},
            "evidence_rejected": rejected,
            "report": {
                "path": str(report_path),
                "alerts": [alert["level"] for alert in drift_report.alerts],
                "score_conflict": drift_report.residual_signals["score_conflict"]["available"],
            },
        }
        registry = DriftRegistry.load(data_dir)
        report["registry"] = {
            evaluator_key: status.status.value
            for evaluator_key, status in sorted(registry.current().items())
        }
        report["detector_version"] = drift_report.detector_version
        report["data_files"] = sorted(
            str(path.relative_to(data_dir)) for path in data_dir.rglob("*.json")
        )
        _assert(
            current_status(data_dir, key).status is DriftState.CONFIRMED_DRIFT,
            "人工处置结果未生效",
        )

    elapsed = time.perf_counter() - started
    report["elapsed_seconds"] = round(elapsed, 3)
    report["elapsed_under_1min"] = elapsed < 60
    report["ok"] = report["elapsed_under_1min"]
    print("\n=== 演示汇总 ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
