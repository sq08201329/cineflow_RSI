"""PG 行为集成测试（US1 / T015，Docker PG）。

- jsonb 列读写保真（observation_context / eval_breakdown / cost / config_snapshot）；
- 三维索引过滤正确性；
- 3 万节点基准（真实分支形态，分支因子 10）：append p99 < 50ms、children p99 < 100ms
  （plan.md 性能目标）。children() 契约全量物化返回，病态宽树（单父节点数万子节点）
  的下限由行传输+反序列化决定（实测秒级），不构成目标形态，故不按该形态断言。
本地无 Docker 时整体跳过。
"""

import random
import time

import pytest

pytestmark = pytest.mark.integration

BENCH_NODES = 30_000
BRANCH_FACTOR = 10
APPEND_P99_MS = 50
CHILDREN_P99_MS = 100


def _p99_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(len(ordered) * 0.99))
    return (ordered[idx]) * 1000


class TestJsonb读写:
    def test_半结构化负载往返保真(self, pg_store, make_tree, make_node):
        snapshot = {
            "evaluator_weights": {"rule.x": "gate", "proxy.y": 0.5},
            "nested": {"list": [1, 2, {"k": "v"}], "null": None, "bool": True},
        }
        tree = make_tree(config_snapshot=snapshot, node_ids=["r1", "r2"])
        pg_store.create_tree(tree)
        ctx = {"unicode": "中文上下文", "float": 0.125, "list": [1, "a", None]}
        breakdown = {"rule.x@1.0.0": {"score": 1.0, "detail": {"ok": True}}}
        node = make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            observation_context=ctx,
            eval_breakdown=breakdown,
            score=1.0,
        )
        pg_store.append_node(node)

        got_tree = pg_store.trees_by(project_id=tree.project_id)[0]
        assert got_tree.config_snapshot == snapshot
        assert got_tree.node_ids == ["r1", "r2"]
        got_node = pg_store.get_node(node.node_id)
        assert got_node.observation_context == ctx
        assert got_node.eval_breakdown == breakdown


class Test三维索引过滤:
    def test_组合过滤仅返回全匹配(self, pg_store, make_tree, make_node):
        specs = [
            ("p1", "a1", "v1"),
            ("p1", "a1", "v2"),
            ("p1", "a2", "v1"),
            ("p2", "a1", "v1"),
        ]
        ids = {}
        for proj, agent, ver in specs:
            tree = make_tree(project_id=proj, agent_id=agent, policy_version=ver)
            pg_store.create_tree(tree)
            pg_store.append_node(make_node(node_id=tree.root_id, tree_id=tree.tree_id))
            ids[(proj, agent, ver)] = tree.tree_id

        hits = pg_store.trees_by(project_id="p1", agent_id="a1", policy_version="v1")
        assert [t.tree_id for t in hits] == [ids[("p1", "a1", "v1")]]
        assert len(pg_store.trees_by(project_id="p1")) == 3
        assert len(pg_store.trees_by(agent_id="a1")) == 3
        assert len(pg_store.trees_by(policy_version="v1")) == 3


class Test基准:
    def test_3万节点_append与children_p99(self, pg_store, make_tree, make_node):
        """单条线索 3 万节点（分支因子 10 的真实形态）：append p99 < 50ms、children p99 < 100ms。"""
        tree = make_tree()
        pg_store.create_tree(tree)
        root = make_node(node_id=tree.root_id, tree_id=tree.tree_id)
        pg_store.append_node(root)

        # 分支因子 10：第 i 个新节点挂在 all_nodes[i // 10] 下
        all_nodes = [root]
        append_samples: list[float] = []
        for i in range(BENCH_NODES - 1):
            parent = all_nodes[i // BRANCH_FACTOR]
            node = make_node(
                tree_id=tree.tree_id,
                parent_id=parent.node_id,
                depth=parent.depth + 1,
                created_at=1000.0 + i * 1e-6,
            )
            start = time.perf_counter()
            pg_store.append_node(node)
            append_samples.append(time.perf_counter() - start)
            all_nodes.append(node)

        # children 枚举：抽 50 个内部父节点（k ≤ (BENCH_NODES-2)//10 时恰有 10 个子节点）
        full_internal = all_nodes[: (BENCH_NODES - 2) // BRANCH_FACTOR]
        rng = random.Random(42)
        children_samples: list[float] = []
        for parent in rng.sample(full_internal, 50):
            start = time.perf_counter()
            result = pg_store.children(parent.node_id)
            children_samples.append(time.perf_counter() - start)
            assert len(result) == BRANCH_FACTOR

        append_p99 = _p99_ms(append_samples)
        children_p99 = _p99_ms(children_samples)
        assert append_p99 < APPEND_P99_MS, f"append p99 = {append_p99:.1f}ms"
        assert children_p99 < CHILDREN_P99_MS, f"children p99 = {children_p99:.1f}ms"
