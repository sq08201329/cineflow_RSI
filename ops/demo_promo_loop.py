#!/usr/bin/env python
"""端到端演示：宣发线上探索闭环（quickstart.md 验证 3）。

演示（确定性模拟平台 + Mock 网关，全程离线无需凭证）：
  1. 触发一轮探索（总预算 $500 → 上限 $10）：物料生成 → 合规门禁
     （含敏感词/规格拦截样例）→ 预算门禁（含超限拒投样例）→ 投放
  2. 成本对账（树内合计 + 待回流 == 网关 + 适配器账目）
  3. 同 round_id 二次触发验证幂等（零重复投放、零重复扣费）
  4. 指标回流 → 完整节点一次性落盘冻结
  5. 冻结入池 → 回放（零生成断言）
  6. 基线 vs 变体进化报告 JSON

生产环境切换：TreeStore DSN 换 PostgreSQL（ops/dev.compose.yml + alembic
迁移）、ArtifactStore 换 S3ArtifactStore（MinIO）、平台适配器换
HttpRealPlatform（PROMO_PLATFORM_* 凭证注入）、网关后端换 HttpBackend
（OPENAI_* 凭证注入）——代码路径不变，仅装配层替换。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from agents.promo.config import PromoConfig  # noqa: E402
from agents.promo.db import create_campaigns_schema  # noqa: E402
from agents.promo.loop import freeze_round_tree, run_round  # noqa: E402
from agents.promo.platform.simulated import SimulatedPlatform  # noqa: E402
from agents.promo.report import generate_evolution_report  # noqa: E402
from core.llm_gateway.backends.mock import MockBackend  # noqa: E402
from core.llm_gateway.gateway import LLMGateway  # noqa: E402
from core.replay.pool import SimulatorPool  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from ops.ingest_metrics import ingest_round  # noqa: E402
from policies.base import Budget  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
POLICY_DIR = REPO_ROOT / "policies" / "history" / "promo"


class DemoPolicy:
    """演示策略：4 个物料简报——正常两档 + 规格违规样例 + 超限申请样例。"""

    policy_version = "demo-policy-v1"

    def plan_materials(self, config):
        return [
            {
                "prompt": "写一句宣发文案（剧情向）",
                "gen_params": {"temperature": 0.3},
                "kind": "copy",
                "tags": ["剧情"],
                "budget_usd": 2.0,
            },
            {
                "prompt": "写一句宣发文案（悬疑向）",
                "gen_params": {"temperature": 0.7},
                "kind": "copy",
                "tags": ["悬疑"],
                "budget_usd": 2.0,
            },
            # 合规拦截样例：海报尺寸不符规格
            {
                "prompt": "写一句宣发文案（规格违规）",
                "gen_params": {"temperature": 0.5, "poster_size": "800x600"},
                "kind": "copy",
                "tags": ["剧情"],
                "budget_usd": 2.0,
            },
            # 预算门禁样例：已耗 ≈$4 后申请 $9.90 → 超过 $10 上限拒投
            {
                "prompt": "写一句宣发文案（超限申请）",
                "gen_params": {"temperature": 0.9},
                "kind": "copy",
                "tags": ["剧情"],
                "budget_usd": 9.9,
            },
        ]


def main() -> int:
    started = time.perf_counter()
    config = PromoConfig.from_yaml(MOVIE_YAML)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    create_campaigns_schema(engine)
    store = create_tree_store(engine)
    gateway = LLMGateway(MockBackend(), price_book=config.model_prices, sleep=lambda _: None)
    adapter = SimulatedPlatform(config.simulated_platform)

    report: dict = {"steps": {}}

    with tempfile.TemporaryDirectory(prefix="cineflow-promo-demo-") as tmp:
        artifacts = LocalArtifactStore(Path(tmp) / "artifacts")

        # 1) 一轮闭环
        first = run_round(
            "demo-round-1",
            DemoPolicy(),
            store,
            artifacts,
            adapter,
            gateway,
            config,
            engine=engine,
        )
        report["steps"]["round"] = first.to_dict()

        # 2) 幂等二次触发
        gateway_calls_before = gateway.call_count
        campaigns_before = adapter.campaign_count
        second = run_round(
            "demo-round-1",
            DemoPolicy(),
            store,
            artifacts,
            adapter,
            gateway,
            config,
            engine=engine,
        )
        report["steps"]["idempotency"] = {
            "duplicate": second.tree_id == first.tree_id,
            "no_new_gateway_calls": gateway.call_count == gateway_calls_before,
            "no_new_campaigns": adapter.campaign_count == campaigns_before,
            "spent_unchanged": second.spent_usd == first.spent_usd,
        }

        # 3) 指标回流 → 一次性落盘冻结
        ingest_report = ingest_round("demo-round-1", store, adapter, engine, config)
        report["steps"]["ingest"] = ingest_report

        # 4) 冻结入池 + 回放（零生成断言）
        tree = freeze_round_tree("demo-round-1", store, engine)
        pool = SimulatorPool(store)
        pool.add_tree(tree)
        simulator = pool.build(
            worker_count=config.simulated_platform and 4 or 4,
            budget=Budget(max_probes=4),
            latency_quantum_ms=0,
        )
        hit = simulator.probe(tree.root_id, {"temperature": 0.3})
        report["steps"]["replay"] = {
            "tree_frozen": True,
            "pool_accepted": True,
            "generation_calls": simulator.budget.max_generation_calls,
            "probe_hit": hit.status == "ok",
            "hit_score": hit.nodes[0].score if hit.status == "ok" else None,
        }

        # 5) 基线 vs 变体进化报告
        evolution = generate_evolution_report(
            store, [tree], sorted(POLICY_DIR.glob("*.py")), max_probes=6
        )
        report["steps"]["evolution"] = evolution.to_dict()

    elapsed = time.perf_counter() - started
    report["elapsed_seconds"] = round(elapsed, 3)
    report["elapsed_under_5min"] = elapsed < 300  # SC-002

    recon = first.cost_reconciliation
    idem = report["steps"]["idempotency"]
    report["ok"] = all(
        [
            recon["consistent"],
            idem["duplicate"] and idem["no_new_gateway_calls"] and idem["no_new_campaigns"],
            report["steps"]["replay"]["probe_hit"],
            report["steps"]["replay"]["generation_calls"] == 0,
            len(report["steps"]["evolution"]["variants"]) == 2,
            report["elapsed_under_5min"],
        ]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
