"""漂移监控报表与信度联动（功能 012 US3，契约 C6/C7/C8）。

`build_report(period, cfg, data_dir) -> DriftReport`（落盘 `drift/reports/{period}.json`）：

- **C6 周期报表**：per (agent, evaluator) 的**指标序列**（各周期检测记录）、基线引用、
  阈值快照、当前状态与处置记录（人/时间/理由/动作）——JSON 可机读，供人工决策与
  F9 证据引用；无检测记录的评估器标注"无数据"（不伪造）；范围外类别（proxy/rule
  默认关闭）标注"非 judge 类未纳入检测"；
- **C7 双信号联动**：漂移告警 ∧ 010 信度低于目标（`reports/{period}.json` 的
  `meets_target`）→ **强化告警**（级别升级为 `escalated_level` + `double_signal: true`
  + "双信号"标注）；单信号 → 常规告警（级别 `base_level`，不误升级别）；
- **C7 场景 3 残余信号附注**：F6 的 ScoreConflict 只作**附注**（`residual_signals`）——
  从约定持久化落点（`{data_dir 同级}/replay/hit_distributions/*.json` 的最新一份）读取；
  F6 侧未落盘时字段为空并注明"无持久化来源"（**不伪造冲突数据**）；
  附注**一律不参与阈值判定**（不产生告警、不改变判定口径）；
- **C8 只读**：报表只写 `drift/reports/`，010 产物（快照/台账/信度报告）与检测记录
  零写入；同输入同口径 → 报表逐字节一致（`generated_at` 可注入）。

检测口径版本（`detector_version`）随报表落地：口径变更不回溯改写历史报表（原则一）。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from core.calibration.drift_config import DriftConfig
from core.calibration.drift_metrics import detector_version, kind_of
from core.calibration.drift_models import DriftMetrics, DriftReport, DriftVerdict
from core.calibration.drift_status import current_status, dispositions

# F6 残余信号（ScoreConflict）约定持久化落点：{data_dir 同级}/replay/hit_distributions/
SCORE_CONFLICT_SOURCE_RELPATH = Path("replay") / "hit_distributions"
_NO_PERSISTED_SOURCE = "无持久化来源（F6 未落盘命中分布）"
# 附注口径：一律不参与阈值判定（机器可读字段 + 文档口径，FR-012）
_PARTICIPATES_IN_JUDGEMENT = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _metrics_by_evaluator(data_dir: str | Path) -> dict[tuple[str, str], list[DriftMetrics]]:
    """全部检测记录：{(agent_id, evaluator_id): [DriftMetrics 按周期升序]}。"""
    root = Path(data_dir) / "drift" / "metrics"
    collected: dict[tuple[str, str], list[DriftMetrics]] = {}
    if not root.is_dir():
        return collected
    for path in sorted(root.rglob("*.json")):
        record = DriftMetrics(**json.loads(path.read_text(encoding="utf-8")))
        collected.setdefault((record.agent_id, record.evaluator_key.partition("@")[0]), []).append(
            record
        )
    for records in collected.values():
        records.sort(key=lambda record: record.period)
    return collected


def _snapshot_pairs(data_dir: str | Path) -> set[tuple[str, str]]:
    """010 快照覆盖的 (agent_id, evaluator_id) 集合（无数据标注的枚举来源）。"""
    root = Path(data_dir) / "snapshots"
    pairs: set[tuple[str, str]] = set()
    if not root.is_dir():
        return pairs
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir():
            continue
        for evaluator_dir in sorted(agent_dir.iterdir()):
            if evaluator_dir.is_dir() and any(evaluator_dir.glob("*.json")):
                pairs.add((agent_dir.name, evaluator_dir.name))
    return pairs


def _registry_pairs(data_dir: str | Path) -> dict[tuple[str, str], str]:
    """状态登记覆盖的 (agent_id, evaluator_id) → evaluator_key（触发引用反解）。

    防止"已登记漂移但快照被清理"的评估器在报表中消失（登记即必须在报表中可见）。
    """
    root = Path(data_dir) / "drift" / "status"
    pairs: dict[tuple[str, str], str] = {}
    if not root.is_dir():
        return pairs
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        parts = Path(payload.get("trigger_metrics") or "").parts
        if len(parts) >= 4 and parts[0] == "drift" and parts[1] == "metrics":
            pairs[(parts[2], parts[3])] = payload["evaluator_key"]
    return pairs


def _period_versions(data_dir: str | Path, agent_id: str, evaluator_id: str) -> dict[str, str]:
    from core.calibration.drift_metrics import period_versions

    return period_versions(data_dir, agent_id, evaluator_id)


def _reliability_entries(data_dir: str | Path, period: str) -> dict[tuple[str, str], dict]:
    """010 信度报告（reports/{period}.json）→ {(agent_id, evaluator_key): 条目}。"""
    path = Path(data_dir) / "reports" / f"{period}.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = payload.get("target")
    entries: dict[tuple[str, str], dict] = {}
    for agent_id, evaluators in (payload.get("agents") or {}).items():
        for evaluator_key, entry in evaluators.items():
            metric = "kendall_tau" if entry.get("kendall_tau") is not None else "pearson_r"
            entries[(agent_id, evaluator_key)] = {
                "metric": metric,
                "value": entry.get(metric),
                "meets_target": bool(entry.get("meets_target")),
                "target": target,
                "samples": entry.get("samples"),
            }
    return entries


def _status_payload(data_dir: str | Path, evaluator_key: str | None) -> dict:
    if evaluator_key is None:
        return {
            "status": "normal",
            "since": None,
            "trigger_metrics": None,
            "disposition_ref": None,
        }
    status = current_status(data_dir, evaluator_key)
    if status is None:
        return {
            "status": "normal",
            "since": None,
            "trigger_metrics": None,
            "disposition_ref": None,
        }
    return {
        "status": status.status.value,
        "since": status.since,
        "trigger_metrics": status.trigger_metrics,
        "disposition_ref": status.disposition_ref,
    }


def _metric_entry(record: DriftMetrics) -> dict:
    return {
        "period": record.period,
        "verdict": record.verdict.value,
        "psi": record.psi,
        "quantile_shifts": dict(record.quantile_shifts),
        "samples": record.samples,
        "baseline_ref": record.baseline_ref,
        "detector_version": record.detector_version,
        "note": record.note,
    }


def _alert(
    *,
    agent_id: str,
    evaluator_key: str,
    metric_period: str | None,
    signals: list[str],
    reliability: dict | None,
    status_payload: dict,
    cfg: DriftConfig,
    detector: str,
) -> dict | None:
    """告警构造（C7）：双信号强化 / 单信号常规；无信号 → None（不产生告警）。"""
    if not signals:
        return None
    rule = cfg.double_signal
    double_signal = rule.enabled and len(signals) == 2
    level = rule.escalated_level if double_signal else rule.base_level
    reliability_part = ""
    if reliability is not None:
        meets = "meets_target=True" if reliability["meets_target"] else "meets_target=False"
        reliability_part = (
            f"010 信度 {reliability['metric']} {reliability['value']} "
            f"（目标 {reliability['target']}，{meets}）"
        )
    if double_signal:
        note = (
            f"双信号：漂移告警（指标周期 {metric_period}）∧ {reliability_part}；"
            f"级别升级 {rule.base_level} → {rule.escalated_level}"
        )
    elif signals == ["drift"]:
        note = f"常规告警：漂移（指标周期 {metric_period}）；{reliability_part or '无信度记录'}"
    else:
        note = f"常规告警：仅信度下降（{reliability_part}），无漂移告警"
    return {
        "agent_id": agent_id,
        "evaluator_key": evaluator_key,
        "metric_period": metric_period,
        "status": status_payload["status"],
        "signals": list(signals),
        "level": level,
        "double_signal": double_signal,
        "note": note,
        "detector_version": detector,
    }


def _item(
    agent_id: str,
    evaluator_id: str,
    records: list[DriftMetrics],
    known_key: str | None,
    *,
    cfg: DriftConfig,
    data_dir: str | Path,
    period: str,
    reliability: dict[tuple[str, str], dict],
    detector: str,
) -> dict:
    in_scope = kind_of(evaluator_id) in cfg.scope_kinds
    latest = records[-1] if records else None
    if latest is not None:
        evaluator_key = latest.evaluator_key
    elif known_key:
        evaluator_key = known_key
    else:
        # 无检测记录也无状态登记 → 版本取自 010 台账记录（升版则取最近一次记录的版本）；
        # 台账亦无记录 → 版本未知，按 evaluator_id 呈现并如实注明（不编造版本）
        versions = _period_versions(data_dir, agent_id, evaluator_id)
        evaluator_key = list(versions.values())[-1] if versions else evaluator_id
    status_payload = _status_payload(data_dir, evaluator_key)
    disposition_records = (
        [
            {
                "conclusion": disposition.conclusion.value,
                "by": disposition.by,
                "at": disposition.at,
                "reason": disposition.reason,
                "action": disposition.action.value,
            }
            for disposition in dispositions(data_dir, evaluator_key)
        ]
        if evaluator_key is not None
        else []
    )

    note_parts: list[str] = []
    data_state = "ok"
    if not in_scope:
        data_state = "out_of_scope"
        note_parts.append(
            f"非 judge 类未纳入检测（kind={kind_of(evaluator_id)}；"
            "calibration.drift.scope_kinds 默认仅 judge，如需纳入请显式配置）"
        )
    elif not records:
        data_state = "no_data"
        note_parts.append("无数据（尚无漂移检测记录，不判定；不伪造）")
    elif latest.period != period:
        note_parts.append(f"本期无记录（指标来自 {latest.period}，如实标注）")
    if "@" not in (evaluator_key or ""):
        note_parts.append("版本未知（无检测记录与台账记录，按 evaluator_id 呈现）")
    note = "；".join(note_parts)

    signals: list[str] = []
    metric_period: str | None = None
    if latest is not None and latest.verdict is DriftVerdict.DRIFT:
        signals.append("drift")
        metric_period = latest.period
    entry = reliability.get((agent_id, evaluator_key)) if evaluator_key else None
    if entry is not None and not entry["meets_target"]:
        signals.append("reliability_below_target")
    alert = _alert(
        agent_id=agent_id,
        evaluator_key=evaluator_key or evaluator_id,
        metric_period=metric_period,
        signals=signals,
        reliability=entry,
        status_payload=status_payload,
        cfg=cfg,
        detector=detector,
    )

    return {
        "agent_id": agent_id,
        "evaluator_key": evaluator_key,
        "kind": kind_of(evaluator_id),
        "in_scope": in_scope,
        "metrics": [_metric_entry(record) for record in records],
        "baseline": None if latest is None else latest.baseline_ref,
        "thresholds": dict(cfg.thresholds_snapshot() if latest is None else latest.thresholds),
        "status": status_payload,
        "dispositions": disposition_records,
        "detector_version": detector,
        "data_state": data_state,
        "signals": signals,
        "alert": alert,
        "note": note,
    }


def score_conflict_note(data_dir: str | Path, *, root: str | Path | None = None) -> dict:
    """F6 ScoreConflict 残余信号附注（C7 场景 3）：约定的持久化来源，无者如实注明。

    - 来源：`{root or data_dir 同级}/replay/hit_distributions/*.json` 中**最新**一份
      含 `conflicts` 列表的文件（F6 侧落盘口径；F6 当前只在内存产出命中分布）；
    - 无来源 → `{"available": False, "source": None, "conflicts": [], "note": "无持久化来源…"}`；
    - 返回值仅作报表附注，**不参与阈值判定**（不产生告警、不改变判定口径）。
    """
    source_root = (
        Path(root) if root is not None else Path(data_dir).parent / SCORE_CONFLICT_SOURCE_RELPATH
    )
    if source_root.is_dir():
        candidates = sorted(
            source_root.rglob("*.json"),
            key=lambda path: (path.stat().st_mtime_ns, str(path)),
            reverse=True,
        )
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue  # 损坏文件不作为来源（不伪造）
            conflicts = payload.get("conflicts")
            if isinstance(conflicts, list):
                return {
                    "available": True,
                    "source": str(path),
                    "generated_at": payload.get("generated_at"),
                    "conflicts": conflicts,
                    "participates_in_judgement": _PARTICIPATES_IN_JUDGEMENT,
                    "note": f"F6 命中分布来源 {path}（冲突 {len(conflicts)} 条）",
                }
    return {
        "available": False,
        "source": None,
        "conflicts": [],
        "participates_in_judgement": _PARTICIPATES_IN_JUDGEMENT,
        "note": _NO_PERSISTED_SOURCE,
    }


def build_report(
    period: str,
    cfg: DriftConfig,
    data_dir: str | Path,
    *,
    generated_at: str | None = None,
    score_conflict_root: str | Path | None = None,
) -> DriftReport:
    """生成周期漂移报表并落盘 `drift/reports/{period}.json`（C6/C7；只读其他产物）。"""
    data_dir = Path(data_dir)
    detector = detector_version(cfg)
    reliability = _reliability_entries(data_dir, period)
    collected = _metrics_by_evaluator(data_dir)
    registry_pairs = _registry_pairs(data_dir)

    pairs: dict[tuple[str, str], str | None] = {}
    for pair in sorted(set(collected) | _snapshot_pairs(data_dir) | set(registry_pairs)):
        pairs[pair] = registry_pairs.get(pair)

    items: list[dict] = []
    for (agent_id, evaluator_id), known_key in sorted(pairs.items()):
        records = [
            record
            for record in collected.get((agent_id, evaluator_id), [])
            if record.period <= period
        ]
        items.append(
            _item(
                agent_id,
                evaluator_id,
                records,
                known_key,
                cfg=cfg,
                data_dir=data_dir,
                period=period,
                reliability=reliability,
                detector=detector,
            )
        )

    alerts = [item["alert"] for item in items if item["alert"] is not None]
    alerts.sort(
        key=lambda alert: (not alert["double_signal"], alert["agent_id"], alert["evaluator_key"])
    )

    report = DriftReport(
        period=period,
        detector_version=detector,
        generated_at=generated_at or _now(),
        items=tuple(items),
        double_signal_rules={
            "enabled": cfg.double_signal.enabled,
            "reliability_target": cfg.double_signal.reliability_target,
            "base_level": cfg.double_signal.base_level,
            "escalated_level": cfg.double_signal.escalated_level,
        },
        alerts=tuple(alerts),
        residual_signals={
            "score_conflict": score_conflict_note(data_dir, root=score_conflict_root),
        },
    )
    path = data_dir / "drift" / "reports" / f"{period}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "period": report.period,
        "detector_version": report.detector_version,
        "generated_at": report.generated_at,
        "double_signal_rules": report.double_signal_rules,
        "items": [dict(item) for item in report.items],
        "alerts": [dict(alert) for alert in report.alerts],
        "residual_signals": report.residual_signals,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
