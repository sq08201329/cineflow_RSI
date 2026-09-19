"""谱系报表单测（US3 / T417，contracts/lineage.md §1/§2）。

两源汇聚（meta.json 父子/审批/reward + 树库 policy_version→tree_ids）、
字段完整（SC-006：缺失为 0）、冲突报错不静默、epsilon_random/manual 标记。
"""

import json

import pytest

from dreaming.lineage import (
    LineageConflictError,
    build_lineage,
    write_meta,
)


def _meta(
    version,
    parent,
    reward=0.5,
    source="dreaming",
    approval=None,
    created_round="dream-agent-dream-1",
):
    return {
        "version": version,
        "parent_version": parent,
        "created_round": created_round,
        "reward": {"pareto_auc": reward, "parallel_penalty": 0.0, "lambda": 0.5, "reward": reward},
        "source": source,
        "approval": approval,
    }


@pytest.fixture()
def store_with_trees(tree_store, build_historical_tree):
    """树库夹具：两棵树分属两个策略版本（policy_version → tree_ids）。"""
    from core.tree.models import NodeStatus

    spec = [(None, {}, 0.4, NodeStatus.EVALUATED, None)]
    tree_a, _ = build_historical_tree(
        spec, project_id="p", agent_id="agent-dream", policy_version="ver-a"
    )
    tree_b, _ = build_historical_tree(
        spec, project_id="p", agent_id="agent-dream", policy_version="ver-b"
    )
    tree_b2, _ = build_historical_tree(
        spec, project_id="p2", agent_id="agent-dream", policy_version="ver-b"
    )
    return tree_store, {
        "ver-a": [tree_a.tree_id],
        "ver-b": sorted([tree_b.tree_id, tree_b2.tree_id]),
    }


class Test两源汇聚:
    def test_字段完整(self, tmp_path, store_with_trees):
        store, tree_map = store_with_trees
        write_meta(tmp_path, "agent-dream", _meta("ver-a", "ver-root"))
        write_meta(
            tmp_path,
            "agent-dream",
            _meta(
                "ver-b",
                "ver-a",
                reward=0.7,
                approval={
                    "approver": "张三",
                    "at": "2026-09-19T10:00:00+08:00",
                    "decision": "approved",
                    "reason": "ok",
                },
            ),
        )
        report = build_lineage("agent-dream", store, tmp_path)
        by_version = {v["version"]: v for v in report.versions}
        # FR-009/SC-006：父/树/子/reward/审批字段完整
        assert by_version["ver-a"]["parent_version"] == "ver-root"
        assert by_version["ver-a"]["tree_ids"] == tree_map["ver-a"]
        assert by_version["ver-a"]["child_versions"] == ["ver-b"]
        assert by_version["ver-b"]["reward"] == 0.7
        assert by_version["ver-b"]["approval"]["decision"] == "approved"
        assert by_version["ver-b"]["tree_ids"] == tree_map["ver-b"]

    def test_树库独有版本_字段缺失为零(self, tmp_path, store_with_trees):
        """有树无 meta 的版本照常入表（字段缺失为 0/None，不静默丢弃）。"""
        store, tree_map = store_with_trees
        report = build_lineage("agent-dream", store, tmp_path)  # 无 meta 文件
        assert {v["version"] for v in report.versions} == {"ver-a", "ver-b"}
        assert all(v["parent_version"] is None for v in report.versions)

    def test_冲突报错不静默(self, tmp_path, store_with_trees):
        """文件名版本与内容版本不一致（两源冲突）→ 报错而非静默。"""
        store, _ = store_with_trees
        write_meta(tmp_path, "agent-dream", _meta("ver-a", "ver-root"))
        # 篡改：文件名是 ver-a，内容改成别的版本号
        target = tmp_path / "agent-dream" / "ver-a.meta.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["version"] = "tampered"
        target.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(LineageConflictError):
            build_lineage("agent-dream", store, tmp_path)

    def test_epsilon_random_与_manual_标记(self, tmp_path, store_with_trees):
        """FR-011：ε 随机预算产出的版本照常入谱系，source 标记保留。"""
        store, _ = store_with_trees
        write_meta(tmp_path, "agent-dream", _meta("ver-eps", "ver-a", source="epsilon_random"))
        write_meta(tmp_path, "agent-dream", _meta("ver-manual", "ver-a", source="manual"))
        report = build_lineage("agent-dream", store, tmp_path)
        by_version = {v["version"]: v for v in report.versions}
        assert by_version["ver-eps"]["source"] == "epsilon_random"
        assert by_version["ver-manual"]["source"] == "manual"

    def test_报告可序列化(self, tmp_path, store_with_trees):
        store, _ = store_with_trees
        write_meta(tmp_path, "agent-dream", _meta("ver-a", "ver-root"))
        report = build_lineage("agent-dream", store, tmp_path)
        data = json.loads(report.to_json())
        assert data["agent_id"] == "agent-dream"
        assert data["versions"]
