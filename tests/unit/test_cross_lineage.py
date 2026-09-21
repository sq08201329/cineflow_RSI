"""跨项目谱系查询单测（功能 011 / T1017，先于实现编写；C6）。

- policy_version → 跨项目产出的树（project_id 标注）→ 跨项目子策略版本（归属标注）；
- 走 001 三维索引读路径扩展（`trees_by(agent_id=.../policy_version=...)`）+ 文件化 meta
  父链（005 惯例），**无新 DB 表**；
- JSON 可机读；单项目版本的跨项目字段为**空列表而非缺失**；
- 缺失/异常如实注明，不静默（无产出树、子版本无树、未提供策略历史根目录）。
"""

import json

import pytest

from core.replay.cross_lineage import cross_lineage
from core.replay.errors import ValidationError
from core.tree.models import CostRecord, NodeStatus

AGENT = "agent-pool"


def _leaf_spec():
    return [(None, {}, 0.0, NodeStatus.EVALUATED, CostRecord())]


@pytest.fixture()
def history_root(tmp_path):
    """策略历史根目录夹具（meta 只增不改，schema 与 005 dreaming/lineage 兼容）。"""
    root = tmp_path / "policy-history"

    def _write(version: str, parent_version: str | None, *, agent_id: str = AGENT) -> None:
        directory = root / agent_id
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": version,
            "parent_version": parent_version,
            "created_round": "dream-1",
            "reward": {"reward": 0.5, "pareto_auc": 0.6, "parallel_penalty": 0.1, "lambda": 0.5},
            "source": "dreaming",
        }
        (directory / f"{version}.meta.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    _write.root = root  # type: ignore[attr-defined]
    return _write


@pytest.fixture()
def cross_project_trees(tree_store, build_historical_tree, history_root):
    """跨项目谱系夹具：父版本 v1 在 A/B 产出树，子版本 v2(→B) / v3(→C) 各自产出树。"""
    planted = {}
    for version, project_id in (
        ("v1", "project-a"),
        ("v1", "project-b"),
        ("v2", "project-b"),
        ("v3", "project-c"),
    ):
        tree, _ = build_historical_tree(
            _leaf_spec(), project_id=project_id, agent_id=AGENT, policy_version=version
        )
        planted.setdefault(version, []).append(tree)
    history_root("v1", None)
    history_root("v2", "v1")
    history_root("v3", "v1")
    return planted


class Test跨项目谱系:
    def test_跨项目树与子版本归属齐全(self, tree_store, cross_project_trees, history_root):
        """C6 场景 1：报表给出跨项目树与子版本（归属标注）。"""
        lineage = cross_lineage(tree_store, "v1", agent_id=AGENT, history_root=history_root.root)
        assert lineage.policy_version == "v1"
        assert [(ref.project_id, ref.tree_id) for ref in lineage.trees] == [
            (tree.project_id, tree.tree_id) for tree in cross_project_trees["v1"]
        ]
        assert {ref.project_id for ref in lineage.trees} == {"project-a", "project-b"}
        # 跨项目子策略版本：v2 在 project-b、v3 在 project-c
        assert [(ref.version, ref.project_id) for ref in lineage.child_versions] == [
            ("v2", "project-b"),
            ("v3", "project-c"),
        ]
        assert lineage.note == ""

    def test_报表_JSON_可机读(self, tree_store, cross_project_trees, history_root):
        lineage = cross_lineage(tree_store, "v1", agent_id=AGENT, history_root=history_root.root)
        payload = json.loads(lineage.to_json())
        assert payload["policy_version"] == "v1"
        assert sorted(payload) == ["child_versions", "note", "policy_version", "trees"]
        assert {entry["project_id"] for entry in payload["trees"]} == {
            "project-a",
            "project-b",
        }
        assert [entry["version"] for entry in payload["child_versions"]] == ["v2", "v3"]
        assert all("project_id" in entry for entry in payload["child_versions"])

    def test_树与子版本顺序确定(self, tree_store, build_historical_tree, history_root):
        """可重现：树按 (project_id, tree_id)、子版本按 (version, project_id) 排序。"""
        for project_id in ("project-c", "project-a", "project-b"):
            build_historical_tree(
                _leaf_spec(), project_id=project_id, agent_id=AGENT, policy_version="v9"
            )
        history_root("v9", None)
        history_root("child-z", "v9")
        history_root("child-a", "v9")
        for project_id in ("project-b", "project-a"):
            build_historical_tree(
                _leaf_spec(), project_id=project_id, agent_id=AGENT, policy_version="child-a"
            )
        build_historical_tree(
            _leaf_spec(), project_id="project-c", agent_id=AGENT, policy_version="child-z"
        )
        lineage = cross_lineage(tree_store, "v9", agent_id=AGENT, history_root=history_root.root)
        assert [ref.project_id for ref in lineage.trees] == [
            "project-a",
            "project-b",
            "project-c",
        ]
        assert [(ref.version, ref.project_id) for ref in lineage.child_versions] == [
            ("child-a", "project-a"),
            ("child-a", "project-b"),
            ("child-z", "project-c"),
        ]


class Test单项目与空字段:
    def test_单项目版本跨项目字段为空列表而非缺失(
        self, tree_store, build_historical_tree, history_root
    ):
        """C6 场景 2：单项目版本正常呈现（字段恒在，值为空列表）。"""
        build_historical_tree(
            _leaf_spec(), project_id="project-solo", agent_id=AGENT, policy_version="solo"
        )
        history_root("solo", None)
        lineage = cross_lineage(tree_store, "solo", agent_id=AGENT, history_root=history_root.root)
        assert [ref.project_id for ref in lineage.trees] == ["project-solo"]
        assert lineage.child_versions == ()
        payload = json.loads(lineage.to_json())
        assert payload["child_versions"] == []  # 空列表而非缺失
        assert payload["trees"] != []

    def test_无产出树如实注明(self, tree_store, history_root):
        history_root("ghost", None)
        lineage = cross_lineage(tree_store, "ghost", agent_id=AGENT, history_root=history_root.root)
        assert lineage.trees == () and lineage.child_versions == ()
        assert "无产出树" in lineage.note

    def test_子版本无树如实注明(self, tree_store, build_historical_tree, history_root):
        """子版本存在但无产出树 → 项目归属未知，如实注明（不静默丢弃、不编造归属）。"""
        build_historical_tree(
            _leaf_spec(), project_id="project-a", agent_id=AGENT, policy_version="p1"
        )
        history_root("p1", None)
        history_root("child-empty", "p1")
        lineage = cross_lineage(tree_store, "p1", agent_id=AGENT, history_root=history_root.root)
        assert lineage.child_versions == ()
        assert "child-empty" in lineage.note and "无产出树" in lineage.note

    def test_未提供策略历史根目录时子版本为空(
        self, tree_store, build_historical_tree, history_root
    ):
        """只读树库可用（三维索引）；缺策略历史根目录 → 子版本为空并注明。"""
        build_historical_tree(
            _leaf_spec(), project_id="project-a", agent_id=AGENT, policy_version="p1"
        )
        history_root("p1", None)
        history_root("child-x", "p1")
        lineage = cross_lineage(tree_store, "p1")
        assert [ref.project_id for ref in lineage.trees] == ["project-a"]
        assert lineage.child_versions == ()
        assert "策略历史" in lineage.note


class Test读路径过滤:
    def test_agent_过滤不混入他_Agent_的树(self, tree_store, build_historical_tree, history_root):
        build_historical_tree(
            _leaf_spec(), project_id="project-a", agent_id=AGENT, policy_version="shared-ver"
        )
        build_historical_tree(
            _leaf_spec(),
            project_id="project-z",
            agent_id="agent-other",
            policy_version="shared-ver",
        )
        history_root("shared-ver", None)
        scoped = cross_lineage(
            tree_store, "shared-ver", agent_id=AGENT, history_root=history_root.root
        )
        assert [ref.project_id for ref in scoped.trees] == ["project-a"]
        unscoped = cross_lineage(tree_store, "shared-ver")
        assert [ref.project_id for ref in unscoped.trees] == ["project-a", "project-z"]

    def test_meta_缺字段即拒绝(self, tree_store, tmp_path):
        directory = tmp_path / "policy-history" / AGENT
        directory.mkdir(parents=True)
        (directory / "broken.meta.json").write_text(
            json.dumps({"version": "broken"}), encoding="utf-8"
        )
        with pytest.raises(ValidationError, match="parent_version"):
            cross_lineage(
                tree_store, "broken", agent_id=AGENT, history_root=tmp_path / "policy-history"
            )


class Test参数校验:
    @pytest.mark.parametrize("bad", ["", None, 1])
    def test_策略版本非空(self, tree_store, bad):
        with pytest.raises(ValidationError, match="policy_version"):
            cross_lineage(tree_store, bad)

    def test_历史根目录需配_agent(self, tree_store, tmp_path):
        with pytest.raises(ValidationError, match="agent_id"):
            cross_lineage(tree_store, "v1", history_root=tmp_path)

    def test_存储类型校验(self):
        with pytest.raises(ValidationError, match="store"):
            cross_lineage(object(), "v1")
