"""TreeStore：发现树追加/读取/谱系查询。

契约见 specs/001-tree-evaluators/contracts/tree-store.md：
- 接口只有 INSERT 与 SELECT——调用方在类型层面无法发起任何变更（不变量 4）；
- 存储层触发器异常一律经 translate_immutable_errors 转换为 ImmutableViolationError；
- eval_breakdown 键必须为 evaluator_id@version，否则 ValidationError（FR-009）。
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from core.tree.db import discovery_trees, tree_nodes
from core.tree.errors import (
    DuplicateError,
    ImmutableViolationError,
    NotFoundError,
    TreeStoreError,
    ValidationError,
)
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode

# FR-009：evaluator_id@version（恰好一个 @，两侧非空且不含空白）
_BREAKDOWN_KEY_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class TreeStore(Protocol):
    """发现树存储公开接口（实现类对外部不可见，经 create_tree_store 获取）。"""

    def create_tree(self, tree: DiscoveryTree) -> None: ...

    def append_node(self, node: TreeNode) -> None: ...

    def get_node(self, node_id: str) -> TreeNode: ...

    def children(self, node_id: str) -> list[TreeNode]: ...

    def trees_by(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        policy_version: str | None = None,
    ) -> list[DiscoveryTree]: ...

    def nodes_of(self, tree_id: str) -> list[TreeNode]: ...


@contextmanager
def translate_immutable_errors() -> Iterator[None]:
    """把存储层触发器/权限拒绝转换为 ImmutableViolationError（契约错误表）。"""
    try:
        yield
    except ImmutableViolationError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "immutable" in message or "permission denied" in message:
            raise ImmutableViolationError(str(exc)) from exc
        raise


def _row_to_node(row) -> TreeNode:
    return TreeNode(
        node_id=row.node_id,
        tree_id=row.tree_id,
        parent_id=row.parent_id,
        depth=row.depth,
        agent_id=row.agent_id,
        policy_version=row.policy_version,
        prompt=row.prompt,
        observation_context=row.observation_context,
        artifact_hash=row.artifact_hash,
        eval_breakdown=row.eval_breakdown,
        score=row.score,
        cost=CostRecord(**row.cost),
        status=NodeStatus(row.status),
        created_at=row.created_at,
    )


def _row_to_tree(row) -> DiscoveryTree:
    return DiscoveryTree(
        tree_id=row.tree_id,
        project_id=row.project_id,
        agent_id=row.agent_id,
        policy_version=row.policy_version,
        root_id=row.root_id,
        node_ids=row.node_ids,
        config_snapshot=row.config_snapshot,
    )


class _SqlTreeStore:
    """TreeStore 的 SQLAlchemy Core 实现（模块内私有，经工厂函数暴露）。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_tree(self, tree: DiscoveryTree) -> None:
        if not isinstance(tree.config_snapshot, dict) or not tree.config_snapshot:
            raise ValidationError("config_snapshot 必须为非空 dict")
        try:
            with translate_immutable_errors(), self._engine.begin() as conn:
                conn.execute(
                    insert(discovery_trees).values(
                        tree_id=tree.tree_id,
                        project_id=tree.project_id,
                        agent_id=tree.agent_id,
                        policy_version=tree.policy_version,
                        root_id=tree.root_id,
                        node_ids=list(tree.node_ids),
                        config_snapshot=tree.config_snapshot,
                    )
                )
        except IntegrityError as exc:
            raise DuplicateError(f"tree_id 已存在：{tree.tree_id}") from exc

    def append_node(self, node: TreeNode) -> None:
        # 状态机：PLANNED 属线上探索内存态，禁止落盘
        if node.status is NodeStatus.PLANNED:
            raise ValidationError("PLANNED 节点禁止落盘（仅线上探索内存态）")

        # FR-009：eval_breakdown 键必须为 evaluator_id@version
        for key in node.eval_breakdown:
            if not _BREAKDOWN_KEY_RE.match(key):
                raise ValidationError(
                    f"eval_breakdown 键必须为 evaluator_id@version，实际为 {key!r}"
                )

        with self._engine.begin() as conn:
            # 谱系校验：所属树必须存在
            tree_exists = conn.execute(
                select(discovery_trees.c.tree_id).where(discovery_trees.c.tree_id == node.tree_id)
            ).scalar()
            if tree_exists is None:
                raise ValidationError(f"所属树不存在：{node.tree_id}")

            # 谱系校验：非根节点的父节点必须同树存在，且 depth = 父 depth + 1
            if node.parent_id is not None:
                parent = conn.execute(
                    select(tree_nodes.c.tree_id, tree_nodes.c.depth).where(
                        tree_nodes.c.node_id == node.parent_id
                    )
                ).first()
                if parent is None:
                    raise ValidationError(f"父节点不存在：{node.parent_id}")
                if parent.tree_id != node.tree_id:
                    raise ValidationError(
                        f"父节点 {node.parent_id} 不属于树 {node.tree_id}（跨树挂载被拒）"
                    )
                if node.depth != parent.depth + 1:
                    raise ValidationError(
                        f"depth 必须等于父节点 depth + 1（父={parent.depth}，实际={node.depth}）"
                    )

            try:
                with translate_immutable_errors():
                    conn.execute(
                        insert(tree_nodes).values(
                            node_id=node.node_id,
                            tree_id=node.tree_id,
                            parent_id=node.parent_id,
                            depth=node.depth,
                            agent_id=node.agent_id,
                            policy_version=node.policy_version,
                            prompt=node.prompt,
                            observation_context=node.observation_context,
                            artifact_hash=node.artifact_hash,
                            eval_breakdown=node.eval_breakdown,
                            score=node.score,
                            status=node.status.value,
                            cost=asdict(node.cost),
                            created_at=node.created_at,
                        )
                    )
            except IntegrityError as exc:
                raise self._classify_integrity_error(node.node_id, exc) from exc

    def _classify_integrity_error(self, node_id: str, exc: IntegrityError) -> TreeStoreError:
        """主键冲突 → DuplicateError；其余约束失败 → ValidationError（绝不部分落盘）。"""
        with self._engine.connect() as conn:
            exists = conn.execute(
                select(tree_nodes.c.node_id).where(tree_nodes.c.node_id == node_id)
            ).scalar()
        if exists is not None:
            return DuplicateError(f"node_id 已存在：{node_id}")
        return ValidationError(f"节点写入违反存储约束：{exc.orig}")

    def get_node(self, node_id: str) -> TreeNode:
        with self._engine.connect() as conn:
            row = conn.execute(select(tree_nodes).where(tree_nodes.c.node_id == node_id)).first()
        if row is None:
            raise NotFoundError(f"节点不存在：{node_id}")
        return _row_to_node(row)

    def children(self, node_id: str) -> list[TreeNode]:
        """按 created_at 升序返回子节点（无子节点或节点不存在 → 空列表）。"""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(tree_nodes)
                .where(tree_nodes.c.parent_id == node_id)
                .order_by(tree_nodes.c.created_at)
            ).all()
        return [_row_to_node(row) for row in rows]

    def trees_by(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        policy_version: str | None = None,
    ) -> list[DiscoveryTree]:
        filters = {
            "project_id": project_id,
            "agent_id": agent_id,
            "policy_version": policy_version,
        }
        active = {k: v for k, v in filters.items() if v is not None}
        if not active:
            raise ValidationError("trees_by 至少需要一个非 None 的过滤条件")
        stmt = select(discovery_trees)
        for column, value in active.items():
            stmt = stmt.where(getattr(discovery_trees.c, column) == value)
        stmt = stmt.order_by(discovery_trees.c.tree_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_row_to_tree(row) for row in rows]

    def nodes_of(self, tree_id: str) -> list[TreeNode]:
        """返回该树全部节点（按 depth、created_at 排序；树不存在 → 空列表）。"""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(tree_nodes)
                .where(tree_nodes.c.tree_id == tree_id)
                .order_by(tree_nodes.c.depth, tree_nodes.c.created_at)
            ).all()
        return [_row_to_node(row) for row in rows]


def create_tree_store(engine: Engine) -> TreeStore:
    """装配入口：返回 TreeStore 公开接口，实现类对外部不可见。"""
    return _SqlTreeStore(engine)
