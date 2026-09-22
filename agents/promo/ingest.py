"""宣发指标回流管道（T218 / 功能 015：自 ops 下沉，两段式落盘的第二段）。

对 delivered 运营记录：`fetch_metrics` → 指标校验（越界拒绝并告警，不写树）→
一次性构造完整 `TreeNode`（含 `human.platform_metrics@1.0.0` 明细与全部成本）
单次 INSERT 落盘——节点从诞生即终态，写入即冻结 → 运营表回填 `ingested`。

幂等：重复执行只处理 delivered 行；节点撞主键视为已落盘跳过。

**分层**（宪章原则五单向依赖）：本模块是库函数、位于业务侧（`agents/promo/`），
既有的 CLI 入口 `ops/ingest_metrics.py` 只作薄封装（参数解析 + DSN/环境装配）。
"""

import time
from dataclasses import asdict

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from agents.promo.config import PromoConfig
from agents.promo.db import promo_campaigns
from agents.promo.evaluators.platform_metrics import EVALUATOR_ID as METRICS_EVALUATOR_ID
from agents.promo.evaluators.platform_metrics import PlatformMetricsEvaluator
from agents.promo.loop import _round_root_id, round_tree_id
from agents.promo.platform.base import MetricSnapshot, validate_metrics
from core.evaluators.base import ArtifactRef, EvalResult
from core.evaluators.composite import composite_score_versioned
from core.evaluators.errors import ValidationError
from core.tree.errors import DuplicateError
from core.tree.models import CostRecord, NodeStatus, TreeNode
from core.tree.store import TreeStore


def _snapshot_to_dict(snapshot: MetricSnapshot) -> dict:
    return asdict(snapshot)


def ingest_round(
    round_id: str,
    store: TreeStore,
    adapter,
    engine: Engine,
    config: PromoConfig,
) -> dict:
    """回流一轮：delivered → 校验 → 完整节点 INSERT 冻结 → ingested。"""
    metrics_evaluator = PlatformMetricsEvaluator(config.metric_weights, ctr_cap=config.ctr_cap)
    weights = {
        key: (0.0 if str(value).lower() == "gate" else float(value))
        for key, value in config.evaluator_weights.items()
    }
    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)

    report: dict = {"round_id": round_id, "ingested": [], "rejected": [], "skipped": 0}
    with engine.connect() as conn:
        rows = conn.execute(
            select(promo_campaigns).where(
                promo_campaigns.c.round_id == round_id,
                promo_campaigns.c.status == "delivered",
            )
        ).all()

    for row in rows:
        snapshot = adapter.fetch_metrics(row.external_id)
        try:
            validate_metrics(snapshot)  # 越界拒绝并告警：不写入树
        except ValidationError as exc:
            _mark_failed(engine, row.campaign_id, f"指标越界：{exc}")
            report["rejected"].append({"material_id": row.material_id, "reason": str(exc)})
            continue
        except Exception as exc:  # MetricValidationError 是 PlatformError 子类
            _mark_failed(engine, row.campaign_id, f"指标越界：{exc}")
            report["rejected"].append({"material_id": row.material_id, "reason": str(exc)})
            continue

        metrics_payload = row.metrics or {}
        human_fragment = metrics_evaluator.evaluate(
            ArtifactRef(artifact_hash=metrics_payload["material"]["artifact_hash"]),
            {"metrics": _snapshot_to_dict(snapshot)},
        )
        # 完整明细 = 轮次内已产出的合规/CTR 片段 + 平台真值（写入即冻结）
        breakdown: dict = dict(metrics_payload["eval_fragments"])
        breakdown[METRICS_EVALUATOR_ID + "@1.0.0"] = {
            "score": human_fragment.score,
            "diagnostics": human_fragment.diagnostics,
        }
        score = composite_score_versioned(
            {k: EvalResult(score=v["score"]) for k, v in breakdown.items()}, weights
        )
        cost_info = metrics_payload["cost"]
        node = TreeNode(
            node_id=f"{row.material_id}-node",
            tree_id=tree_id,
            parent_id=root_id,
            depth=1,
            agent_id="promo",
            policy_version="promo",
            prompt="",
            observation_context={
                "gen_params": metrics_payload["gen_params"],
                "material_id": row.material_id,
                # 物料分桶信息随节点落盘：CTR 历史汇聚（FR-012）的读取落点
                "material_kind": metrics_payload["material"]["kind"],
                "material_tags": metrics_payload["material"]["tags"],
                "material_platform": metrics_payload["material"]["platform"],
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
            created_at=time.time(),
        )
        try:
            store.append_node(node)  # 一次性完整 INSERT，落盘即冻结
        except DuplicateError:
            report["skipped"] += 1  # 幂等：已落盘
        else:
            report["ingested"].append(row.material_id)
        with engine.begin() as conn:
            conn.execute(
                update(promo_campaigns)
                .where(promo_campaigns.c.campaign_id == row.campaign_id)
                .values(
                    status="ingested",
                    node_id=node.node_id,
                    metrics={**metrics_payload, "platform_metrics": _snapshot_to_dict(snapshot)},
                    updated_at=time.time(),
                )
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
