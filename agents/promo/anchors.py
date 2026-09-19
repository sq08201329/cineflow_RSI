"""平台真值锚点适配（功能 010 US1，契约 C3；业务侧适配，保 core 业务无关）。

读 promo_campaigns 中已 ingested 的回流记录（metrics.platform_metrics），
按 configs 的 promo.metric_weights 归一化口径（复用 PlatformMetricsEvaluator）
合成 score，转为 source=platform_truth 的 AnchorScore 写入 calibration_anchors
——与人评录入共用 insert_anchor 写入路径与唯一键幂等语义（同轮重复采集零变更）。
"""

from datetime import datetime, timezone

from sqlalchemy import Connection, Engine, select

from agents.promo.config import PromoConfig
from agents.promo.db import promo_campaigns
from agents.promo.evaluators.platform_metrics import PlatformMetricsEvaluator
from core.calibration.anchors import insert_anchor
from core.calibration.models import AnchorScore
from core.evaluators.base import ArtifactRef
from core.tree.models import new_id


def _snapshot_created_at(snapshot: dict) -> str:
    """锚点 created_at 取平台时间戳（真值产生时刻）；缺失则以采集时刻兜底。"""
    ts = float(snapshot.get("platform_timestamp") or 0.0)
    if ts > 0:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def collect_platform_anchors(
    promo_engine: Engine,
    anchors_conn: Connection,
    *,
    round_id: str,
    config: PromoConfig,
) -> list[AnchorScore]:
    """采集回流真值 → platform_truth 锚点入库（同键幂等拒绝，整批不中断）。

    reviewer 记渠道标识（material.platform）；返回本轮新入库的锚点列表。
    """
    evaluator = PlatformMetricsEvaluator(config.metric_weights, ctr_cap=config.ctr_cap)
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
            created_at=_snapshot_created_at(snapshot),
        )
        if insert_anchor(anchors_conn, anchor):
            accepted.append(anchor)
    return accepted
