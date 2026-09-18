"""消费方模拟契约测试（T035 / SC-005，PG 集成）。

模拟回放消费方：仅通过 TreeStore 公开接口（create_tree / append_node /
get_node / children / trees_by / nodes_of）跑通 建树→追加→三维查询→
失败节点读取 全路径，不接触任何存储实现细节。
PG 不可达则整体跳过。
"""

import pytest

from core.tree.models import CostRecord, NodeStatus

pytestmark = pytest.mark.integration


def test_回放消费方全路径_仅公开接口(pg_store, make_tree, make_node):
    """SC-005：下游仅通过公开接口完成节点写入与全部谱系查询。"""
    # 1) 建树（三维：项目 / Agent / 策略版本）
    tree = make_tree(project_id="proj-replay", agent_id="agent-x", policy_version="deadbeef0001")
    pg_store.create_tree(tree)

    # 2) 追加节点：根 + 两个已评估子节点 + 一个失败节点
    root = make_node(node_id=tree.root_id, tree_id=tree.tree_id)
    pg_store.append_node(root)
    children = [
        make_node(tree_id=tree.tree_id, parent_id=root.node_id, depth=1, created_at=10.0 + i)
        for i in range(2)
    ]
    for child in children:
        pg_store.append_node(child)
    failed = make_node(
        tree_id=tree.tree_id,
        parent_id=children[0].node_id,
        depth=2,
        status=NodeStatus.FAILED,
        score=None,
        eval_breakdown={},
        cost=CostRecord(llm_calls=5, llm_tokens=3000, wall_clock_seconds=4.2),
    )
    pg_store.append_node(failed)

    # 3) 三维谱系查询：命中且仅命中本树
    hits = pg_store.trees_by(
        project_id="proj-replay", agent_id="agent-x", policy_version="deadbeef0001"
    )
    assert [t.tree_id for t in hits] == [tree.tree_id]

    # 4) 结构读取：全树节点按 depth 排序、子节点枚举
    all_nodes = pg_store.nodes_of(tree.tree_id)
    assert [n.depth for n in all_nodes] == [0, 1, 1, 2]
    assert {n.node_id for n in pg_store.children(root.node_id)} == {c.node_id for c in children}

    # 5) 失败节点读取：score 为 None、成本完整入账
    got = pg_store.get_node(failed.node_id)
    assert got.status is NodeStatus.FAILED
    assert got.score is None
    assert got.cost.llm_calls == 5
    assert got.cost.llm_tokens == 3000

    # 6) 公开接口不存在任何更新/删除方法（类型层面不可变更）
    for forbidden in ("update", "delete", "remove"):
        assert not hasattr(pg_store, forbidden)
