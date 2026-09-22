#!/usr/bin/env python
"""指标回流管道 CLI（T218；功能 015 收尾：库函数下沉到 `agents/promo/ingest.py`）。

对 delivered 运营记录：fetch_metrics → 校验（越界拒绝并告警，不写树）→
一次性构造完整 TreeNode（含 human.platform_metrics@1.0.0 明细与全部成本）
单次 INSERT 落盘——节点从诞生即终态，写入即冻结 → 运营表回填 ingested。

分层（宪章原则五单向依赖）：本文件只作**薄封装**（参数解析 + DSN/环境装配 + JSON 输出），
回流逻辑在业务侧 `agents/promo/ingest.py`（CLI 与编排共用同一实现，避免两套口径）。
CLI 用于 PG 生产库（--dsn 默认 CINEFLOW_PG_DSN）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from agents.promo.config import PromoConfig  # noqa: E402
from agents.promo.ingest import ingest_round  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="宣发指标回流管道：快照校验 → 节点一次性落盘冻结")
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    args = parser.parse_args()

    dsn = args.dsn or os.environ.get("CINEFLOW_PG_DSN")
    if not dsn:
        print(json.dumps({"error": "缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）"}, ensure_ascii=False))
        return 2

    from agents.promo.platform.http_real import HttpRealPlatform

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
