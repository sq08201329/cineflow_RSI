"""宣发指标回流管道（T218 / 功能 015：自 ops 下沉，两段式落盘的第二段；
功能 020：按采集日分片 + 节点 id 含周期派生 + 节点自描述归属日）。

对 delivered / ingested 运营记录：`fetch_metrics` → 指标校验（越界或**归属日缺失**即拒绝并告警，
不写树）→ 一次性构造完整 `TreeNode`（含 `human.platform_metrics@1.0.0` 明细与全部成本）
单次 INSERT 落盘——节点从诞生即终态，写入即冻结 → 运营表回填 `ingested`。

**功能 020 的两处口径变化**（`agents/promo/daily.py` 的 `ingest_daily` 与之共用同一套纪律）：

- 候选选取由"仅 `delivered`"放宽为 `status IN ('delivered', 'ingested')`
  （首日 `delivered`、后续日 `ingested`）——"同一活动无法连续多日各采一次"的直接根因在此；
- 节点 id 改为**含周期的确定性派生** `daily_node_id(material_id, period)`
  （`{material_id}-node@{period}`）；**历史 `f"{material_id}-node"` 节点一律不回改**；
  节点 `observation_context` 新增 `metric_date` 与 `period` 两键（节点自描述其归属日，
  供 `core/calibration/selection.py` 的归属日过滤与事后归因使用）。

`created_at` **保持采集墙钟**（`time.time()`，语义不变）；`platform_timestamp` 仍是
"真值产生时刻"；归属日进 `metric_date`——三者不等同、并列可见。

幂等：重复执行时同 `(material_id, period)` 的节点 id 相同 ⇒ 撞主键视为已落盘跳过；
同 `(campaign_id, period)` 的重复回流由 `promo_daily_metrics` 的唯一键在 DB 层拒绝
（`ingest_daily`，零变更、整批不中断）。

**分层**（宪章原则五单向依赖）：本模块是库函数、位于业务侧（`agents/promo/`），
既有的 CLI 入口 `ops/ingest_metrics.py` 只作薄封装（参数解析 + DSN/环境装配）。
"""

import time
from dataclasses import asdict

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from agents.promo.config import PromoConfig
from agents.promo.daily import INGEST_CANDIDATE_STATUSES, daily_node_id
from agents.promo.db import promo_campaigns
from agents.promo.evaluators.platform_metrics import EVALUATOR_ID as METRICS_EVALUATOR_ID
from agents.promo.evaluators.platform_metrics import PlatformMetricsEvaluator
from agents.promo.loop import _round_root_id, round_tree_id
from agents.promo.platform.base import MetricSnapshot, validate_metrics
from core.calibration.periods import period_label
from core.evaluators.base import ArtifactRef, EvalResult
from core.evaluators.composite import composite_score_versioned
from core.tree.errors import DuplicateError
from core.tree.models import CostRecord, NodeStatus, TreeNode
from core.tree.store import TreeStore

#: 回流候选行的运营状态（首日 delivered、后续日 ingested）——**定义在** `agents/promo/daily.py`
#: （供本模块与 `daily.py` 共用单一份取值，避免两模块循环 import 与口径漂移）


def _snapshot_to_dict(snapshot: MetricSnapshot) -> dict:
    return asdict(snapshot)


def weights_of(config: PromoConfig) -> dict:
    """综合得分权重（gate 类记为 0）——两路径共用的唯一口径。"""
    return {
        key: (0.0 if str(value).lower() == "gate" else float(value))
        for key, value in config.evaluator_weights.items()
    }


def metrics_breakdown(
    metrics_payload: dict, snapshot: MetricSnapshot, config: PromoConfig
) -> tuple[dict, float]:
    """平台真值明细 + 综合得分：轮次内已产出的合规/CTR 片段 + 平台真值（写入即冻结）。

    单快照路径（`ingest_round`）与日级分片路径（`ingest_daily`）**共用本函数**——
    不得各写一套校验或各算一次得分。
    """
    evaluator = PlatformMetricsEvaluator(config.metric_weights, ctr_cap=config.ctr_cap)
    human_fragment = evaluator.evaluate(
        ArtifactRef(artifact_hash=metrics_payload["material"]["artifact_hash"]),
        {"metrics": _snapshot_to_dict(snapshot)},
    )
    breakdown: dict = dict(metrics_payload["eval_fragments"])
    breakdown[METRICS_EVALUATOR_ID + "@1.0.0"] = {
        "score": human_fragment.score,
        "diagnostics": human_fragment.diagnostics,
    }
    score = composite_score_versioned(
        {k: EvalResult(score=v["score"]) for k, v in breakdown.items()}, weights_of(config)
    )
    return breakdown, score


