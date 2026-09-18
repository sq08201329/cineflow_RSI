#!/usr/bin/env python
"""指标回流管道（T218，research 决策 1：两段式落盘的第二段）。

对 delivered 运营记录：fetch_metrics → 校验（越界拒绝并告警，不写树）→
一次性构造完整 TreeNode（含 human.platform_metrics@1.0.0 明细与全部成本）
单次 INSERT 落盘——节点从诞生即终态，写入即冻结 → 运营表回填 ingested。

幂等：重复执行只处理 delivered 行；节点撞主键视为已落盘跳过。
CLI 用于 PG 生产库（--dsn 默认 CINEFLOW_PG_DSN）；库函数 ingest_round
由单测以 SQLite 直接驱动。
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from agents.promo.config import PromoConfig  # noqa: E402
from agents.promo.db import promo_campaigns  # noqa: E402
from agents.promo.evaluators.platform_metrics import (  # noqa: E402
    EVALUATOR_ID as METRICS_EVALUATOR_ID,
)
from agents.promo.evaluators.platform_metrics import PlatformMetricsEvaluator  # noqa: E402
from agents.promo.loop import _round_root_id, round_tree_id  # noqa: E402
from agents.promo.platform.base import MetricSnapshot, validate_metrics  # noqa: E402
from core.evaluators.base import ArtifactRef, EvalResult  # noqa: E402
from core.evaluators.composite import composite_score_versioned  # noqa: E402
from core.evaluators.errors import ValidationError  # noqa: E402
from core.tree.errors import DuplicateError  # noqa: E402
from core.tree.models import CostRecord, NodeStatus, TreeNode  # noqa: E402
from core.tree.store import TreeStore  # noqa: E402


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

    report = {"round_id": round_id, "ingested": [], "rejected": [], "skipped": 0}
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


def main() -> int:
    parser = argparse.ArgumentParser(description="宣发指标回流管道：快照校验 → 节点一次性落盘冻结")
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    args = parser.parse_args()

    import os

    dsn = args.dsn or os.environ.get("CINEFLOW_PG_DSN")
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    from sqlalchemy import create_engine

    from agents.promo.platform.http_real import HttpRealPlatform
    from core.tree.store import create_tree_store

    engine = create_engine(dsn)
    report = ingest_round(
        args.round_id,
        create_tree_store(engine),
        HttpRealPlatform.from_env(),  # 生产真实渠道；模拟平台为进程内形态不走 CLI
        engine,
        PromoConfig.from_yaml(args.config),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["rejected"] else 1


if __name__ == "__main__":
    sys.exit(main())
