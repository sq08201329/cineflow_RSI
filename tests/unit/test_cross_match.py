"""跨项目匹配单测（功能 011 / T1011，先于实现编写；C3 场景 1~4）。

- 规范化精确匹配（结构键 = 002 的 gen_params 规范化 + 版本集 hash）在池内全部项目树中查找；
- 单棵命中 → 返回该历史节点得分（跨项目复用，不再 UNKNOWN）；
- 多棵命中且得分相同 → 命中（一致口径，命中清单含全部参与项目）；
- 多棵命中且得分不同（冲突）→ UNKNOWN + ScoreConflict（树清单/得分/归属齐全；
  不取均值、不取最新），回放不终止；
- 跨版本集不命中；已揭示节点不再命中（与 002 同口径）。
"""

import pytest

from core.replay.cross_match import build_pool_index, cross_match
from core.replay.errors import PoolError, ValidationError
from core.replay.merged_pool import build_merged_pool, evaluator_versions_hash
from core.replay.pooling_models import PoolingConfig

AGENT = "agent-pool"
FORM = "movie"


def _versions_hash(versions: dict) -> str:
    return evaluator_versions_hash({"evaluator_versions": versions})


def _key(temperature: float, tag: str) -> dict:
    """结构键（002 规范化精确匹配的 gen_params 槽）。"""
    return {"temperature": temperature, "tree": tag}


@pytest.fixture()
def disjoint_trees(make_pool_tree):
    """A/B 各 2 棵、结构键无交集：A 有 .3/.7，B 有 .4/.9（A 有 B 无 → 跨项目复用）。"""
    trees = []
    for tag, temperature, score in (
        ("a0", 0.3, 0.6),
        ("a1", 0.7, 0.7),
        ("b0", 0.4, 0.8),
        ("b1", 0.9, 0.9),
    ):
        project_id = "project-a" if tag.startswith("a") else "project-b"
        tree, _ = make_pool_tree(
            project_id=project_id,
            structure_keys=(_key(temperature, tag),),
            scores=(score,),
            created_at=1000.0 + temperature,
        )
        trees.append(tree)
    return trees


@pytest.fixture()
def agreeing_trees(make_pool_tree):
    """A/B 各 2 棵：共享键 `shared` 在 A/B 得分一致（0.6），另有各自独占键。"""
    trees = []
    for tag, project_id, shared_score, own_score in (
        ("a0", "project-a", 0.6, 0.7),
        ("a1", "project-a", 0.6, 0.75),
        ("b0", "project-b", 0.6, 0.8),
        ("b1", "project-b", 0.6, 0.85),
    ):
        tree, _ = make_pool_tree(
            project_id=project_id,
            structure_keys=(_key(0.5, "shared"), _key(0.3, tag)),
            scores=(shared_score, own_score),
            created_at=1000.0 + len(trees),
        )
        trees.append(tree)
    return trees