def build_metrics_node(
    *,
    metrics_payload: dict,
    snapshot: MetricSnapshot,
    config: PromoConfig,
    node_id: str,
    material_id: str,
    tree_id: str,
    root_id: str,
    created_at: float,
    metric_date: str,
    period: str,
) -> TreeNode:
    """构造平台回流节点（一次性完整 INSERT 的载荷）——两路径共用的唯一节点构造纪律。

    `observation_context` 自描述归属日与周期（`metric_date` / `period`），
    使"周期窗口按归属日过滤"有落点；节点 `created_at` 是**采集墙钟**（调用方传入）。
    """
    breakdown, score = metrics_breakdown(metrics_payload, snapshot, config)
    cost_info = metrics_payload["cost"]
    return TreeNode(
        node_id=node_id,
        tree_id=tree_id,
        parent_id=root_id,
        depth=1,
        agent_id="promo",
        policy_version="promo",
        prompt="",
        observation_context={
            "gen_params": metrics_payload["gen_params"],
            "material_id": material_id,
            # 物料分桶信息随节点落盘：CTR 历史汇聚（FR-012）的读取落点
            "material_kind": metrics_payload["material"]["kind"],
            "material_tags": metrics_payload["material"]["tags"],
            "material_platform": metrics_payload["material"]["platform"],
            # 功能 020：节点自描述归属日与周期（周期窗口按归属日过滤的落点）
            "metric_date": metric_date,
            "period": period,
        },
        artifact_hash=metrics_payload["material"]["artifact_hash"],
        eval_breakdown=breakdown,
        score=score,
        cost=CostRecord(
            llm_calls=cost_info.get("llm_calls", 0),
            llm_tokens=cost_info.get("llm_tokens", 0),
            generation_api_calls=1,
            generation_api_cost_usd=cost_info.get("total_usd", 0.0),
            wall_clock_seconds=cost_info.get("wall_clock_seconds", 0.0),
        ),
        status=NodeStatus.EVALUATED,
        created_at=created_at,
    )


def _update_campaign(
    engine: Engine,
    *,
    campaign_id: str,
    node_id: str,
    metrics_payload: dict,
    snapshot: MetricSnapshot,
) -> None:
    """运营表回填：`status=ingested` + **最近一次**落盘节点 + 最近一次快照。

    多日快照的**正本**在日级表 `promo_daily_metrics`；本列为"最近一次"快照（口径见注释）。
    """
    with engine.begin() as conn:
        conn.execute(
            update(promo_campaigns)
            .where(promo_campaigns.c.campaign_id == campaign_id)
            .values(
                status="ingested",
                node_id=node_id,
                metrics={**metrics_payload, "platform_metrics": _snapshot_to_dict(snapshot)},
                updated_at=time.time(),
            )
        )


def ingest_round(
    round_id: str,
    store: TreeStore,
    adapter,
    engine: Engine,
    config: PromoConfig,
) -> dict:
    """回流一轮（**单快照路径**，功能 003/015 既有语义保留）：
    delivered|ingested → 校验 → 完整节点 INSERT 冻结 → ingested。"""
    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)

    report: dict = {"round_id": round_id, "ingested": [], "rejected": [], "skipped": 0}
    with engine.connect() as conn:
        rows = conn.execute(
            select(promo_campaigns).where(
                promo_campaigns.c.round_id == round_id,
                promo_campaigns.c.status.in_(INGEST_CANDIDATE_STATUSES),
            )
        ).all()

    for row in rows:
        try:
            snapshot = adapter.fetch_metrics(row.external_id)
        except Exception as exc:  # PlatformError 族（未知活动/限流/不可用/平台数据缺失）：
            # **按条拒绝、整批不中断**（FR-004），且**不改运营表状态**——平台侧故障可重试，
            # 改状态会让该条永久退出候选面。候选放宽到 delivered|ingested 后（T2036①），
            # 历史活动可能已不在平台侧（多日分片下这是常态）。
            report["rejected"].append(
                {
                    "material_id": row.material_id,
                    "reason": f"平台采集失败：{exc}",
                    "retryable": True,
                }
            )
            continue
        try:
            validate_metrics(snapshot)  # 越界/缺归属日拒绝并告警：不写入树
        except Exception as exc:  # MetricValidationError 是 PlatformError 子类
            _mark_failed(engine, row.campaign_id, f"指标越界：{exc}")
            report["rejected"].append({"material_id": row.material_id, "reason": str(exc)})
            continue

        metrics_payload = row.metrics or {}
        period = period_label(snapshot.metric_date, config.period_days)
        node = build_metrics_node(
            metrics_payload=metrics_payload,
            snapshot=snapshot,
            config=config,
            node_id=daily_node_id(row.material_id, period),
            material_id=row.material_id,
            tree_id=tree_id,
            root_id=root_id,
            created_at=time.time(),  # 节点 created_at 保持**采集墙钟**（语义不变）
            metric_date=snapshot.metric_date,
            period=period,
        )
        try:
            store.append_node(node)  # 一次性完整 INSERT，落盘即冻结
        except DuplicateError:
            report["skipped"] += 1  # 幂等：同 (material_id, period) 已落盘
        else:
            report["ingested"].append(row.material_id)
        _update_campaign(
            engine,
            campaign_id=row.campaign_id,
            node_id=node.node_id,
            metrics_payload=metrics_payload,
            snapshot=snapshot,
        )
    return report


def _mark_failed(engine: Engine, campaign_id: str, reason: str) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            select(promo_campaigns.c.metrics).where(promo_campaigns.c.campaign_id == campaign_id)
        ).first()
        conn.execute(
            update(promo_campaigns)
            .where(promo_campaigns.c.campaign_id == campaign_id)
            .values(
                status="failed",
                metrics={**(row.metrics or {}), "reject_reason": reason},
                updated_at=time.time(),
            )
        )
