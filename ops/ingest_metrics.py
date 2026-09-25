#!/usr/bin/env python
"""指标回流管道 CLI（T218；功能 015 收尾：库函数下沉；功能 020：薄参数 + 日级/覆盖）。

对 delivered / ingested 运营记录：fetch_metrics → 校验（越界或**归属日缺失**即拒绝并告警，
不写树）→ 一次性构造完整 TreeNode（含 human.platform_metrics@1.0.0 明细与全部成本）
单次 INSERT 落盘——节点从诞生即终态，写入即冻结 → 运营表回填 ingested。

分层（宪章原则五单向依赖）：本文件只作**薄封装**（参数解析 + DSN/环境装配 + JSON 输出），
回流与覆盖判定全在业务侧 `agents/promo/{ingest,daily}.py`（CLI **零判定逻辑**，
不重算 `meets` / `max_gap_days`）。CLI 用于 PG 生产库（--dsn 默认 CINEFLOW_PG_DSN）。

## 功能 020 的新增参数（薄参数，不承载第二份口径）

```
--source {real,simulated}   必填：real ⇒ HttpRealPlatform.from_env()
                            （缺凭证 ⇒ 装配期显式拒绝、退出 2、零落盘零扣费）
                            simulated ⇒ SimulatedPlatform（仅离线/演示）
--daily                     走按采集日分片路径（agents/promo/daily.py 的 ingest_daily）
--metric-date ISO 日期      显式声明的归属日（离线跨日演练；**不派生**、不猜测）
--coverage                  只做日级覆盖检查（派生视图、**不落盘**）：meets False ⇒ 退出 1
--end ISO 日期              覆盖窗口右端（缺省 = 今天，UTC）
--min-window-days N         覆盖下限（缺省取自 `budget.runs.min_window_days`，**不取码内默认**）
--gap-tolerance-days N      断档容差（缺省取自 `budget.runs.gap_tolerance_days`）
```

退出码（与既有工具一致）：`0` 成功/达标｜`1` 如实报未达标或有条目被拒｜`2` 用法或配置错误。
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from agents.promo.config import PromoConfig  # noqa: E402
from agents.promo.ingest import ingest_round  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402

_EXIT_OK = 0
_EXIT_REJECTED = 1
_EXIT_USAGE = 2


def _emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def _error(message: str) -> int:
    return _emit({"error": message}, _EXIT_USAGE)


def _resolve_dsn(args) -> str | None:
    return args.dsn or os.environ.get("CINEFLOW_PG_DSN")


def runs_thresholds(config_path: str) -> tuple[int, int]:
    """`budget.runs.{min_window_days, gap_tolerance_days}`：缺项即报错（不取码内默认）。"""
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    runs = (data or {}).get("budget", {}).get("runs")
    if not isinstance(runs, dict):
        raise ValueError(f"形态配置缺少 budget.runs 段：{config_path}")
    for key in ("min_window_days", "gap_tolerance_days"):
        if key not in runs:
            raise ValueError(f"budget.runs 缺少配置项 {key!r}（覆盖判定的下限/容差必须声明）")
    return int(runs["min_window_days"]), int(runs["gap_tolerance_days"])


def _adapter(args, config: PromoConfig):
    """装配面：来源由 `--source` **显式声明**（不按跑没跑通推断、不静默回落）。"""
    from agents.promo.platform.simulated import SimulatedPlatform

    if args.source == "simulated":
        return SimulatedPlatform(config.simulated_platform, metric_date=args.metric_date)
    from agents.promo.platform.http_real import HttpRealPlatform

    return HttpRealPlatform.from_env(config.simulated_platform)  # 缺凭证 ⇒ UnavailableError


def _coverage(args, engine, config: PromoConfig) -> int:
    """覆盖检查（派生视图、**不落盘**）：判定全在 `agents/promo/daily.py`。"""
    from agents.promo.daily import daily_coverage

    min_days, gap_days = args.min_window_days, args.gap_tolerance_days
    if min_days is None or gap_days is None:
        # 阈值缺省取自 `budget.runs.*`（**不取码内默认**）；缺项即报错并退出 2
        try:
            cfg_min, cfg_gap = runs_thresholds(args.config)
        except ValueError as exc:
            return _error(f"覆盖判定的下限/容差未声明：{exc}")
        min_days = cfg_min if min_days is None else min_days
        gap_days = cfg_gap if gap_days is None else gap_days

    end = args.end or datetime.now(UTC).date().isoformat()
    try:
        coverage = daily_coverage(
            engine,
            end=end,
            min_window_days=min_days,
            gap_tolerance_days=gap_days,
            period_days=config.period_days,
            required_since=config.attribution_date_required_since,
        )
    except Exception as exc:  # noqa: BLE001 - CLI 边界：参数/配置错误如实收口为退出 2
        return _error(f"覆盖检查失败：{exc}")
    return _emit(coverage, _EXIT_OK if coverage["meets"] else _EXIT_REJECTED)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="宣发指标回流管道：快照校验 → 节点一次性落盘冻结（可选日级分片与覆盖检查）"
    )
    parser.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    parser.add_argument(
        "--source",
        required=True,
        choices=["real", "simulated"],
        help="回流来源（**必填**，由装配面显式声明）：real 走真实适配器、simulated 仅离线/演示",
    )
    parser.add_argument("--round-id", default=None, help="投放轮标识（回流路径必填）")
    parser.add_argument(
        "--daily",
        action="store_true",
        help="按采集日分片回流（agents/promo/daily.py 的 ingest_daily）",
    )
    parser.add_argument(
        "--metric-date", default=None, help="显式声明的归属日（ISO YYYY-MM-DD；离线跨日演练）"
    )
    parser.add_argument("--coverage", action="store_true", help="只做日级覆盖检查（不落盘）")
    parser.add_argument("--end", default=None, help="覆盖窗口右端（ISO 日期；缺省 = 今天 UTC）")
    parser.add_argument("--min-window-days", type=int, default=None)
    parser.add_argument("--gap-tolerance-days", type=int, default=None)
    args = parser.parse_args()

    dsn = _resolve_dsn(args)
    if not dsn:
        return _error("缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）")

    try:
        config = PromoConfig.from_yaml(args.config)
    except Exception as exc:  # noqa: BLE001 - 配置错误 ⇒ 退出 2（不启动、零落盘）
        return _error(f"配置读取失败：{exc}")

    engine = create_engine(dsn)

    if args.coverage:
        return _coverage(args, engine, config)

    if not args.round_id:
        return _error("回流路径需要 --round-id（或用 --coverage 只做覆盖检查）")

    try:
        adapter = _adapter(args, config)
    except Exception as exc:  # noqa: BLE001 - 缺凭证等装配错误 ⇒ 退出 2、零落盘零扣费
        return _error(f"装配期拒绝启动：{exc}")

    if args.daily:
        from agents.promo.daily import ingest_daily

        try:
            report = ingest_daily(
                args.round_id,
                create_tree_store(engine),
                adapter,
                engine,
                config,
                source=args.source,
                metric_date=args.metric_date,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界：执行失败如实报错并退出 1
            return _emit({"error": f"日级回流执行失败：{exc}"}, _EXIT_REJECTED)
    else:
        report = ingest_round(args.round_id, create_tree_store(engine), adapter, engine, config)
    return _emit(report, _EXIT_OK if not report["rejected"] else _EXIT_REJECTED)


if __name__ == "__main__":
    sys.exit(main())
