#!/usr/bin/env python
"""端到端演示：视觉线上探索闭环（quickstart.md 验证 3）。

演示（确定性模拟生成器 + Mock 网关，全程离线无需凭证）：
  1. 触发一轮探索（3 个候选片段：合规 / 分辨率违规 / 预算超限样例；
     演示用小预算上限 $1.2 以展示门禁，生产为 configs 的 $500）
  2. 成本对账（树内合计 == 运营表扣减 + 网关 judge 账目）
  3. 同 round_id 二次触发验证幂等（零重复生成、零重复扣费）
  4. 冻结入池 → 回放（零生成断言）
  5. 一致性验收报告 JSON（首轮 tau=null 分档）

生产环境切换：TreeStore DSN 换 PostgreSQL（ops/dev.compose.yml + alembic
迁移）、ArtifactStore 换 S3ArtifactStore（MinIO）、生成适配器换
HttpRealVideoGen（VISUAL_GEN_* 凭证注入）、网关后端换 HttpBackend
（OPENAI_* 凭证注入）——代码路径不变，仅装配层替换。
"""

import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from agents.visual.config import VisualConfig  # noqa: E402
from agents.visual.consistency import verify_consistency  # noqa: E402
from agents.visual.db import create_gen_jobs_schema  # noqa: E402
from agents.visual.loop import build_evaluators, freeze_round_tree, run_round  # noqa: E402
from agents.visual.platform.simulated import SimulatedVideoGen  # noqa: E402
from core.evaluators.registry import Registry  # noqa: E402
from core.llm_gateway.backends.mock import MockBackend  # noqa: E402
from core.llm_gateway.gateway import LLMGateway  # noqa: E402
from core.replay.pool import SimulatorPool  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from policies.base import Budget  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"


class DemoVisualPolicy:
    """演示策略：3 个候选片段——合规 / 分辨率违规（门禁拦截）/ 预算超限。"""

    policy_version = "demo-visual-v1"

    def plan_clips(self, config):
        return [
            {"gen_params": {"style": "史诗", "shots": 2, "seed_tier": 1}},
            # 合规拦截样例：分辨率不符 clip_spec（生成成本照入账，合成 0 分）
            {
                "gen_params": {
                    "style": "纪实",
                    "shots": 1,
                    "seed_tier": 2,
                    "width": 640,
                    "height": 480,
                }
            },
            # 预算门禁样例：已耗 $1.0 后申请 $0.6 超 $1.2 上限 → 拒投
            {"gen_params": {"style": "文艺", "shots": 2, "seed_tier": 3}},
        ]


def main() -> int:
    started = time.perf_counter()
    # 演示用小预算上限展示门禁（生产读 configs 的 exploration_per_round_usd=500）
    config = replace(VisualConfig.from_yaml(MOVIE_YAML), exploration_per_round_usd=1.2)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    create_gen_jobs_schema(engine)
    store = create_tree_store(engine)
    gateway = LLMGateway(
        MockBackend(),
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
    )
    adapter = SimulatedVideoGen(config.simulated_gen)

    report: dict = {"steps": {}, "demo_budget_cap_usd": 1.2}

    with tempfile.TemporaryDirectory(prefix="cineflow-visual-demo-") as tmp:
        artifacts = LocalArtifactStore(Path(tmp) / "artifacts")

        # 1) 一轮闭环（3 候选片段）
        first = run_round(
            "demo-v-round", DemoVisualPolicy(), store, artifacts, adapter, gateway, engine, config
        )
        report["steps"]["round"] = first.to_dict()

        # 2) 幂等二次触发
        gateway_calls = gateway.call_count
        jobs_before = adapter.job_count
        second = run_round(
            "demo-v-round", DemoVisualPolicy(), store, artifacts, adapter, gateway, engine, config
        )
        report["steps"]["idempotency"] = {
            "duplicate": second.tree_id == first.tree_id,
            "no_new_generation": adapter.job_count == jobs_before,
            "no_new_gateway_calls": gateway.call_count == gateway_calls,
            "spent_unchanged": second.spent_usd == first.spent_usd,
        }

        # 3) 冻结入池 + 回放（零生成断言）
        tree = freeze_round_tree("demo-v-round", store, engine)
        pool = SimulatorPool(store)
        pool.add_tree(tree)
        simulator = pool.build(worker_count=4, budget=Budget(max_probes=4), latency_quantum_ms=0)
        hit = simulator.probe(tree.root_id, {"style": "史诗", "shots": 2, "seed_tier": 1})
        report["steps"]["replay"] = {
            "pool_accepted": True,
            "generation_calls": simulator.budget.max_generation_calls,
            "probe_hit": hit.status == "ok",
            "hit_score_frozen": hit.nodes[0].score if hit.status == "ok" else None,
        }

        # 4) 一致性验收（首轮：池内仅一棵树 → tau=null 分档）
        registry = Registry()
        for evaluator in build_evaluators(config, gateway, artifacts)["all"]:
            registry.register(evaluator)
        consistency = verify_consistency(tree.tree_id, store, artifacts, registry)
        report["steps"]["consistency"] = consistency.to_dict()

    elapsed = time.perf_counter() - started
    report["elapsed_seconds"] = round(elapsed, 3)
    report["elapsed_under_5min"] = elapsed < 300  # SC 耗时断言入报告

    round_step = report["steps"]["round"]
    idem = report["steps"]["idempotency"]
    statuses = [c["status"] for c in round_step["clips"]]
    report["ok"] = all(
        [
            statuses == ["ingested", "ingested", "rejected"],  # 合规违规片已评估0分落盘
            round_step["cost_reconciliation"]["consistent"],
            idem["duplicate"] and idem["no_new_generation"] and idem["no_new_gateway_calls"],
            report["steps"]["replay"]["probe_hit"]
            and report["steps"]["replay"]["generation_calls"] == 0,
            consistency.verdict == "pass" and consistency.consistent_rate == 1.0,
            report["elapsed_under_5min"],
        ]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
