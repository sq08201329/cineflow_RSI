"""池化回放接线单测（功能 011 / T1013，先于实现编写；C5）。

- 跨项目命中：probe 走结构键 + 版本集匹配（B 的根 + A 独有键 → 命中 A 的历史得分）；
- 冲突即 UNKNOWN：不揭示、不泄漏得分，回放不终止；
- 已揭示不再命中（002 同口径，避免重复揭示与重复计费）；
- 版本集隔离：回放只在本版本组内匹配（必须显式指定版本集 hash）；
- 分树（C5）：跨项目跨版本组合并后按 (created_at, project_id, tree_id) 全局排序，
  最近树只做 validation；留出树不揭示、不命中（防泄漏红线）；
- 零生成审计（C5）：budget 归零 + 断言、网关构造即炸仍可完整回放、源码级不引用生成/渲染/网关；
- 非池化路径回归：002 SimulatorPool 语义逐条不变（本接线是新增路径，不改既有行为）。
"""

from pathlib import Path

import pytest

from core.replay import cross_match as cross_match_module
from core.replay import hit_stats as hit_stats_module
from core.replay import pooled_replay as pooled_replay_module
from core.replay.errors import ValidationError
from core.replay.hit_stats import hit_stats
from core.replay.merged_pool import (
    build_merged_pool,
    evaluator_versions_hash,
    pool_trees_in_time_order,
    select_version_group,
    split_train_validation,
)
from core.replay.pool import SimulatorPool
from core.replay.pooled_replay import PooledReplaySimulator, pool_trees
from policies.base import Budget, assert_replay_budget

AGENT = "agent-pool"
FORM = "movie"


def _key(tag: str) -> dict:
    """结构键（002 规范化精确匹配槽）。"""
    return {"temperature": 0.5, "tag": tag}


def _versions_hash(versions: dict) -> str:
    return evaluator_versions_hash({"evaluator_versions": versions})


def _trees(make_pool_tree, spec):
    """spec: [(project_id, tag, score, created_at)] → [(tree, node_ids)]（顺序即 spec 顺序）。"""
    return [
        make_pool_tree(
            project_id=project_id,
            structure_keys=(_key(tag),),
            scores=(score,),
            created_at=created_at,
        )
        for project_id, tag, score, created_at in spec
    ]


def _root_id(store, tree) -> str:
    return next(node.node_id for node in store.nodes_of(tree.tree_id) if node.parent_id is None)


def _child_id(node_ids: list[str]) -> str:
    return node_ids[1]


def _pooled(store, pool, **kwargs) -> PooledReplaySimulator:
    return PooledReplaySimulator.from_pool(
        pool,
        store,
        worker_count=1,
        budget=Budget(max_probes=8),
        latency_quantum_ms=0,
        **kwargs,
    )


@pytest.fixture()
def disjoint_trees(tree_store, make_pool_tree, pooling_config):
    """A/B 各 2 棵、结构键无交集（A: a0/a1；B: b0/b1）。"""
    built = _trees(
        make_pool_tree,
        [
            ("project-a", "a0", 0.6, 1000.0),
            ("project-a", "a1", 0.62, 1001.0),
            ("project-b", "b0", 0.8, 1010.0),
            ("project-b", "b1", 0.82, 1011.0),
        ],
    )
    return build_merged_pool(tree_store, AGENT, FORM, pooling_config), built


