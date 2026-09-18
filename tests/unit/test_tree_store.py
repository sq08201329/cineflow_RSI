"""TreeStore 契约单测（US1 / T012，SQLite 内存库）。

按 contracts/tree-store.md 的语义契约与错误表逐行覆盖：
追加/读取/谱系查询、eval_breakdown 键格式校验（FR-009）、PLANNED 拒落盘、
存储层触发器异常转换为 ImmutableViolationError。
"""

import pytest
from sqlalchemy import delete, update

from core.tree.errors import (
    DuplicateError,
    ImmutableViolationError,
    NotFoundError,
    ValidationError,
)
from core.tree.models import NodeStatus
from core.tree.store import translate_immutable_errors


def _build_tree_with_root(store, make_tree, make_node, **tree_overrides):
    """建树并追加根节点，返回 (tree, root)。"""
    tree = make_tree(**tree_overrides)
    store.create_tree(tree)
    root = make_node(node_id=tree.root_id, tree_id=tree.tree_id)
    store.append_node(root)
    return tree, root


class TestCreateTree:
    def test_建树与三维查询命中(self, tree_store, make_tree, make_node):
        tree, _ = _build_tree_with_root(
            tree_store, make_tree, make_node, project_id="p1", agent_id="a1", policy_version="v1"
        )
        hits = tree_store.trees_by(project_id="p1", agent_id="a1", policy_version="v1")
        assert [t.tree_id for t in hits] == [tree.tree_id]
        assert hits[0].config_snapshot == tree.config_snapshot

    def test_重复_tree_id_报_DuplicateError(self, tree_store, make_tree):
        tree = make_tree()
        tree_store.create_tree(tree)
        with pytest.raises(DuplicateError):
            tree_store.create_tree(tree)

    def test_空快照报_ValidationError(self, tree_store, make_tree):
        # 领域层已拒空快照；存储层为第二道校验（防御绕过模型层的调用方）
        tree = make_tree()
        object.__setattr__(tree, "config_snapshot", {})
        with pytest.raises(ValidationError):
            tree_store.create_tree(tree)


class TestAppendNode:
    def test_追加并读回_逐字段一致(self, tree_store, make_tree, make_node):
        tree, root = _build_tree_with_root(tree_store, make_tree, make_node)
        got = tree_store.get_node(root.node_id)
        assert got == root
        assert got.tree_id == tree.tree_id

    def test_子节点追加与_children_按_created_at_升序(self, tree_store, make_tree, make_node):
        tree, root = _build_tree_with_root(tree_store, make_tree, make_node)
        children = [
            make_node(
                tree_id=tree.tree_id,
                parent_id=root.node_id,
                depth=1,
                created_at=100.0 + i,
            )
            for i in range(3)
        ]
        # 乱序追加，读取必须按 created_at 升序
        for child in reversed(children):
            tree_store.append_node(child)
        got = tree_store.children(root.node_id)
        assert [n.node_id for n in got] == [n.node_id for n in children]
        assert all(n.depth == root.depth + 1 for n in got)

    def test_所属树不存在报_ValidationError(self, tree_store, make_node):
        with pytest.raises(ValidationError):
            tree_store.append_node(make_node(tree_id="no-such-tree"))

    def test_父节点不存在报_ValidationError(self, tree_store, make_tree, make_node):
        tree, _ = _build_tree_with_root(tree_store, make_tree, make_node)
        with pytest.raises(ValidationError):
            tree_store.append_node(make_node(tree_id=tree.tree_id, parent_id="ghost", depth=1))

    def test_父节点跨树报_ValidationError(self, tree_store, make_tree, make_node):
        tree_a, root_a = _build_tree_with_root(tree_store, make_tree, make_node)
        tree_b, _ = _build_tree_with_root(tree_store, make_tree, make_node)
        with pytest.raises(ValidationError):
            tree_store.append_node(
                make_node(tree_id=tree_b.tree_id, parent_id=root_a.node_id, depth=1)
            )

    def test_depth_不等于父节点加一报_ValidationError(self, tree_store, make_tree, make_node):
        tree, root = _build_tree_with_root(tree_store, make_tree, make_node)
        with pytest.raises(ValidationError):
            tree_store.append_node(make_node(tree_id=tree.tree_id, parent_id=root.node_id, depth=5))

    def test_重复_node_id_报_DuplicateError(self, tree_store, make_tree, make_node):
        tree, root = _build_tree_with_root(tree_store, make_tree, make_node)
        with pytest.raises(DuplicateError):
            tree_store.append_node(root)

    def test_planned_拒落盘(self, tree_store, make_tree, make_node):
        tree = make_tree()
        tree_store.create_tree(tree)
        node = make_node(
            node_id=tree.root_id, tree_id=tree.tree_id, status=NodeStatus.PLANNED, score=None
        )
        with pytest.raises(ValidationError):
            tree_store.append_node(node)

    @pytest.mark.parametrize(
        "bad_key",
        ["rule_no_version", "@1.0.0", "rule.x@", "rule.x@1@2", "has space@1.0"],
    )
    def test_eval_breakdown_键格式非法拒写(self, tree_store, make_tree, make_node, bad_key):
        """FR-009：eval_breakdown 键必须为 evaluator_id@version。"""
        tree, _ = _build_tree_with_root(tree_store, make_tree, make_node)
        node = make_node(tree_id=tree.tree_id, eval_breakdown={bad_key: {"score": 0.1}})
        with pytest.raises(ValidationError):
            tree_store.append_node(node)

    def test_失败节点_cost_完整落盘(self, tree_store, make_tree, make_node):
        """宪章原则二：FAILED 节点成本照常入账。"""
        from core.tree.models import CostRecord

        tree, root = _build_tree_with_root(tree_store, make_tree, make_node)
        failed = make_node(
            tree_id=tree.tree_id,
            parent_id=root.node_id,
            depth=1,
            status=NodeStatus.FAILED,
            score=None,
            eval_breakdown={},
            cost=CostRecord(llm_calls=3, llm_tokens=1200, wall_clock_seconds=2.5),
        )
        tree_store.append_node(failed)
        got = tree_store.get_node(failed.node_id)
        assert got.status is NodeStatus.FAILED
        assert got.score is None
        assert got.cost.llm_calls == 3
        assert got.cost.llm_tokens == 1200


