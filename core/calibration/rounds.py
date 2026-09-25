"""轮次收口管线（功能 010 US2，T522；功能 020 起周期标签由 cadence 派生）。

close_round：配对 → 偏差 → 台账 → 锚点分布快照 → 信度报告，
轮次状态 open/intake → closed（样本不足照常 closed 并注明）。

周期标签取 `period_end` 与形态声明的 `calibration.period_days` 派生（周级 ⇒ ISO 周
`YYYY-Www`、日级 ⇒ 日期 `YYYY-MM-DD`），与台账/报告/快照共用；口径唯一实现见
`core/calibration/periods.py`（`iso_week_label` 在本模块**再导出**，既有读取点零改动）；
judge 口径按 evaluator_id 的 judge. 前缀判定（与 composite 的 rule. 前缀惯例一致）。
"""

from sqlalchemy import Connection

from core.calibration.anchors import load_anchors
from core.calibration.bias import compute_bias
from core.calibration.config import CalibrationConfig
from core.calibration.ledger import (
    append_ledger,
    materialize_anchor_snapshots,
    with_provenance,
)
from core.calibration.models import RoundStatus
from core.calibration.pairing import pair_anchors
from core.calibration.periods import cadence_of, iso_week_label, period_label
from core.calibration.report import build_report
from core.calibration.selection import load_round, save_round
from core.evaluators.errors import ValidationError
from core.tree.store import TreeStore

__all__ = ["close_round", "compute_bias_records", "iso_week_label"]

_JUDGE_PREFIX = "judge."


def compute_bias_records(pairs: list, period: str, config: CalibrationConfig) -> list:
    """按评估器分组产偏差记录（judge 走 τ，连续走 mean_shift + Pearson r）。

    收口管线与 CLI propose 共用同一口径（propose 依此重建本轮偏差证据）。
    """
    by_evaluator: dict[str, list] = {}
    for pair in pairs:
        by_evaluator.setdefault(pair.evaluator_key, []).append(pair)
    return [
        compute_bias(
            evaluator_pairs,
            evaluator_key=key,
            period=period,
            min_samples=config.min_samples,
            judge=key.split("@")[0].startswith(_JUDGE_PREFIX),
        )
        for key, evaluator_pairs in sorted(by_evaluator.items())
    ]


def _with_snapshot_provenance(record, by_evaluator, period_days, round_id, config):
    """给台账行补全口径与快照溯源（无该评估器快照 ⇒ 锚点数 0、指纹 None，如实标注）。"""
    materialization = by_evaluator.get(record.evaluator_key.split("@")[0])
    return with_provenance(
        record,
        period_days=period_days,
        window_semantics=config.window_semantics,
        round_id=round_id,
        anchor_count=materialization.anchor_count if materialization else 0,
        snapshot_fingerprint=(materialization.snapshot_fingerprint if materialization else None),
    )


def close_round(
    store: TreeStore,
    anchors_conn: Connection,
    data_dir,
    *,
    round_id: str,
    config: CalibrationConfig,
) -> dict:
    """收口一轮校准：配对 → 偏差 → 台账/快照/报告落盘 → 轮次 closed。"""
    round_, blind_list = load_round(data_dir, round_id)
    if round_.status is RoundStatus.CLOSED:
        raise ValidationError(f"轮次 {round_id} 已 closed，不可重复收口")

    anchors = load_anchors(anchors_conn, round_id)
    pairs = pair_anchors(anchors, store, config.self_pairing_exclusions)
    period = period_label(round_.period_end, config.period_days)

    # 快照 = **周期物化**（主键（评估器, 周期））：先物化取得锚点数与内容指纹，
    # 再由台账行登记溯源——"内容变了但无人知道"的次数恒为 0（契约 C3）。
    materializations = materialize_anchor_snapshots(data_dir, round_.agent_id, period, pairs)
    by_evaluator = {item.evaluator_id: item for item in materializations}
    period_days = cadence_of(period)
    records = [
        _with_snapshot_provenance(record, by_evaluator, period_days, round_.round_id, config)
        for record in compute_bias_records(pairs, period, config)
    ]

    # 派生产物同轮次落盘：台账（append-only，已带口径与快照溯源）+ 信度报告
    # 报告按**轮标识**落盘（同周期多轮并留存、零覆盖；缺省 run_id 才走兼容别名路径）
    append_ledger(data_dir, round_.agent_id, records)
    report = build_report(
        data_dir,
        period,
        target=config.reliability_target,
        window_semantics=config.window_semantics,
        window_semantics_change_date=config.window_semantics_change_date,
        run_id=round_.round_id,
    )

    # 轮次状态机：open → intake → closed；样本不足照常 closed 并注明
    insufficient = [
        r.evaluator_key for r in records if r.pearson_r is None and r.kendall_tau is None
    ]
    current = round_
    if current.status is RoundStatus.OPEN:
        current = current.transition(RoundStatus.INTAKE)
    closed = current.transition(RoundStatus.CLOSED)
    if insufficient:
        note = f"样本不足评估器：{', '.join(insufficient)}"
        closed = type(closed)(
            **{**closed.__dict__, "note": f"{closed.note}；{note}" if closed.note else note}
        )
    save_round(data_dir, closed, blind_list)

    return {
        "round_id": round_id,
        "period": period,
        "records": len(records),
        "alerts": report["alerts"],
        "status": "closed",
    }
