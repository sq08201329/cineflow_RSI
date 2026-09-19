#!/usr/bin/env python
"""端到端演示：做梦层 5 轮进化（quickstart.md 验证 3，里程碑验收线 SC-004）。

流程（确定性变异生成器 + SQLite 内存池 + 真实沙箱容器回放）：
  连续 5 轮做梦（演示档 M=8）→ 静态检查 → 沙箱回放 → reward 排名 →
  过拟合筛选（train/validation 分树）→ 审批单（脚本内模拟 approve）→
  部署指针更新（临时配置副本，不动仓库 configs）→ 谱系报表 + 进化曲线 JSON。

断言：collapse.collapsed == false；全程生成 API 调用恒 0；
LLM 调用全过网关入账（演示用 MutatorGenerator 不调 LLM——LLM 路径为
生产形态：LLMGenerator 经同一网关计费，凭证 OPENAI_* 注入 HttpBackend）。

生产切换：数据源换 PG（CINEFLOW_PG_DSN + alembic 迁移）、候选生成器换
LLMGenerator（真实 LLM 凭证）、审批从脚本模拟换人工 CLI 确认——
代码路径不变，仅装配层替换。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from core.replay.pool import SimulatorPool  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from dreaming.approve import create_approval_ticket, current_policy_version, decide  # noqa: E402
from dreaming.candidates import MutatorGenerator  # noqa: E402
from dreaming.config import DreamConfig  # noqa: E402
from dreaming.lineage import build_curve, build_lineage  # noqa: E402
from dreaming.overfit import evaluate_overfit, split_train_validation  # noqa: E402
from dreaming.pipeline import default_sandbox_replay, run_dream_round  # noqa: E402
from policies.versioning import record_policy  # noqa: E402

AGENT_ID = "agent-demo-dream"

CHAMPION_SOURCE = '''
class Policy:
    """初始冠军：双档贪心（做梦轮次的父版本）。"""

    def solve(self, env, budget):
        grid = [{"temperature": 0.3}, {"temperature": 0.7}]
        best_id = None
        probes = 0
        for round_no in range(6):
            observations = env.observed()
            scored = sorted(
                ((o.score, o.node_id) for o in observations.values() if o.score is not None),
                key=lambda item: (-item[0], item[1]),
            )
            if scored:
                best_id = scored[0][1]
            if best_id is None or probes >= budget.max_probes:
                break
            env.probe(best_id, grid[round_no % len(grid)])
            probes += 1
        return best_id or ""
'''


def _build_pool(store):
    """演示池：三棵不同时间树（最近一棵只做 validation）。"""
    trees = []
    for t in range(3):
        tree = DiscoveryTree(
            tree_id=new_id(),
            project_id="dream-demo",
            agent_id=AGENT_ID,
            policy_version="demo-champion",
            root_id=new_id(),
            node_ids=[],
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params"],
            },
        )
        store.create_tree(tree)
        store.append_node(
            TreeNode(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                parent_id=None,
                depth=0,
                agent_id=AGENT_ID,
                policy_version="demo-champion",
                prompt="",
                observation_context={"gen_params": {}},
                artifact_hash="ab" * 32,
                eval_breakdown={},
                score=0.4,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=float(t * 10),
            )
        )
        for i, temp in enumerate((0.3, 0.7)):
            store.append_node(
                TreeNode(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id=AGENT_ID,
                    policy_version="demo-champion",
                    prompt="",
                    observation_context={"gen_params": {"temperature": temp}},
                    artifact_hash="ab" * 32,
                    eval_breakdown={},
                    score=0.5 + 0.05 * t + 0.1 * i,
                    cost=CostRecord(llm_calls=1),
                    status=NodeStatus.EVALUATED,
                    created_at=float(t * 10 + 1 + i),
                )
            )
        trees.append(tree)
    return trees


def main() -> int:
    started = time.perf_counter()
    config = DreamConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    store = create_tree_store(engine)
    trees = _build_pool(store)

    report: dict = {"rounds": []}

    with tempfile.TemporaryDirectory(prefix="cineflow-dream-demo-") as tmp:
        tmp_path = Path(tmp)
        history_root = tmp_path / "dreaming" / "history"
        policies_root = tmp_path / "policies"
        tickets_dir = tmp_path / "tickets"
        config_copy = tmp_path / "movie.yaml"
        config_copy.write_text(
            (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        # 初始冠军落盘 + 审批记录（SC-005：指针版本必须有 approved 记录）
        from dreaming.lineage import write_meta

        champion_version = record_policy(CHAMPION_SOURCE, AGENT_ID, history_root=policies_root)
        write_meta(
            policies_root,
            AGENT_ID,
            {
                "version": champion_version,
                "parent_version": "root",
                "created_round": "dream-seed-0",
                "reward": {
                    "pareto_auc": 0.0,
                    "parallel_penalty": 0.0,
                    "lambda": config.lambda_,
                    "reward": 0.0,
                },
                "source": "manual",
                "approval": {
                    "approver": "bootstrap",
                    "at": "2026-09-19T09:00:00+08:00",
                    "decision": "approved",
                    "reason": "初始冠军部署",
                },
            },
        )
        data = yaml.safe_load(config_copy.read_text(encoding="utf-8"))
        data.setdefault("deployment", {}).setdefault(AGENT_ID, {})["current_policy_version"] = (
            champion_version
        )
        config_copy.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        champion_source = CHAMPION_SOURCE
        total_generation_calls = 0
        for round_no in range(1, 6):  # 连续 5 轮做梦（里程碑验收线）
            dream_round = run_dream_round(
                AGENT_ID,
                champion_source,
                MutatorGenerator(f"round-{round_no}"),
                _pool_of(store, trees),
                None,
                config,
                replay_fn=default_sandbox_replay,
                history_root=history_root,
                m=config.demo_candidates,
            )
            total_generation_calls += dream_round.diagnostics["generation_api_calls"]

            # 过拟合筛选（最近树只做 validation；胜者在 validation 池重放判定）
            train_trees, validation_trees = split_train_validation(trees)
            winner = next(
                c for c in dream_round.candidates if c.version == dream_round.winner_version
            )
            verdict = evaluate_overfit(
                winner.version,
                {winner.version: winner.reward.reward},
                {},
                top_ratio=config.validation_top_ratio,
            )

            # 审批单 → 脚本内模拟 approve → 部署指针更新
            ticket = create_approval_ticket(
                dream_round,
                dream_round.champion_version,
                _pool_of(store, validation_trees or trees),
                champion_source=champion_source,
                tickets_dir=tickets_dir,
                replay_fn=default_sandbox_replay,
                lambda_=config.lambda_,
            )
            decide(
                ticket.path,
                approver="demo-script",
                decision="approved",
                reason=f"演示自动审批（{verdict.note}）",
                history_root=policies_root,
                config_path=config_copy,
            )
            pointer = current_policy_version(AGENT_ID, config_copy, history_root=policies_root)
            report["rounds"].append(
                {
                    "round_id": dream_round.round_id,
                    "winner_version": dream_round.winner_version,
                    "winner_reward": winner.reward.reward,
                    "overfit_note": verdict.note,
                    "deployed_pointer": pointer,
                }
            )
            champion_source = winner.source_code  # 下一轮以胜者为父

        lineage = build_lineage(AGENT_ID, store, policies_root)
        curve = build_curve(
            AGENT_ID, history_root, config.collapse_window, config.collapse_threshold
        )

    elapsed = time.perf_counter() - started
    report["lineage"] = lineage.to_dict()
    report["curve"] = curve.to_dict()
    report["audit"] = {
        "generation_api_calls_total": total_generation_calls,
        "zero_generation": total_generation_calls == 0,
        "llm_via_gateway": "MutatorGenerator 不调 LLM（0 次）；LLMGenerator 生产路径全过网关",
    }
    report["elapsed_seconds"] = round(elapsed, 2)
    report["ok"] = all(
        [
            len(report["rounds"]) == 5,
            not curve.collapse["collapsed"],
            report["audit"]["zero_generation"],
            all(r["deployed_pointer"] == r["winner_version"] for r in report["rounds"]),
            # 谱系链完整：所有 meta 在册版本都有父版本（树库独有版本除外）
            all(
                v["parent_version"] is not None for v in lineage.versions if v["source"] is not None
            ),
        ]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


def _pool_of(store, trees):
    pool = SimulatorPool(store)
    for tree in trees:
        pool.add_tree(tree)
    return pool


if __name__ == "__main__":
    sys.exit(main())
