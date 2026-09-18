#!/usr/bin/env python
"""immutable 审计脚本（宪章门禁：每日定时，失败即告警）。

随机抽取 100 个历史节点（不足则全量），按其树冻结的 config_snapshot 中的权重
重算 score，与落盘值比对；任一不一致即非零退出，并向 stdout 打印 JSON 差异明细。

用法：
    uv run python ops/audit_immutable.py [--dsn DSN] [--sample 100] [--seed 42]

DSN 默认读环境变量 CINEFLOW_PG_DSN。比对核心（recompute_node_score / audit_samples）
为纯函数，由 tests/unit/test_audit.py 覆盖。
"""

import argparse
import json
import sys
from typing import Any

from core.evaluators.base import EvalResult
from core.evaluators.composite import composite_score
from core.tree.errors import ValidationError


def _weight_key_of(breakdown_key: str, weights: dict[str, float]) -> str | None:
    """把 breakdown 的版本化键（evaluator_id@version）对齐到 weights 键。"""
    if breakdown_key in weights:
        return breakdown_key
    base = breakdown_key.rsplit("@", 1)[0] if "@" in breakdown_key else breakdown_key
    return base if base in weights else None


def recompute_node_score(eval_breakdown: dict, config_snapshot: dict) -> float:
    """按冻结快照中的权重复算节点总分（纯函数）。

    复算语义（"快照权重 ∩ 明细"）：
    - 明细为空的锚点节点由 audit_samples 直接跳过（不进本函数）；
    - 缺片段节点（如拦截节点无 human 明细）按交集权重复算；
    - 明细中出现权重表外的评估器键、缺 score 明细、快照缺权重，仍一律报错
      ——审计宁可报错也不放行。
    """
    weights = config_snapshot.get("evaluator_weights")
    if not isinstance(weights, dict) or not weights:
        raise ValidationError("config_snapshot 缺少 evaluator_weights，无法复算 score")

    aligned: dict[str, EvalResult] = {}
    for key, diag in eval_breakdown.items():
        weight_key = _weight_key_of(key, weights)
        if weight_key is None:
            raise ValidationError(f"breakdown 键 {key!r} 在快照权重中无对应项")
        if not isinstance(diag, dict) or "score" not in diag:
            raise ValidationError(f"breakdown[{key!r}] 缺少 score 明细，无法复算")
        aligned[weight_key] = EvalResult(score=diag["score"])

    # 交集权重：缺片段节点按已有明细复算（gate 语义不受影响——rule.* 零分仍短路）
    effective_weights = {key: weights[key] for key in aligned}
    return composite_score(aligned, effective_weights)


def audit_sample(node_fields: dict[str, Any], config_snapshot: dict) -> dict[str, Any] | None:
    """比对单个节点（纯函数）：一致返回 None，不一致返回差异明细 dict。

    node_fields 为 TreeNode 的字段字典（dataclasses.asdict 形态）。
    """
    node_id = node_fields["node_id"]
    status = node_fields["status"]
    stored = node_fields["score"]

    # FAILED 节点：score 必须为 None（成本已入账不在本审计范围，由成本回归门禁覆盖）
    if status == "failed":
        if stored is None:
            return None
        return {"node_id": node_id, "reason": "FAILED 节点 score 必须为 None", "stored": stored}

    try:
        recomputed = recompute_node_score(node_fields["eval_breakdown"], config_snapshot)
    except ValidationError as exc:
        return {"node_id": node_id, "reason": f"复算失败：{exc}", "stored": stored}

    if abs(recomputed - stored) > 1e-9:
        return {
            "node_id": node_id,
            "reason": "score 与复算值不一致",
            "stored": stored,
            "recomputed": recomputed,
        }
    return None


def audit_samples(samples: list[tuple[dict[str, Any], dict]]) -> dict[str, Any]:
    """批量比对（纯函数）：samples 为 (节点字段字典, 该树 config_snapshot) 列表。

    锚点节点（eval_breakdown 为空，如轮次结构起点）无明细可复算：
    跳过并计入 skipped。
    """
    failures = []
    skipped = 0
    for node_fields, snapshot in samples:
        # 锚点节点（evaluated 且明细为空，如轮次结构起点）无明细可复算：
        # 跳过并计数；FAILED 节点明细虽空但仍须校验 score=None 不变量，不跳过
        if not node_fields["eval_breakdown"] and node_fields["status"] != "failed":
            skipped += 1
            continue
        finding = audit_sample(node_fields, snapshot)
        if finding is not None:
            failures.append(finding)
    return {
        "checked": len(samples),
        "ok": len(samples) - skipped - len(failures),
        "skipped": skipped,
        "failures": failures,
    }


def _load_samples(dsn: str, sample_size: int, seed: int) -> list[tuple[dict, dict]]:
    """从 PG 随机抽样：节点字段字典 + 所属树的 config_snapshot。"""
    from sqlalchemy import create_engine, func, select

    from core.tree.db import discovery_trees, tree_nodes

    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(tree_nodes)).scalar() or 0
            if total == 0:
                return []
            limit = min(sample_size, total)
            stmt = (
                select(tree_nodes, discovery_trees.c.config_snapshot)
                .join(discovery_trees, tree_nodes.c.tree_id == discovery_trees.c.tree_id)
                .order_by(func.random())  # PG 随机抽样；seed 仅用于可复现说明
                .limit(limit)
            )
            rows = conn.execute(stmt).all()
        return [
            (
                {column: row._mapping[column] for column in tree_nodes.c.keys()},
                row._mapping["config_snapshot"],
            )
            for row in rows
        ]
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="immutable 审计：随机抽历史节点复算 score 比对")
    parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    parser.add_argument("--sample", type=int, default=100, help="抽样节点数（默认 100）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（记录用）")
    args = parser.parse_args()

    import os

    dsn = args.dsn or os.environ.get("CINEFLOW_PG_DSN")
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    samples = _load_samples(dsn, args.sample, args.seed)
    report = audit_samples(samples)
    report["seed"] = args.seed
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
