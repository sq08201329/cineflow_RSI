#!/usr/bin/env python
"""周校准 CLI（功能 010）：round / intake 子命令。

- round：按 calibration 配置生成 top-k 盲评清单并落盘轮次（状态 open）；
- intake：读 JSON 条目文件（[{node_id, score, reviewer}]）逐条录入，
  非法/重复条目拒绝并计数，整批不中断；录入后轮次转 intake。

CLI 默认面向 PG 库（--dsn 或 CINEFLOW_PG_DSN）；库函数由单测以 SQLite 驱动。
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _resolve_dsn(args) -> str | None:
    import os

    return args.dsn or os.environ.get("CINEFLOW_PG_DSN")


def _cmd_round(args) -> int:
    from sqlalchemy import create_engine

    from core.calibration.config import CalibrationConfig
    from core.calibration.selection import build_blind_list
    from core.tree.store import create_tree_store

    dsn = _resolve_dsn(args)
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    config = CalibrationConfig.from_yaml(args.config)
    period_end = args.period_end or datetime.now(UTC).date().isoformat()
    period_start = (
        args.period_start
        or (datetime.now(UTC).date() - timedelta(days=config.period_days)).isoformat()
    )
    try:
        observation_match = json.loads(args.observation_match) if args.observation_match else None
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                {"error": f"--observation-match 必须为 JSON 对象：{exc}"}, ensure_ascii=False
            )
        )
        return 2

    engine = create_engine(dsn)
    round_ = build_blind_list(
        create_tree_store(engine),
        agent_id=args.agent,
        period_start=period_start,
        period_end=period_end,
        top_k=args.top_k or config.top_k,
        data_dir=args.data_dir,
        observation_match=observation_match,
    )
    print(
        json.dumps(
            {
                "round_id": round_.round_id,
                "agent_id": round_.agent_id,
                "period": [round_.period_start, round_.period_end],
                "selected": len(round_.node_ids),
                "top_k": round_.top_k,
                "note": round_.note,
                "status": round_.status.value,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_intake(args) -> int:
    from sqlalchemy import create_engine

    from core.calibration.anchors import intake_anchors

    dsn = _resolve_dsn(args)
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    entries = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        print(json.dumps({"error": "录入文件必须为条目数组"}, ensure_ascii=False))
        return 2

    engine = create_engine(dsn)
    rejections: list = []
    with engine.begin() as conn:
        accepted = intake_anchors(
            conn, args.round, entries, data_dir=args.data_dir, rejections=rejections
        )
    print(
        json.dumps(
            {
                "round_id": args.round,
                "accepted": accepted,
                "rejected": len(rejections),
                "rejections": rejections,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not rejections else 1


def _cmd_close(args) -> int:
    from sqlalchemy import create_engine

    from core.calibration.config import CalibrationConfig
    from core.calibration.rounds import close_round
    from core.tree.store import create_tree_store

    dsn = _resolve_dsn(args)
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    config = CalibrationConfig.from_yaml(args.config)
    engine = create_engine(dsn)
    with engine.connect() as conn:
        summary = close_round(
            create_tree_store(engine),
            conn,
            args.data_dir,
            round_id=args.round,
            config=config,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_report(args) -> int:
    from core.calibration.config import CalibrationConfig
    from core.calibration.report import build_report

    config = CalibrationConfig.from_yaml(args.config)
    report = build_report(args.data_dir, args.period, target=config.reliability_target)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _has_ledger_history(data_dir, agent_id: str, current_period: str) -> bool:
    """首轮判定：台账中是否存在本周期以外的记录（无 → 记基线不判超阈）。"""
    ledger_root = Path(data_dir) / "ledger" / agent_id
    if not ledger_root.is_dir():
        return False
    for ledger_file in ledger_root.glob("*.jsonl"):
        for line in ledger_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("period") != current_period:
                return True
    return False


def _cmd_propose(args) -> int:
    from sqlalchemy import create_engine

    from core.calibration.anchors import load_anchors
    from core.calibration.config import CalibrationConfig
    from core.calibration.pairing import pair_anchors
    from core.calibration.refit import gate_keys_of, maybe_propose
    from core.calibration.rounds import compute_bias_records, iso_week_label
    from core.calibration.selection import load_round
    from core.evaluators.weights import load_evaluator_weights
    from core.tree.store import create_tree_store

    dsn = _resolve_dsn(args)
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    config = CalibrationConfig.from_yaml(args.config)
    round_, _ = load_round(args.data_dir, args.round)
    engine = create_engine(dsn)
    with engine.connect() as conn:
        anchors = load_anchors(conn, args.round)
    pairs = pair_anchors(anchors, create_tree_store(engine), config.self_pairing_exclusions)
    period = iso_week_label(round_.period_end)
    bias_records = compute_bias_records(pairs, period, config)
    proposal = maybe_propose(
        agent_id=round_.agent_id,
        bias_records=bias_records,
        pairs=pairs,
        current_weights=load_evaluator_weights(args.config, round_.agent_id),
        cfg=config,
        has_history=_has_ledger_history(args.data_dir, round_.agent_id, period),
        data_dir=args.data_dir,
        fixed_keys=frozenset(gate_keys_of(args.config)),
    )
    if proposal is None:
        print(json.dumps({"round_id": args.round, "proposal": None}, ensure_ascii=False))
        return 0
    print(
        json.dumps(
            {
                "proposal_id": proposal.proposal_id,
                "agent_id": proposal.agent_id,
                "based_version": proposal.based_version,
                "candidate_weights": proposal.candidate_weights,
                "status": proposal.status.value,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_confirm(args) -> int:
    from core.calibration.refit import confirm_proposal
    from core.evaluators.registry import Registry

    try:
        new_version = confirm_proposal(
            args.data_dir,
            args.config,
            proposal_id=args.proposal,
            by=args.by,
            registry=Registry(),
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {"proposal_id": args.proposal, "status": "confirmed", "new_version": new_version},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_shelve(args) -> int:
    from core.calibration.refit import shelve_proposal

    shelved = shelve_proposal(args.data_dir, args.proposal, by=args.by)
    print(
        json.dumps(
            {"proposal_id": shelved.proposal_id, "status": shelved.status.value, "by": args.by},
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="外环周校准：盲评清单/录入/收口/信度报告")
    sub = parser.add_subparsers(dest="command", required=True)

    round_parser = sub.add_parser("round", help="生成 top-k 盲评清单并落盘轮次")
    round_parser.add_argument("--agent", required=True, help="Agent ID（promo 不盲评）")
    round_parser.add_argument("--period-start", default=None, help="ISO 日期，默认上一周期")
    round_parser.add_argument("--period-end", default=None, help="ISO 日期，默认今天")
    round_parser.add_argument("--top-k", type=int, default=None, help="默认取 calibration.top_k")
    round_parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    round_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    round_parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    round_parser.add_argument(
        "--observation-match",
        default=None,
        help='观测槽精确匹配过滤（JSON，如 {"stage": "outline"}——剧本线只盲评大纲阶段）',
    )
    round_parser.set_defaults(func=_cmd_round)

    intake_parser = sub.add_parser("intake", help="人评锚点录入（JSON 条目文件）")
    intake_parser.add_argument("--round", required=True, help="校准轮次 ID")
    intake_parser.add_argument(
        "--file", required=True, help="条目 JSON：[{node_id, score, reviewer}]"
    )
    intake_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    intake_parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    intake_parser.set_defaults(func=_cmd_intake)

    close_parser = sub.add_parser("close", help="收口轮次：配对→偏差→台账/快照/报告→closed")
    close_parser.add_argument("--round", required=True, help="校准轮次 ID")
    close_parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    close_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    close_parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    close_parser.set_defaults(func=_cmd_close)

    report_parser = sub.add_parser("report", help="按周期重建信度报告（读台账）")
    report_parser.add_argument("--period", required=True, help="周期标签（如 2026-W38）")
    report_parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    report_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    report_parser.set_defaults(func=_cmd_report)

    propose_parser = sub.add_parser("propose", help="依本轮偏差生成权重再拟合提案（超阈才产）")
    propose_parser.add_argument("--round", required=True, help="校准轮次 ID（须已 closed）")
    propose_parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    propose_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    propose_parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    propose_parser.set_defaults(func=_cmd_propose)

    confirm_parser = sub.add_parser("confirm", help="确认提案生效（人工两键之一，无编辑路径）")
    confirm_parser.add_argument("--proposal", required=True, help="提案 ID")
    confirm_parser.add_argument("--by", required=True, help="确认人")
    confirm_parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    confirm_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    confirm_parser.set_defaults(func=_cmd_confirm)

    shelve_parser = sub.add_parser("shelve", help="搁置提案（零变更）")
    shelve_parser.add_argument("--proposal", required=True, help="提案 ID")
    shelve_parser.add_argument("--by", required=True, help="操作人")
    shelve_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    shelve_parser.set_defaults(func=_cmd_shelve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