class Test跨项目命中:
    def test_A_有_B_无_跨项目命中(
        self, tree_store, pooling_config, disjoint_trees, pool_evaluator_versions
    ):
        """C3 场景 1：A 有、B 无的结构键 → 命中 A 的历史得分（跨项目复用）。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        result = cross_match(
            pool, _key(0.3, "a0"), _versions_hash(pool_evaluator_versions), store=tree_store
        )
        assert result.status == "hit"
        assert result.score == 0.6
        assert [hit.project_id for hit in result.hits] == ["project-a"]
        assert result.conflict is None
        assert result.structure_key == '{"temperature":0.3,"tree":"a0"}'

    def test_两项目独占键各自命中(
        self, tree_store, pooling_config, disjoint_trees, pool_evaluator_versions
    ):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        version_hash = _versions_hash(pool_evaluator_versions)
        for expected_project, expected_score in (
            ("project-a", 0.6),
            ("project-b", 0.8),
        ):
            temperature = 0.3 if expected_project == "project-a" else 0.4
            tag = "a0" if expected_project == "project-a" else "b0"
            result = cross_match(pool, _key(temperature, tag), version_hash, store=tree_store)
            assert result.status == "hit"
            assert result.score == expected_score
            assert [hit.project_id for hit in result.hits] == [expected_project]

    def test_同分命中_清单含全部参与项目(
        self, tree_store, pooling_config, agreeing_trees, pool_evaluator_versions
    ):
        """C3 场景 2：A 与 B 都有且得分相同 → 命中（一致），命中清单含两项目。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        result = cross_match(
            pool, _key(0.5, "shared"), _versions_hash(pool_evaluator_versions), store=tree_store
        )
        assert result.status == "hit"
        assert result.score == 0.6
        assert set(result.matched_project_ids) == {"project-a", "project-b"}
        assert len(result.hits) == 4

    def test_未命中即_UNKNOWN(self, tree_store, pooling_config, disjoint_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        result = cross_match(
            pool, _key(0.15, "missing"), _versions_hash({"rule.x": "1.0.0"}), store=tree_store
        )
        assert result.status == "unknown"
        assert result.hits == () and result.score is None and result.conflict is None


class Test冲突即_UNKNOWN:
    def test_得分不同即_UNKNOWN_加冲突诊断(self, tree_store, pooling_config, pool_conflict_trees):
        """C3 场景 3：A/B 同结构键同版本集但得分不同 → UNKNOWN + 冲突诊断。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        shared = {"temperature": 0.5, "shared": True}
        result = cross_match(
            pool,
            shared,
            _versions_hash({"rule.x": "1.0.0", "proxy.y": "1.0.0"}),
            store=tree_store,
        )
        assert result.status == "unknown"
        assert result.score is None  # 不取均值、不取最新：不得分
        conflict = result.conflict
        assert conflict is not None
        assert conflict.structure_key == '{"shared":true,"temperature":0.5}'
        assert sorted(hit.score for hit in conflict.hits) == [0.6, 0.8]
        assert sorted(hit.project_id for hit in conflict.hits) == ["project-a", "project-b"]
        assert all(hit.tree_id for hit in conflict.hits)  # 树清单归属齐全
        assert "UNKNOWN" in conflict.note and "不取均值" in conflict.note

    def test_冲突后回放不终止(self, tree_store, pooling_config, pool_conflict_trees):
        """冲突是一次未命中的理由，不是终态：后续匹配照常工作。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        version_hash = pool.version_groups[0].evaluator_versions_hash
        conflicted = cross_match(
            pool, {"temperature": 0.5, "shared": True}, version_hash, store=tree_store
        )
        assert conflicted.status == "unknown"
        after = cross_match(
            pool, {"temperature": 0.3, "unique": "project-a"}, version_hash, store=tree_store
        )
        assert after.status == "hit"
        assert after.score == 0.7


class Test版本集过滤:
    def test_跨版本集不命中也不混池(
        self, tree_store, pooling_config, make_pool_tree, pool_evaluator_versions
    ):
        """C3 场景 4：评估器版本是匹配的组成——跨版本不命中、也不产生"跨版本冲突"。"""
        new_versions = {**pool_evaluator_versions, "rule.x": "2.0.0"}
        v1 = _versions_hash(pool_evaluator_versions)
        v2 = _versions_hash(new_versions)
        assert v1 != v2
        shared_key = {"temperature": 0.5, "shared": True}  # 同键在两版本集中并存
        v2_only_key = {"temperature": 0.15, "only": "v2"}
        for p_index, project_id in enumerate(("project-a", "project-b")):
            make_pool_tree(
                project_id=project_id,
                structure_keys=(shared_key,),
                scores=(0.7,),
                evaluator_versions=pool_evaluator_versions,
                created_at=1000.0 + p_index,
            )
            make_pool_tree(
                project_id=project_id,
                structure_keys=(shared_key, v2_only_key),
                scores=(0.9, 0.5),
                evaluator_versions=new_versions,
                created_at=1100.0 + p_index,
            )
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)

        # 同键在两版本集得分不同（0.7 vs 0.9）：分组隔离 → 各自命中，不构成冲突
        hit_v1 = cross_match(pool, shared_key, v1, store=tree_store)
        assert hit_v1.status == "hit" and hit_v1.score == 0.7
        assert set(hit_v1.matched_project_ids) == {"project-a", "project-b"}
        hit_v2 = cross_match(pool, shared_key, v2, store=tree_store)
        assert hit_v2.status == "hit" and hit_v2.score == 0.9
        # 版本集不同的树 → 不命中（v2 独有键按 v1 请求）
        assert cross_match(pool, v2_only_key, v1, store=tree_store).status == "unknown"
        assert cross_match(pool, v2_only_key, v2, store=tree_store).score == 0.5

    def test_池外版本集哈希不命中(self, tree_store, pooling_config, pool_multi_project_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        foreign = "0" * 64
        result = cross_match(pool, _key(0.3, "project-a-0"), foreign, store=tree_store)
        assert result.status == "unknown" and result.conflict is None


class Test池索引:
    def test_索引复用与直读等价(
        self, tree_store, pooling_config, disjoint_trees, pool_evaluator_versions
    ):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        index = build_pool_index(tree_store, pool)
        version_hash = _versions_hash(pool_evaluator_versions)
        by_store = cross_match(pool, _key(0.4, "b0"), version_hash, store=tree_store)
        by_index = cross_match(pool, _key(0.4, "b0"), version_hash, index=index)
        assert (
            by_store
            == by_index
            == cross_match(pool, _key(0.4, "b0"), version_hash, store=tree_store, index=index)
        )

    def test_已揭示节点不再命中(
        self, tree_store, pooling_config, disjoint_trees, pool_evaluator_versions
    ):
        """回放口径：已揭示节点不再命中（002 同口径；避免重复计费与重复揭示）。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        node_ids = {
            node.node_id for tree in disjoint_trees for node in tree_store.nodes_of(tree.tree_id)
        }
        result = cross_match(
            pool,
            _key(0.3, "a0"),
            _versions_hash(pool_evaluator_versions),
            store=tree_store,
            exclude_node_ids=frozenset(node_ids),
        )
        assert result.status == "unknown"

    def test_索引与池不符即拒绝(self, tree_store, pooling_config, disjoint_trees):
        """不得张冠李戴：索引来自另一个池（pool_id 不符）→ 拒绝。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        other = build_merged_pool(
            tree_store,
            AGENT,
            FORM,
            PoolingConfig(min_trees=5, pools_dir=pooling_config.pools_dir),
            enforce_min_trees=False,
        )
        assert other.pool_id != pool.pool_id
        index = build_pool_index(tree_store, pool)
        with pytest.raises(PoolError, match="索引"):
            cross_match(
                other, _key(0.3, "a0"), pool.version_groups[0].evaluator_versions_hash, index=index
            )


class Test参数校验:
    def test_结构键必须为映射(self, tree_store, pooling_config, disjoint_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        with pytest.raises(ValidationError, match="gen_params"):
            cross_match(pool, ["not", "a", "dict"], "0" * 64, store=tree_store)

    def test_版本集哈希非空(self, tree_store, pooling_config, disjoint_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        with pytest.raises(ValidationError, match="version_hash"):
            cross_match(pool, _key(0.3, "a0"), "", store=tree_store)

    def test_必须提供_store_或_index(self, tree_store, pooling_config, disjoint_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        with pytest.raises(ValidationError, match="store"):
            cross_match(pool, _key(0.3, "a0"), "0" * 64)

    def test_池类型校验(self, tree_store):
        with pytest.raises(ValidationError, match="pool"):
            cross_match({}, {"a": 1}, "0" * 64, store=tree_store)
