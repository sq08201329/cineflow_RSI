#!/usr/bin/env python
"""端到端演示：回放模拟器 + 沙箱策略执行（quickstart.md 验证 4）。

流程：SQLite 内存库造一棵小型冻结历史树 → 构建回放模拟器 →
沙箱容器内回放参考贪心策略（容器内只有 Policy 类源码，无模拟器对象）→
打印轨迹 JSON 报告（得分曲线、probe 数、有效串行轮、虚拟成本、
生成调用计数恒 0 的审计断言）。

本演示的树数据用 SQLite 内存库；生产环境切换只需：
- TreeStore DSN 换成 PostgreSQL（ops/dev.compose.yml 起库 + alembic 迁移）；
- 工件存储换 S3ArtifactStore 指向 MinIO；
- 沙箱后端经 select_backend() 装配（CI 上为 gVisor）。
回放语义、IPC 协议与隔离旗标完全一致。
"""

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from core.replay.simulator import ReplaySimulator  # noqa: E402
from core.sandbox.backends import select_backend  # noqa: E402
from core.sandbox.runner import RunLimits, RunStatus, run_policy  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from policies.base import Budget  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"

# 参考贪心策略（容器内独立运行形态：与 tests/stubs.py 的 ReferencePolicy
# 同一贪心逻辑，无类型注解依赖——容器内只有标准库）。
# 版本号 = 本源码 BLAKE3 前 12 位，由 run_policy 落盘 policies/history/。
REFERENCE_POLICY_SOURCE = """
class Policy:
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
"""


def build_demo_tree(store) -> tuple[DiscoveryTree, dict[str, str]]:
    """小型冻结历史树：root(0.4) ─┬─ t0.3 → 0.9 ── t0.3 → 0.95
    └─ t0.7 → 0.5（另挂一个 FAILED 节点）"""
    tree = DiscoveryTree(
        tree_id=new_id(),
        project_id="demo-replay",
        agent_id="agent-demo",
        policy_version="a1b2c3d4e5f6",
        root_id=new_id(),
        node_ids=[],
        config_snapshot={
            "evaluator_weights": {"rule.x": 0.0},
            "observation_fields": ["gen_params"],
        },
    )
    store.create_tree(tree)

    def append(parent_id, depth, gen_params, score, status, cost, created_at):
        node = TreeNode(
            node_id=tree.root_id if parent_id is None else new_id(),
            tree_id=tree.tree_id,
            parent_id=parent_id,
            depth=depth,
            agent_id="agent-demo",
            policy_version="a1b2c3d4e5f6",
            prompt="",
            observation_context={"gen_params": gen_params},
            artifact_hash="ab" * 32,
            eval_breakdown={} if status is NodeStatus.FAILED else {"rule.x@1": {"score": score}},
            score=score,
            cost=cost,
            status=status,
            created_at=created_at,
        )
        store.append_node(node)
        return node.node_id

    ids = {}
    ids["root"] = append(None, 0, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1), 1.0)
    ids["a"] = append(
        ids["root"],
        1,
        {"temperature": 0.3},
        0.9,
        NodeStatus.EVALUATED,
        CostRecord(llm_calls=2),
        2.0,
    )
    ids["b"] = append(
        ids["root"],
        1,
        {"temperature": 0.7},
        0.5,
        NodeStatus.EVALUATED,
        CostRecord(llm_calls=3),
        3.0,
    )
    ids["f"] = append(
        ids["root"], 1, {"temperature": 0.7}, None, NodeStatus.FAILED, CostRecord(llm_calls=1), 4.0
    )  # 同参同批：与 b 同次揭示
    ids["a1"] = append(
        ids["a"], 2, {"temperature": 0.3}, 0.95, NodeStatus.EVALUATED, CostRecord(llm_calls=4), 5.0
    )
    return tree, ids


def main() -> int:
    replay_config = yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8"))["replay"]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    store = create_tree_store(engine)
    tree, _ = build_demo_tree(store)

    simulator = ReplaySimulator.from_trees(
        [tree],
        store,
        worker_count=replay_config["worker_count"],
        budget=Budget(max_probes=6),
        latency_quantum_ms=0,  # 沙箱形态：填充统一在宿主 IPC 桥接层（RunLimits）
    )
    backend = select_backend()

    with tempfile.TemporaryDirectory(prefix="cineflow-demo-history-") as history_root:
        limits = RunLimits(latency_quantum_ms=10, wall_clock_seconds=90)
        result = run_policy(
            REFERENCE_POLICY_SOURCE, simulator, limits, backend, history_root=history_root
        )

    trajectory = result.trajectory
    # 审计断言（SC-003）：回放全程生成调用恒为 0
    generation_calls = simulator.budget.max_generation_calls
    assert generation_calls == 0

    report = {
        "backend": backend.name,
        "run_status": result.status.value,
        "generation_calls": generation_calls,
        "trajectory": trajectory.to_dict() if trajectory else None,
    }
    report["ok"] = (
        result.status is RunStatus.COMPLETED
        and generation_calls == 0
        and trajectory is not None
        and trajectory.probe_count > 0
        and trajectory.policy_version != ""
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
