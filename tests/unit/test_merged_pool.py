"""合并池构建单测（功能 011 / T1007，先于实现编写；C1 场景 1~5）。

- 分组与归属：按 (Agent, 形态) 读多项目树，每棵树保留 project_id（跨项目可追溯）；
- 版本分组：同版本集合并，跨版本不混池（组数与组内树数逐断言）；
- 前置条件：树数 < min_trees 即拒绝并注明（可上调；放宽路径返回未启用池并注明）；
- 可重现：同输入两次构建同池（pool_id 与树顺序逐项一致；时间重叠按
  (created_at, project_id) 字典序稳定排序）；
- 跨形态：未显式 allow_cross_form 即拒绝；显式开启则并入并如实注明；
- 入池校验：未冻结树拒绝；缺评估器版本集拒绝（不得静默混池）。
"""

import pytest

from core.replay.errors import PoolError, ValidationError
from core.replay.merged_pool import build_merged_pool, evaluator_versions_hash
from core.replay.pooling_models import PoolingConfig

AGENT = "agent-pool"
FORM = "movie"


class Test分组与归属:
    def test_多项目树合并归属可追溯(self, tree_store, pooling_config, pool_multi_project_trees):
        """C1 场景 1：项目 A/B 各 2 棵 → 池含 4 棵，归属可追溯。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert pool.agent_id == AGENT
        assert pool.form == FORM
        assert pool.tree_count == 4
        assert pool.conditions_met is True
        assert len(pool.pool_id) == 64
        assert {ref.tree_id for ref in pool.trees} == {t.tree_id for t in pool_multi_project_trees}
        assert [ref.project_id for ref in pool.trees].count("project-a") == 2
        assert [ref.project_id for ref in pool.trees].count("project-b") == 2
        assert all(ref.node_count == 3 for ref in pool.trees)

    def test_异_Agent_树不入池(self, tree_store, pooling_config, make_pool_tree):
        for _ in range(3):
            make_pool_tree(project_id="project-a")
        for _ in range(2):
            make_pool_tree(project_id="project-c", agent_id="agent-other")
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert pool.tree_count == 3
        assert {ref.project_id for ref in pool.trees} == {"project-a"}

    def test_形态未标注的树按池形态归入并注明(self, tree_store, pooling_config, make_pool_tree):
        """既有轮次树未写 config_snapshot["form"]：按池形态归入，但如实注明未标注。"""
        for i in range(3):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert pool.tree_count == 3
        assert "形态未标注" in pool.note

    def test_形态标注一致时无附注(self, tree_store, pooling_config, make_pool_tree):
        for i in range(3):
            make_pool_tree(project_id="project-a", form=FORM, created_at=1000.0 + i)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert pool.note == ""


class Test版本分组:
    def test_版本集不同按版本集分组不混池(
        self, tree_store, pooling_config, pool_version_split_trees
    ):
        """C1 场景 2：跨版本不混池——两个版本组各 2 棵。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert pool.tree_count == 4
        assert len(pool.version_groups) == 2
        assert [len(group.trees) for group in pool.version_groups] == [2, 2]
        by_tree = {t.tree_id: t for t in pool_version_split_trees}
        for group in pool.version_groups:
            hashes = {
                evaluator_versions_hash(by_tree[ref.tree_id].config_snapshot) for ref in group.trees
            }
            assert hashes == {group.evaluator_versions_hash}
        # 两个组的版本集哈希互不相同（跨版本不混池）
        assert len({group.evaluator_versions_hash for group in pool.version_groups}) == 2

    def test_版本集附注入组(self, tree_store, pooling_config, pool_multi_project_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert len(pool.version_groups) == 1
        group = pool.version_groups[0]
        assert len(group.trees) == 4
        assert group.evaluator_versions_hash == evaluator_versions_hash(
            pool_multi_project_trees[0].config_snapshot
        )
        assert group.evaluator_versions == {"rule.x": "1.0.0", "proxy.y": "1.0.0"}

    def test_缺评估器版本集即拒绝(self, tree_store, pooling_config, build_historical_tree):
        """未知版本集不得静默混进同一组（池化复用的前提是同评估器版本）。"""
        from core.tree.models import CostRecord, NodeStatus

        spec = [(None, {}, 0.0, NodeStatus.EVALUATED, CostRecord())]
        for i in range(3):
            build_historical_tree(spec, project_id=f"project-{i}", agent_id=AGENT)
        with pytest.raises(ValidationError, match="evaluator_versions"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_config)

    def test_同版本集_config_微调如实标注不影响分组(
        self, tree_store, pooling_config, make_pool_tree
    ):
        for i in range(2):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        tweaked, _ = make_pool_tree(
            project_id="project-b",
            created_at=1002.0,
            config_extra={"observation_fields": ["gen_params", "stage"]},
        )
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert len(pool.version_groups) == 1  # 微调不改变分组
        notes = {ref.tree_id: ref.config_note for ref in pool.trees}
        assert "不影响分组" in notes[tweaked.tree_id]
        assert "observation_fields" in notes[tweaked.tree_id]
        assert all(note == "" for tree_id, note in notes.items() if tree_id != tweaked.tree_id)


class Test前置条件:
    def test_树不足即拒绝并注明(self, tree_store, pooling_config, make_pool_tree):
        """C1 场景 3：同 Agent 同形态树 < min_trees → 拒绝并注明前置条件不足。"""
        for i in range(2):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        with pytest.raises(PoolError, match="前置条件不足"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_config)

    def test_min_trees_可上调(self, tree_store, pool_multi_project_trees):
        cfg = PoolingConfig(min_trees=5)
        with pytest.raises(PoolError, match="前置条件不足"):
            build_merged_pool(tree_store, AGENT, FORM, cfg)

    def test_放宽前置条件返回未启用池并注明(self, tree_store, pooling_config, make_pool_tree):
        """做梦/验收路径的读取面：前置不足时不抛错而是返回 conditions_met=False + 注明。"""
        for i in range(2):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config, enforce_min_trees=False)
        assert pool.conditions_met is False
        assert pool.tree_count == 2
        assert "前置条件不足" in pool.note

    def test_无同_Agent_树即拒绝(self, tree_store, pooling_config):
        with pytest.raises(PoolError, match="前置条件不足"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_config)