class TestRead:
    def test_get_node_未命中报_NotFoundError(self, tree_store):
        with pytest.raises(NotFoundError):
            tree_store.get_node("no-such-node")

    def test_children_无子节点或节点不存在返回空列表(self, tree_store, make_tree, make_node):
        _, root = _build_tree_with_root(tree_store, make_tree, make_node)
        assert tree_store.children(root.node_id) == []
        assert tree_store.children("no-such-node") == []

    def test_nodes_of_按_depth_created_at_排序(self, tree_store, make_tree, make_node):
        tree, root = _build_tree_with_root(tree_store, make_tree, make_node)
        child = make_node(tree_id=tree.tree_id, parent_id=root.node_id, depth=1)
        grandchild = make_node(tree_id=tree.tree_id, parent_id=child.node_id, depth=2)
        tree_store.append_node(child)
        tree_store.append_node(grandchild)
        got = tree_store.nodes_of(tree.tree_id)
        assert [n.node_id for n in got] == [root.node_id, child.node_id, grandchild.node_id]

    def test_nodes_of_树不存在返回空列表(self, tree_store):
        assert tree_store.nodes_of("no-such-tree") == []


class TestTreesBy:
    def test_无过滤条件报_ValidationError(self, tree_store):
        with pytest.raises(ValidationError):
            tree_store.trees_by()

    def test_单维与组合过滤(self, tree_store, make_tree, make_node):
        _build_tree_with_root(
            tree_store, make_tree, make_node, project_id="p1", agent_id="a1", policy_version="v1"
        )
        tree_b, _ = _build_tree_with_root(
            tree_store, make_tree, make_node, project_id="p1", agent_id="a2", policy_version="v1"
        )
        assert len(tree_store.trees_by(project_id="p1")) == 2
        assert len(tree_store.trees_by(agent_id="a1")) == 1
        hits = tree_store.trees_by(project_id="p1", policy_version="v1", agent_id="a2")
        assert [t.tree_id for t in hits] == [tree_b.tree_id]
        assert tree_store.trees_by(project_id="p1", agent_id="a3") == []


class TestImmutable:
    def test_原始_UPDATE_被_sqlite_触发器拒绝(
        self, tree_store, make_tree, make_node, sqlite_engine
    ):
        from core.tree.db import tree_nodes

        _, root = _build_tree_with_root(tree_store, make_tree, make_node)
        stmt = update(tree_nodes).where(tree_nodes.c.node_id == root.node_id).values(score=0.0)
        with sqlite_engine.begin() as conn, pytest.raises(Exception, match="immutable"):
            conn.execute(stmt)

    def test_原始_DELETE_被_sqlite_触发器拒绝(
        self, tree_store, make_tree, make_node, sqlite_engine
    ):
        from core.tree.db import tree_nodes

        _, root = _build_tree_with_root(tree_store, make_tree, make_node)
        stmt = delete(tree_nodes).where(tree_nodes.c.node_id == root.node_id)
        with sqlite_engine.begin() as conn, pytest.raises(Exception, match="immutable"):
            conn.execute(stmt)

    def test_触发器异常转换为_ImmutableViolationError(
        self, tree_store, make_tree, make_node, sqlite_engine
    ):
        """存储层触发器异常经 translate_immutable_errors 统一转换（契约错误表）。"""
        from core.tree.db import tree_nodes

        _, root = _build_tree_with_root(tree_store, make_tree, make_node)
        stmt = update(tree_nodes).where(tree_nodes.c.node_id == root.node_id).values(score=0.0)
        with pytest.raises(ImmutableViolationError), translate_immutable_errors():
            with sqlite_engine.begin() as conn:
                conn.execute(stmt)
