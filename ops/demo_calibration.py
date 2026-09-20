#!/usr/bin/env python
"""端到端演示：外环周校准（quickstart.md 六步，里程碑验收线 SC-001/F5）。

流程（确定性夹具数据 + SQLite 文件库 + configs/movie.yaml 临时副本，不动仓库配置）：
  1. 盲评清单：visual 夹具树（周期内 6 节点）→ top-5 清单，递归断言零泄露（SC-002）；
  2. 人评录入：5 条合法 + 1 条同键重复 + 1 条越界 score → 入库 5、拒绝 2（整批不中断）；
  3. 平台真值锚点：promo 回流夹具 3 条 → platform_truth 锚点入库，重复采集幂等；
  4. 偏差与台账：基线轮（W36）+ 注入 +0.2 偏移轮（W38）→ mean_shift 断言；
     台账追加，注册中心 spec 集合不变（版本不变断言）；
  5. 信度报告：reports/2026-W38.json 四要素 + meets_target；
  6. 提案与生效：超阈 → pending → confirm → composite 新版本注册 + 配置副本
     定点改写保注释 + 历史节点 score/eval_breakdown 逐字节一致（SC-006/FR-008）。

生产切换：SQLite 换 PG（CINEFLOW_PG_DSN + alembic 迁移 0004）、夹具人评换
CLI 录入（ops/calibrate.py intake）、配置副本换真实 configs——代码路径不变。
"""

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, insert  # noqa: E402

from agents.promo.anchors import collect_platform_anchors  # noqa: E402
from agents.promo.config import PromoConfig  # noqa: E402
from agents.promo.db import create_campaigns_schema, promo_campaigns  # noqa: E402
from core.calibration.anchors import (  # noqa: E402
    intake_anchors,
    load_anchors,
)
from core.calibration.config import CalibrationConfig  # noqa: E402
from core.calibration.db import create_anchor_schema  # noqa: E402
from core.calibration.models import ProposalStatus  # noqa: E402
from core.calibration.pairing import pair_anchors  # noqa: E402
from core.calibration.refit import (  # noqa: E402
    confirm_proposal,
    gate_keys_of,
    load_proposal,
    maybe_propose,
)
from core.calibration.report import build_report  # noqa: E402
from core.calibration.rounds import close_round, compute_bias_records  # noqa: E402
from core.calibration.selection import build_blind_list, load_round  # noqa: E402
from core.evaluators.registry import Registry  # noqa: E402
from core.evaluators.weights import load_evaluator_weights  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402


def _assert(condition, message):
    if not condition:
        raise AssertionError(f"演示断言失败：{message}")


def _walk_keys(payload):
    keys = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= _walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            keys |= _walk_keys(item)
    return keys


def _breakdown(score: float) -> dict:
    """分量得分围绕 score 微散开（拟合有区分度，仍确定性）。"""
    return {
        "rule.format_compliance@1.0.0": {"score": 1.0},
        "proxy.aesthetic@1.0.0": {"score": score},
        "proxy.identity_consistency@1.0.0": {"score": min(1.0, score * 0.9)},
        "proxy.flicker@1.0.0": {"score": score},
        "judge.cinematic@1.0.0": {"score": min(1.0, score * 1.05)},
    }


def _seed_visual_tree(store, window_ts: float, scores: list[float]) -> None:
    root, tree_id = new_id(), new_id()
    store.create_tree(
        DiscoveryTree(
            tree_id=tree_id,
            project_id="calib-demo",
            agent_id="visual",
            policy_version="demo-v1",
            root_id=root,
            node_ids=[root],
            config_snapshot={"evaluator_weights": {}},
        )
    )
    for i, score in enumerate(scores):
        store.append_node(
            TreeNode(
                node_id=root if i == 0 else new_id(),
                tree_id=tree_id,
                parent_id=None if i == 0 else root,
                depth=0 if i == 0 else 1,
                agent_id="visual",
                policy_version="demo-v1",
                prompt="",
                observation_context={},
                artifact_hash="ab" * 32,
                eval_breakdown=_breakdown(score),
                score=score,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=window_ts + i,
            )
        )


