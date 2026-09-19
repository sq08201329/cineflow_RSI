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
from datetime import datetime, timedelta, timezone
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
    period_end = args.period_end or datetime.now(timezone.utc).date().isoformat()
    period_start = args.period_start or (
        datetime.now(timezone.utc).date() - timedelta(days=config.period_days)
    ).isoformat()

    engine = create_engine(dsn)
    round_ = build_blind_list(
        create_tree_store(engine),
        agent_id=args.agent,
        period_start=period_start,
        period_end=period_end,
        top_k=args.top_k or config.top_k,
        data_dir=args.data_dir,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="外环周校准：盲评清单生成与人评录入")
    sub = parser.add_subparsers(dest="command", required=True)

    round_parser = sub.add_parser("round", help="生成 top-k 盲评清单并落盘轮次")
    round_parser.add_argument("--agent", required=True, help="Agent ID（promo 不盲评）")
    round_parser.add_argument("--period-start", default=None, help="ISO 日期，默认上一周期")
    round_parser.add_argument("--period-end", default=None, help="ISO 日期，默认今天")
    round_parser.add_argument("--top-k", type=int, default=None, help="默认取 calibration.top_k")
    round_parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    round_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    round_parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    round_parser.set_defaults(func=_cmd_round)

    intake_parser = sub.add_parser("intake", help="人评锚点录入（JSON 条目文件）")
    intake_parser.add_argument("--round", required=True, help="校准轮次 ID")
    intake_parser.add_argument("--file", required=True, help="条目 JSON：[{node_id, score, reviewer}]")
    intake_parser.add_argument("--data-dir", default=str(REPO_ROOT / "calibration"))
    intake_parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    intake_parser.set_defaults(func=_cmd_intake)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