class Test跨项目命中:
    def test_探测_B_的根命中_A_的历史得分(self, tree_store, disjoint_trees):
        """US2 场景 1：B 的根 + A 独有的结构键 → 跨项目命中（不再 UNKNOWN）。"""
        pool, built = disjoint_trees
        simulator = _pooled(tree_store, pool)
        b0_root = _root_id(tree_store, built[2][0])
        a0_child = _child_id(built[0][1])

        result = simulator.probe(b0_root, _key("a0"))
        assert result.status == "ok"
        assert [node.node_id for node in result.nodes] == [a0_child]
        assert result.nodes[0].score == 0.6
        outcome = simulator.outcomes[-1]
        assert (outcome.status, outcome.project_id) == ("hit", "project-a")  # 信号来源归属

    def test_同键同分跨项目全部揭示(self, tree_store, pooling_config, make_pool_tree):
        built = _trees(
            make_pool_tree,
            [
                ("project-a", "a0", 0.6, 1000.0),
                ("project-a", "solo-a", 0.7, 1001.0),
                ("project-b", "a0", 0.6, 1010.0),
                ("project-b", "solo-b", 0.8, 1011.0),
            ],
        )
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        simulator = _pooled(tree_store, pool)
        result = simulator.probe(_root_id(tree_store, built[0][0]), _key("a0"))
        assert result.status == "ok"
        assert {node.node_id for node in result.nodes} == {
            _child_id(built[0][1]),
            _child_id(built[2][1]),
        }
        assert simulator.outcomes[-1].project_id == "project-a"  # 首个命中（池序）归属

    def test_已揭示节点不再命中(self, tree_store, disjoint_trees):
        pool, built = disjoint_trees
        simulator = _pooled(tree_store, pool)
        a0_root = _root_id(tree_store, built[0][0])
        assert simulator.probe(a0_root, _key("a0")).status == "ok"
        assert simulator.probe(a0_root, _key("a0")).status == "unknown"  # 002 同口径

    def test_未揭示父节点_UNKNOWN_且不计入分布(self, tree_store, disjoint_trees):
        """002 口径：未揭示父节点 → UNKNOWN（不泄漏存在性）；未走匹配 → 无池化信号。"""
        pool, built = disjoint_trees
        simulator = _pooled(tree_store, pool)
        a0_child = _child_id(built[0][1])
        assert simulator.probe(a0_child, _key("a0")).status == "unknown"
        assert simulator.outcomes == ()  # 无从归属、不构成命中分布口径


class Test冲突即_UNKNOWN:
    def test_冲突不揭示且回放不终止(self, tree_store, pooling_config, pool_conflict_trees):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        simulator = _pooled(tree_store, pool)
        before = simulator.observed()
        root = _root_id(tree_store, pool_conflict_trees[0])

        result = simulator.probe(root, {"temperature": 0.5, "shared": True})
        assert result.status == "unknown"
        assert simulator.observed().keys() == before.keys()  # 零揭示、零信息
        outcome = simulator.outcomes[-1]
        assert outcome.status == "unknown"
        assert outcome.conflict is not None and len(outcome.conflict.hits) == 2

        # 冲突不终止：后续 probe 照常命中
        after = simulator.probe(root, {"temperature": 0.3, "unique": "project-a"})
        assert after.status == "ok"
        distribution, _ = hit_stats(pool, simulator.outcomes, pooling_config)
        assert distribution.conflicts == (outcome.conflict,)
        assert (distribution.merged_hits, distribution.merged_unknowns) == (1, 1)


class Test版本集隔离:
    @pytest.fixture()
    def version_trees(self, tree_store, make_pool_tree, pooling_config, pool_evaluator_versions):
        new_versions = {**pool_evaluator_versions, "rule.x": "2.0.0"}
        spec = []
        for p_index, project_id in enumerate(("project-a", "project-b")):
            spec.append((project_id, "v1-key", 0.7, 1000.0 + p_index, pool_evaluator_versions))
            spec.append((project_id, "v2-key", 0.9, 1100.0 + p_index, new_versions))
        built = [
            make_pool_tree(
                project_id=project_id,
                structure_keys=(_key(tag),),
                scores=(score,),
                created_at=created_at,
                evaluator_versions=versions,
            )
            for project_id, tag, score, created_at, versions in spec
        ]
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        return pool, built, pool_evaluator_versions, new_versions

    def test_只在本版本组内揭示与匹配(self, tree_store, version_trees):
        pool, built, v1_versions, v2_versions = version_trees
        simulator = _pooled(tree_store, pool, version_hash=_versions_hash(v1_versions))
        # spec 顺序：A-v1(0) A-v2(1) B-v1(2) B-v2(3)
        v1_roots = {_root_id(tree_store, built[0][0]), _root_id(tree_store, built[2][0])}
        assert set(simulator.observed()) == v1_roots  # v2 组树未入回放

        # v2 独有键按 v1 不命中；v1 键命中
        assert simulator.probe(sorted(v1_roots)[0], _key("v2-key")).status == "unknown"
        assert simulator.probe(sorted(v1_roots)[0], _key("v1-key")).status == "ok"

    def test_多版本池必须显式指定版本集(self, tree_store, version_trees):
        pool, _, _, _ = version_trees
        with pytest.raises(ValidationError, match="version_hash"):
            _pooled(tree_store, pool)

    def test_池外版本集即拒绝(self, tree_store, version_trees):
        pool, _, _, _ = version_trees
        with pytest.raises(ValidationError, match="版本分组"):
            _pooled(tree_store, pool, version_hash="0" * 64)

    def test_单版本池可缺省(self, tree_store, pool_multi_project_trees, pooling_config):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert (
            _pooled(tree_store, pool).version_hash == pool.version_groups[0].evaluator_versions_hash
        )


