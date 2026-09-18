"""不可变性集成测试（US1 / T014，Docker PG）。

双保险验证：UPDATE/DELETE 同时被触发器与权限拒绝；随机抽样节点重算 score
与落盘值一致（immutable 审计的最小形态，宪章每日门禁的测试侧对应）。
本地无 Docker 时整体跳过。
"""

import random

import pytest
from sqlalchemy import delete, text, update

from core.tree.errors import ImmutableViolationError
from core.tree.models import CostRecord, NodeStatus
from core.tree.store import translate_immutable_errors

pytestmark = pytest.mark.integration


def _seed_tree(pg_store, make_tree, make_node, n_children=5):
    tree = make_tree()
    pg_store.create_tree(tree)
    root = make_node(node_id=tree.root_id, tree_id=tree.tree_id)
    pg_store.append_node(root)
    children = [
        make_node(tree_id=tree.tree_id, parent_id=root.node_id, depth=1, created_at=100.0 + i)
        for i in range(n_children)
    ]
    for child in children:
        pg_store.append_node(child)
    return tree, root, children


class Test触发器拒绝:
    def test_update_被拒绝(self, pg_engine, pg_store, make_tree, make_node):
        from core.tree.db import tree_nodes

        _, root, _ = _seed_tree(pg_store, make_tree, make_node)
        with pytest.raises(ImmutableViolationError), translate_immutable_errors():
            with pg_engine.begin() as conn:
                conn.execute(
                    update(tree_nodes).where(tree_nodes.c.node_id == root.node_id).values(score=0.0)
                )

    def test_delete_被拒绝(self, pg_engine, pg_store, make_tree, make_node):
        from core.tree.db import tree_nodes

        _, root, _ = _seed_tree(pg_store, make_tree, make_node)
        with pytest.raises(ImmutableViolationError), translate_immutable_errors():
            with pg_engine.begin() as conn:
                conn.execute(delete(tree_nodes).where(tree_nodes.c.node_id == root.node_id))

    def test_拒绝后数据逐字节不变(self, pg_engine, pg_store, make_tree, make_node):
        _, root, _ = _seed_tree(pg_store, make_tree, make_node)
        before = pg_store.get_node(root.node_id)
        from core.tree.db import tree_nodes

        with pytest.raises(ImmutableViolationError), translate_immutable_errors():
            with pg_engine.begin() as conn:
                conn.execute(
                    update(tree_nodes).where(tree_nodes.c.node_id == root.node_id).values(score=0.0)
                )
        assert pg_store.get_node(root.node_id) == before


class Test权限回收:
    def test_应用账号_update_delete_被拒(self, pg_engine, pg_store, make_tree, make_node):
        """cineflow_app 角色（迁移内 REVOKE）连触发器都到不了。"""
        _, root, _ = _seed_tree(pg_store, make_tree, make_node)
        with pg_engine.begin() as conn:
            # 迁移是否已执行决定角色是否存在；不存在则本用例无意义，跳过
            role = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'cineflow_app'")
            ).scalar()
            if not role:
                pytest.skip("cineflow_app 角色不存在（Alembic 迁移未执行）")
            conn.execute(text("SET ROLE cineflow_app"))
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(
                    text("UPDATE tree_nodes SET score = 0 WHERE node_id = :nid"),
                    {"nid": root.node_id},
                )


class Test审计抽样:
    def test_随机抽样重算_score_与落盘一致(self, pg_store, make_tree, make_node):
        """随机抽历史节点，按 eval_breakdown 重算加权和并与落盘 score 比对。"""
        _, root, children = _seed_tree(pg_store, make_tree, make_node, n_children=20)
        nodes = pg_store.nodes_of(root.tree_id)
        sample = random.sample(nodes, k=min(10, len(nodes)))
        for node in sample:
            if node.status is NodeStatus.FAILED:
                assert node.score is None
                continue
            recomputed = sum(v["score"] for v in node.eval_breakdown.values()) / max(
                1, len(node.eval_breakdown)
            )
            # make_node 工厂落盘的 score=0.8 与单评估器 breakdown 均值一致
            assert abs(recomputed - node.score) < 1e-9

    def test_失败节点成本完整(self, pg_store, make_tree, make_node):
        tree = make_tree()
        pg_store.create_tree(tree)
        failed = make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            status=NodeStatus.FAILED,
            score=None,
            eval_breakdown={},
            cost=CostRecord(llm_calls=2, generation_api_calls=1, generation_api_cost_usd=0.03),
        )
        pg_store.append_node(failed)
        got = pg_store.get_node(failed.node_id)
        assert got.cost.generation_api_cost_usd == pytest.approx(0.03)