def _seed_promo_backfill(engine, store) -> None:
    """promo 回流夹具：3 个真实落树节点（含 human.platform_metrics 分量）+ ingested 运营行。"""
    root, tree_id = new_id(), new_id()
    store.create_tree(
        DiscoveryTree(
            tree_id=tree_id,
            project_id="calib-demo",
            agent_id="promo",
            policy_version="demo-v1",
            root_id=root,
            node_ids=[root],
            config_snapshot={"evaluator_weights": {}},
        )
    )
    for i in range(3):
        node_id = root if i == 0 else new_id()
        store.append_node(
            TreeNode(
                node_id=node_id,
                tree_id=tree_id,
                parent_id=None if i == 0 else root,
                depth=0 if i == 0 else 1,
                agent_id="promo",
                policy_version="demo-v1",
                prompt="",
                observation_context={},
                artifact_hash=f"{i + 1:064x}",
                eval_breakdown={
                    "proxy.ctr_history@1.0.0": {"score": 0.4 + i * 0.05},
                    "human.platform_metrics@1.0.0": {"score": 0.35 + i * 0.05},
                },
                score=0.4 + i * 0.05,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=1000.0 + i,
            )
        )
        with engine.begin() as conn:
            conn.execute(
                insert(promo_campaigns).values(
                    campaign_id=f"camp-demo-{i}",
                    round_id="promo-demo",
                    material_id=f"mat-{i}",
                    node_id=node_id,
                    status="ingested",
                    spent_usd=1.0,
                    external_id=f"ext-{i}",
                    metrics={
                        "platform_metrics": {
                            "ctr": 0.05,
                            "completion_rate": 0.6,
                            "conversions": 12,
                            "impressions": 1000,
                            "clicks": 50,
                            "platform_timestamp": 1000.0 + i,
                            "data_version": "v1",
                        },
                        "material": {
                            "platform": "douyin",
                            "artifact_hash": f"{i + 1:064x}",
                            "kind": "poster",
                            "tags": [],
                        },
                    },
                    created_at=1000.0,
                    updated_at=1000.0 + i,
                )
            )


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="calib_demo_"))
    data_dir = workdir / "calibration"
    config_copy = workdir / "movie.yaml"
    shutil.copy(REPO_ROOT / "configs" / "movie.yaml", config_copy)

    engine = create_engine(f"sqlite+pysqlite:///{workdir}/demo.db")
    create_schema(engine)
    create_anchor_schema(engine)
    create_campaigns_schema(engine)
    store = create_tree_store(engine)
    calib_cfg = CalibrationConfig.from_yaml(config_copy)
    promo_cfg = PromoConfig.from_yaml(config_copy)

    # 基线轮 W36（2026-08-31..09-06）与偏移轮 W38（2026-09-14..09-20）的夹具树
    w36_ts = datetime(2026, 9, 2, tzinfo=UTC).timestamp()
    w38_ts = datetime(2026, 9, 16, tzinfo=UTC).timestamp()
    _seed_visual_tree(store, w36_ts, [0.40, 0.50, 0.60, 0.70, 0.80, 0.55])
    _seed_visual_tree(store, w38_ts, [0.30, 0.45, 0.55, 0.65, 0.75, 0.50])
    _seed_promo_backfill(engine, store)

    # ── 步骤 1：盲评清单（top-5 降序，SC-002 零泄露递归断言）
    round1 = build_blind_list(
        store,
        agent_id="visual",
        period_start="2026-08-31",
        period_end="2026-09-06",
        top_k=calib_cfg.top_k,
        data_dir=data_dir,
    )
    round1_payload = json.loads(
        (data_dir / "rounds" / "visual" / f"{round1.round_id}.json").read_text(encoding="utf-8")
    )
    _assert(len(round1.node_ids) == 5, "清单应为 top-5")
    for entry in round1_payload["blind_list"]:
        _assert(set(entry) == {"node_id", "artifact_hash", "round_id"}, "清单条目键白名单")
    _assert(
        not (_walk_keys(round1_payload["blind_list"]) & {"score", "eval_breakdown"}),
        "SC-002：清单零泄露",
    )
    print(
        json.dumps(
            {"step": 1, "round": round1.round_id, "blind_list": 5, "zero_leak": True},
            ensure_ascii=False,
        )
    )

    # ── 步骤 2：人评录入（5 合法 + 1 重复 + 1 越界 → 入库 5、拒绝 2）
    entries = [
        {"node_id": nid, "score": score, "reviewer": "demo-reviewer"}
        for nid, score in zip(round1.node_ids, [0.82, 0.72, 0.62, 0.52, 0.42], strict=True)
    ]
    entries.append(dict(entries[0]))  # 同键重复
    entries.append({"node_id": round1.node_ids[1], "score": 1.2, "reviewer": "demo-reviewer"})
    rejections: list = []
    with engine.begin() as conn:
        accepted = intake_anchors(
            conn, round1.round_id, entries, data_dir=data_dir, rejections=rejections
        )
    _assert(accepted == 5 and len(rejections) == 2, "录入应为 5 入库 2 拒绝")
    print(
        json.dumps(
            {"step": 2, "accepted": accepted, "rejected": [r["reason"] for r in rejections]},
            ensure_ascii=False,
        )
    )

    # 基线轮收口（小偏差 ≈ 自动分 + 0.02 量级，不判超阈的历史数据）
    with engine.connect() as conn:
        close_round(store, conn, data_dir, round_id=round1.round_id, config=calib_cfg)

    # ── 步骤 3：平台真值锚点（promo 回流 3 条入库，重复采集幂等）
    round2 = build_blind_list(
        store,
        agent_id="visual",
        period_start="2026-09-14",
        period_end="2026-09-20",
        top_k=calib_cfg.top_k,
        data_dir=data_dir,
    )
    with engine.begin() as conn:
        platform_anchors = collect_platform_anchors(
            engine, conn, round_id=round2.round_id, config=promo_cfg
        )
        repeat = collect_platform_anchors(engine, conn, round_id=round2.round_id, config=promo_cfg)
    _assert(len(platform_anchors) == 3 and repeat == [], "平台锚点 3 条入库且幂等")
    print(
        json.dumps(
            {
                "step": 3,
                "platform_truth_anchors": len(platform_anchors),
                "reviewer": platform_anchors[0].reviewer,
            },
            ensure_ascii=False,
        )
    )

    # 偏移轮录入（注入 +0.2 偏移；锚点 = 节点实际得分 + 0.2，按盲评清单逐节点取真值）
    biased = [
        {
            "node_id": entry["node_id"],
            "score": min(1.0, store.get_node(entry["node_id"]).score + 0.2),
            "reviewer": "demo-reviewer",
        }
        for entry in json.loads(
            (data_dir / "rounds" / "visual" / f"{round2.round_id}.json").read_text(encoding="utf-8")
        )["blind_list"]
    ]
    with engine.begin() as conn:
        intake_anchors(conn, round2.round_id, biased, data_dir=data_dir)

    # ── 步骤 4：偏差与台账（mean_shift ≈ 0.2；注册中心版本不变）
    registry = Registry()
    specs_before = sorted(s.key for s in registry.list_all())
    with engine.connect() as conn:
        summary2 = close_round(store, conn, data_dir, round_id=round2.round_id, config=calib_cfg)
    period = summary2["period"]
    ledger_line = json.loads(
        (data_dir / "ledger" / "visual" / "proxy.aesthetic.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    _assert(abs(ledger_line["mean_shift"] - 0.2) < 1e-6, "注入 +0.2 偏移应复现")
    _assert(ledger_line["period"] == period, "台账周期标签一致")
    _assert(sorted(s.key for s in registry.list_all()) == specs_before, "版本不变断言")
    print(
        json.dumps(
            {
                "step": 4,
                "period": period,
                "mean_shift": ledger_line["mean_shift"],
                "pearson_r": ledger_line["pearson_r"],
            },
            ensure_ascii=False,
        )
    )

    # ── 步骤 5：信度报告（四要素 + meets_target）
    report = build_report(data_dir, period, target=calib_cfg.reliability_target)
    _assert(set(report) == {"period", "agents", "target", "alerts"}, "报告 schema 四要素")
    entry = report["agents"]["visual"]["proxy.aesthetic@1.0.0"]
    _assert(entry["samples"] == 5 and entry["meets_target"] is True, "信度达标口径")
    print(
        json.dumps(
            {
                "step": 5,
                "report": f"reports/{period}.json",
                "meets_target": entry["meets_target"],
                "alerts": report["alerts"],
            },
            ensure_ascii=False,
        )
    )

    # ── 步骤 6：提案与生效（超阈 → pending → confirm → 新版本 + 定点改写 + 审计一致）
    with engine.connect() as conn:
        anchors2 = load_anchors(conn, round2.round_id)
    pairs2 = pair_anchors(anchors2, store, calib_cfg.self_pairing_exclusions)
    bias_records = compute_bias_records(pairs2, period, calib_cfg)
    proposal = maybe_propose(
        agent_id="visual",
        bias_records=bias_records,
        pairs=pairs2,
        current_weights=load_evaluator_weights(config_copy, "visual"),
        cfg=calib_cfg,
        has_history=True,
        data_dir=data_dir,  # W36 台账已在 → 非首轮
        fixed_keys=frozenset(gate_keys_of(config_copy)),
    )
    _assert(proposal is not None and proposal.status is ProposalStatus.PENDING, "超阈应产提案")

    # SC-006 前置快照：生效前历史节点
    _, blind2 = load_round(data_dir, round2.round_id)
    nodes_before = {
        e["node_id"]: (
            store.get_node(e["node_id"]).score,
            json.dumps(store.get_node(e["node_id"]).eval_breakdown, sort_keys=True),
        )
        for e in blind2
    }
    new_version = confirm_proposal(
        data_dir,
        config_copy,
        proposal_id=proposal.proposal_id,
        by="demo-ops",
        registry=registry,
    )
    confirmed = load_proposal(data_dir, proposal.proposal_id)
    rewritten = config_copy.read_text(encoding="utf-8")
    _assert(confirmed.status is ProposalStatus.CONFIRMED, "提案应 confirmed")
    _assert(confirmed.confirmed_by == "demo-ops", "确认人落盘")
    _assert(new_version.startswith("1.0.0+w"), "新版本号 = base+w{哈希}")
    _assert("    rule.format_compliance: gate\n" in rewritten, "gate 行不被改写")
    _assert("# gate 表示硬规则门禁" in rewritten, "注释保留")
    for node_id, (score, breakdown) in nodes_before.items():
        node = store.get_node(node_id)
        _assert(node.score == score, "SC-006：历史节点 score 不变")
        _assert(
            json.dumps(node.eval_breakdown, sort_keys=True) == breakdown,
            "SC-006：历史节点 eval_breakdown 逐字节一致",
        )
    print(
        json.dumps(
            {
                "step": 6,
                "proposal": proposal.proposal_id,
                "status": "confirmed",
                "new_version": new_version,
            },
            ensure_ascii=False,
        )
    )

    print(
        json.dumps(
            {"demo": "weekly-calibration", "verdict": "PASS", "workdir": str(workdir)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