class Test分树与留出:
    def test_跨版本组全局排序最近树只做_validation(
        self, tree_store, pooling_config, pool_version_split_trees
    ):
        """C5 场景 2：分树按全局时间排序（跨项目、跨版本组），最近树只做 validation。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        ordered = pool_trees_in_time_order(pool)
        assert [ref.created_at for ref in ordered] == [1000.0, 1001.0, 1010.0, 1011.0]
        # 池内按组排列 ≠ 全局时间序（必须合并各组再排序）
        assert [ref.tree_id for ref in ordered] != [ref.tree_id for ref in pool.trees]
        assert [(ref.created_at, ref.project_id) for ref in ordered] == sorted(
            (ref.created_at, ref.project_id) for ref in pool.trees
        )

        train, validation = split_train_validation(pool)
        assert [ref.tree_id for ref in train] == [ref.tree_id for ref in ordered[:-1]]
        assert len(validation) == 1
        assert validation[0].created_at == 1011.0  # 最近树（跨项目）只做 validation

    def test_单树池无_validation(self, tree_store, pooling_config, make_pool_tree):
        """005 口径：不足两棵 → 无 validation（跳过判定而非编造）。"""
        make_pool_tree(project_id="project-a", created_at=1000.0)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config, enforce_min_trees=False)
        train, validation = split_train_validation(pool)
        assert len(train) == 1 and validation == ()

    def test_池内树实体按全局时间序(self, tree_store, disjoint_trees):
        pool, built = disjoint_trees
        trees = pool_trees(tree_store, pool)
        assert [tree.tree_id for tree in trees] == [
            ref.tree_id for ref in pool_trees_in_time_order(pool)
        ]
        assert {tree.agent_id for tree in trees} == {AGENT}

    def test_留出树不揭示也不命中(self, tree_store, pooling_config, make_pool_tree):
        """防泄漏红线：留出树（validation）既不揭示也不参与匹配。"""
        built = _trees(
            make_pool_tree,
            [
                ("project-a", "train-a", 0.6, 1000.0),
                ("project-b", "train-b", 0.7, 1001.0),
                ("project-a", "holdout", 0.9, 2000.0),  # 最近树 → 留出
            ],
        )
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        train, validation = split_train_validation(pool)
        assert len(validation) == 1
        assert validation[0].created_at == 2000.0

        simulator = _pooled(tree_store, pool, exclude_tree_ids=(validation[0].tree_id,))
        revealed = simulator.observed()
        holdout_root = _root_id(tree_store, built[2][0])
        assert holdout_root not in revealed
        assert _child_id(built[2][1]) not in revealed
        # 只在留出树中存在的结构键 → UNKNOWN（不泄漏留出树的存在性）
        assert (
            simulator.probe(_root_id(tree_store, built[0][0]), _key("holdout")).status == "unknown"
        )
        assert simulator.outcomes[-1].project_id == "project-a"  # 归属被探测树

    def test_留出后无树即拒绝(self, tree_store, pooling_config, make_pool_tree):
        make_pool_tree(project_id="project-a", created_at=1000.0)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config, enforce_min_trees=False)
        with pytest.raises(ValidationError, match="留出"):
            _pooled(tree_store, pool, exclude_tree_ids=(pool.trees[0].tree_id,))

    def test_留出树必须属于池(self, tree_store, disjoint_trees):
        """留出树的标识写错（不在池内）→ 拒绝，绝不静默"没留出"（防泄漏）。"""
        pool, _ = disjoint_trees
        with pytest.raises(ValidationError, match="留出"):
            _pooled(tree_store, pool, exclude_tree_ids=("not-in-pool",))


class Test零生成审计:
    def test_回放全程零生成零网关(self, tree_store, disjoint_trees, pooling_config, monkeypatch):
        """C5 场景 1 + 原则三：budget 归零、网关构造即炸仍可完整回放、源码不引用生成路径。"""
        pool, built = disjoint_trees
        from core.llm_gateway.gateway import LLMGateway

        def _boom(*args, **kwargs):
            raise AssertionError("回放路径不得构造 LLM 网关（零生成，原则三）")

        monkeypatch.setattr(LLMGateway, "__init__", _boom)
        simulator = _pooled(tree_store, pool)
        assert simulator.budget.max_generation_calls == 0
        assert_replay_budget(simulator.budget)

        simulator.observed()
        assert simulator.probe(_root_id(tree_store, built[2][0]), _key("a0")).status == "ok"
        trajectory = simulator.trajectory()
        assert trajectory.probe_count == 1
        distribution, _ = hit_stats(pool, simulator.outcomes, pooling_config)
        assert distribution.merged_hits == 1

        for module in (pooled_replay_module, cross_match_module, hit_stats_module):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for banned in ("llm_gateway", "LLMGateway", "LLMBackend", "Renderer", "simulated_gen"):
                assert banned not in source, f"{module.__name__} 不得引用生成/网关路径：{banned}"

    def test_生成预算非零也强制归零(self, tree_store, disjoint_trees):
        """回放装配双保险（002）在池化路径同样生效：max_generation_calls 强制归零。"""
        pool, _ = disjoint_trees
        simulator = PooledReplaySimulator.from_pool(
            pool,
            tree_store,
            worker_count=1,
            budget=Budget(max_probes=1, max_generation_calls=5),
            latency_quantum_ms=0,
        )
        assert simulator.budget.max_generation_calls == 0
        assert_replay_budget(simulator.budget)


class Test非池化路径回归:
    def test_002_池路径语义不变(self, tree_store, pooling_config, make_pool_tree):
        """本接线不改变 002：池级扩展只发生在 PooledReplaySimulator 路径。"""
        built = _trees(
            make_pool_tree,
            [
                ("project-a", "shared", 0.6, 1000.0),
                ("project-a", "solo-a", 0.7, 1001.0),
                ("project-b", "shared", 0.6, 1010.0),
                ("project-b", "solo-b", 0.8, 1011.0),
            ],
        )
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        by_id = {tree.tree_id: tree for tree, _ in built}
        legacy = SimulatorPool(tree_store)
        for ref in pool.trees:
            legacy.add_tree(by_id[ref.tree_id])
        legacy_simulator = legacy.build(
            worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0
        )
        a_root = _root_id(tree_store, built[0][0])

        # 002 口径：只揭示被探测父节点的同参子节点（不跨项目扩展）
        legacy_hit = legacy_simulator.probe(a_root, _key("shared"))
        assert [node.node_id for node in legacy_hit.nodes] == [_child_id(built[0][1])]
        # 002 口径：未揭示父节点 → UNKNOWN（不泄漏存在性）
        assert legacy_simulator.probe(_child_id(built[0][1]), _key("shared")).status == "unknown"

        # 池化路径（新增能力）：同键同分 → 两项目节点一并揭示；跨项目键 → 命中
        pooled = _pooled(tree_store, pool)
        pooled_hit = pooled.probe(a_root, _key("shared"))
        assert {node.node_id for node in pooled_hit.nodes} == {
            _child_id(built[0][1]),
            _child_id(built[2][1]),
        }
        assert pooled.probe(a_root, _key("solo-b")).status == "ok"

    def test_002_既有测试语义未受影响(self, tree_store, build_historical_tree):
        """002 直连口径回归：单树父节点子树匹配与真实 simulator 一致。"""
        from core.tree.models import CostRecord, NodeStatus

        spec = [
            (None, {}, 0.3, NodeStatus.EVALUATED, CostRecord()),
            (0, {"temperature": 0.5}, 0.6, NodeStatus.EVALUATED, CostRecord()),
        ]
        tree, node_ids = build_historical_tree(spec)
        simulator = SimulatorPool(tree_store)
        simulator.add_tree(tree)
        env = simulator.build(worker_count=1, budget=Budget(max_probes=4), latency_quantum_ms=0)
        result = env.probe(node_ids[0], {"temperature": 0.5})
        assert result.status == "ok"
        assert [node.node_id for node in result.nodes] == [node_ids[1]]


class Test版本组选择:
    def test_select_version_group_显式与单组缺省(
        self, tree_store, pooling_config, pool_multi_project_trees
    ):
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        group = select_version_group(pool, None)
        assert group == pool.version_groups[0]
        assert select_version_group(pool, group.evaluator_versions_hash) == group
        with pytest.raises(ValidationError, match="版本分组"):
            select_version_group(pool, "0" * 64)