class Test可重现:
    def test_同输入两次构建同池(self, tree_store, pooling_config, pool_multi_project_trees):
        """C1 场景 4：树集合与顺序确定 → 同输入产同池（快照哈希相等的前提）。"""
        first = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        second = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert first.pool_id == second.pool_id
        assert [g.evaluator_versions_hash for g in first.version_groups] == [
            g.evaluator_versions_hash for g in second.version_groups
        ]
        assert [ref.tree_id for ref in first.trees] == [ref.tree_id for ref in second.trees]
        assert first == second

    def test_时间重叠按_created_at_project_id_稳定排序(
        self, tree_store, pooling_config, pool_overlapping_time_trees
    ):
        """边界：同 created_at 多棵 → (created_at, project_id) 字典序稳定排序（可重现）。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        order = [(ref.created_at, ref.project_id) for ref in pool.trees]
        assert order == sorted(order)
        assert {ref.created_at for ref in pool.trees} == {2000.0}
        assert [ref.project_id for ref in pool.trees] == [
            "project-a",
            "project-a",
            "project-b",
            "project-b",
        ]
        assert pool == build_merged_pool(tree_store, AGENT, FORM, pooling_config)

    def test_树集合不同即不同池_标识(self, tree_store, pooling_config, make_pool_tree):
        for i in range(3):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        first = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        make_pool_tree(project_id="project-b", created_at=2000.0)
        second = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert first.pool_id != second.pool_id  # 池内容变 → 池标识必变（不得复用旧标识）


class Test跨形态:
    def test_跨形态未显式拒绝(self, tree_store, pooling_config, make_pool_tree):
        """C1 场景 5：短剧形态树未显式开启并入电影形态池 → 拒绝。"""
        for i in range(3):
            make_pool_tree(project_id="project-a", form=FORM, created_at=1000.0 + i)
        cross, _ = make_pool_tree(project_id="project-b", form="short_drama", created_at=1100.0)
        with pytest.raises(PoolError, match="跨形态"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        # 拒绝即无池：显式开启后才可并入（同一输入换配置）
        cfg = PoolingConfig(allow_cross_form=True)
        pool = build_merged_pool(tree_store, AGENT, FORM, cfg)
        assert pool.tree_count == 4
        assert cross.tree_id in {ref.tree_id for ref in pool.trees}
        assert "跨形态" in pool.note

    def test_跨形态拒绝时不产生部分池(self, tree_store, pooling_config, make_pool_tree):
        """拒绝语义：整池拒绝（不留"只并了同形态"的半成品）。"""
        for i in range(3):
            make_pool_tree(project_id="project-a", form=FORM, created_at=1000.0 + i)
        make_pool_tree(project_id="project-b", form="short_drama", created_at=1100.0)
        with pytest.raises(PoolError):
            build_merged_pool(tree_store, AGENT, FORM, pooling_config)


class Test入池校验:
    def test_未冻结树拒绝(self, tree_store, pooling_config, make_tree):
        """树行已落盘但根节点缺失（仍在写入中）→ 拒绝入池（沿用 002 冻结校验）。"""
        tree_store.create_tree(make_tree(agent_id=AGENT))
        with pytest.raises(PoolError, match="冻结"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_config)

    @pytest.mark.parametrize("bad", ["", None, 1])
    def test_分组参数非法即拒绝(self, tree_store, pooling_config, bad):
        with pytest.raises(ValidationError):
            build_merged_pool(tree_store, bad, FORM, pooling_config)
        with pytest.raises(ValidationError):
            build_merged_pool(tree_store, AGENT, bad, pooling_config)

    def test_配置类型校验(self, tree_store):
        with pytest.raises(ValidationError, match="cfg"):
            build_merged_pool(tree_store, AGENT, FORM, {"min_trees": 3})
