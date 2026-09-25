"""平台真值锚点适配（功能 010 US1 契约 C3；功能 020 契约 C7/C8；业务侧，保 core 业务无关）。

读 promo_campaigns 中已回流记录（metrics.platform_metrics），按 configs 的
promo.metric_weights 归一化口径（复用 PlatformMetricsEvaluator）合成 score，
转为 source=platform_truth 的 AnchorScore 写入 calibration_anchors
——与人评录入共用 insert_anchor 写入路径与唯一键幂等语义（同轮重复采集零变更）。

**功能 020 的归属日纪律（契约 C7）**：

- 锚点 `metric_date` = 平台指标所描述的日期（`snapshot["metric_date"]`）；
- **新写入不得走回退**：快照缺归属日且锚点 `created_at` 的日期 ≥
  `promo.attribution_date_required_since` ⇒ 该条**拒绝**（原因「平台未提供指标归属日」）；
- **历史行允许回退**：`created_at` 的日期早于该生效日的历史采集允许 `metric_date=NULL`，
  由 `attribution_fallback_count` 如实计数并在覆盖视图 `attribution_missing_anchors` 登记
  （**不静默补值**、不冒充已标定）；
- `_snapshot_created_at` 的"平台时间戳"语义**不改**：锚点 `created_at` 仍取平台时间戳；
- `collect_platform_anchors(period=...)` 给定时**只采归属日落在该周期窗口内**的行
  （周期窗口按**归属日**过滤，不是采集时刻）。
"""

from datetime import UTC, date, datetime

from sqlalchemy import Connection, Engine, select

from agents.promo.config import PromoConfig
from agents.promo.db import promo_campaigns
from agents.promo.evaluators.platform_metrics import PlatformMetricsEvaluator
from core.calibration.anchors import insert_anchor
from core.calibration.db import calibration_anchors
from core.calibration.models import AnchorScore
from core.calibration.periods import period_start, period_window
from core.evaluators.base import ArtifactRef
from core.tree.models import new_id


def _snapshot_created_at(snapshot: dict) -> str:
    """锚点 created_at 取平台时间戳（真值产生时刻）；缺失则以采集时刻兜底。"""
    ts = float(snapshot.get("platform_timestamp") or 0.0)
    if ts > 0:
        return datetime.fromtimestamp(ts, UTC).isoformat()
    return datetime.now(UTC).isoformat()


def _row_metric_date(snapshot: dict) -> str | None:
    """快照里的归属日（历史 payload 缺该键 ⇒ `None`，**不派生**）。"""
    value = snapshot.get("metric_date")
    return value if isinstance(value, str) and value else None


def _in_period(metric_date: str, created_at: str, period: str, *, config: PromoConfig) -> bool:
    """该行是否落在指定周期内（**按归属日**；无归属日的历史行按 `created_at` 的日期回退）。"""
    anchor_day = _fallback_day(metric_date, created_at)
    if anchor_day is None:
        return False
    start, end = period_window(period_start(period, config.period_days), config.period_days)
    return start <= anchor_day < end


def _fallback_day(metric_date: str | None, created_at: str) -> date | None:
    """归属日（优先）或 `created_at` 的日期（历史行回退）；两者皆不可解析 ⇒ `None`。"""
    candidate = metric_date or created_at[:10]
    try:
        return date.fromisoformat(candidate)
    except (TypeError, ValueError):
        return None


def collect_platform_anchors(
    promo_engine: Engine,
    anchors_conn: Connection,
    *,
    round_id: str,
    config: PromoConfig,
    period: str | None = None,
) -> list[AnchorScore]:
    """采集回流真值 → platform_truth 锚点入库（同键幂等拒绝，整批不中断）。

    reviewer 记渠道标识（material.platform）；`period` 给定时只采**归属日落在该周期
    窗口内**的行（按归属日过滤，不是采集时刻）；返回本轮新入库的锚点列表。
    缺归属日且 `created_at` 的日期 ≥ `promo.attribution_date_required_since` 的行
    **拒绝**（不落锚点）——缺失即失败，失败点在写入路径。
    """
    evaluator = PlatformMetricsEvaluator(config.metric_weights, ctr_cap=config.ctr_cap)
    required_since = date.fromisoformat(config.attribution_date_required_since)
    with promo_engine.connect() as conn:
        rows = conn.execute(
            select(promo_campaigns).where(promo_campaigns.c.status == "ingested")
        ).all()

    accepted: list[AnchorScore] = []
    for row in rows:
        payload = row.metrics or {}
        snapshot = payload.get("platform_metrics")
        material = payload.get("material") or {}
        if snapshot is None or not material.get("platform"):
            continue  # 未回流或缺渠道标识的行不参与锚点
        metric_date = _row_metric_date(snapshot)
        created_at = _snapshot_created_at(snapshot)
        if metric_date is None:
            # 缺失即失败 vs 历史回退：边界 = created_at 的日期 < attribution_date_required_since
            created_day = _fallback_day(None, created_at)
            if created_day is None or created_day >= required_since:
                continue  # 新写入不得走回退（不落锚点、不补值）
        if period is not None and not _in_period(metric_date, created_at, period, config=config):
            continue
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash=material["artifact_hash"]), {"metrics": snapshot}
        )
        anchor = AnchorScore(
            anchor_id=new_id(),
            node_id=row.node_id,
            artifact_hash=material["artifact_hash"],
            agent_id="promo",
            source="platform_truth",
            score=result.score,
            reviewer=material["platform"],  # 渠道标识
            round_id=round_id,
            created_at=created_at,
            metric_date=metric_date,  # 归属日（历史行可为 None，如实保留 NULL）
        )
        if insert_anchor(anchors_conn, anchor):
            accepted.append(anchor)
    return accepted


def attribution_fallback_count(anchors_conn, *, required_since: str) -> int:
    """归属日**历史回退计数**（契约 C7 的读侧口径；与覆盖视图 `attribution_missing_anchors` 同值）。

    `source='platform_truth' ∧ metric_date IS NULL ∧ created_at 的日期 < required_since`
    的行数——回退只作用于历史行、且以生效日为**唯一**边界；计数如实登记、不静默补值。
    """
    boundary = date.fromisoformat(required_since)

    def _count(conn) -> int:
        rows = conn.execute(
            select(calibration_anchors.c.created_at).where(
                calibration_anchors.c.source == "platform_truth",
                calibration_anchors.c.metric_date.is_(None),
            )
        ).all()
        total = 0
        for row in rows:
            try:
                created_day = date.fromisoformat(str(row.created_at)[:10])
            except ValueError:
                continue
            if created_day < boundary:
                total += 1
        return total

    if isinstance(anchors_conn, Engine):
        with anchors_conn.connect() as conn:
            return _count(conn)
    return _count(anchors_conn)


__all__ = ["attribution_fallback_count", "collect_platform_anchors"]
