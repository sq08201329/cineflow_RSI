"""合并池回放无偏性验收（功能 011 / T1019，先于实现编写；发布阻塞门禁 C8）。

合并口径：三项目 × 三棵树的合并池回放（`PooledReplaySimulator.probe` 跨项目复用历史得分）
vs **真实重跑**（按结构键重算的确定性评分函数——历史树的落盘得分即记录时该口径的真实值）
→ Kendall τ ≥ 0.95 放行；注入偏差（逆序/乱序/得分抹平/**选错版本组 = 跨版本混池**）
100% 拒绝。复用 002 `core/replay/unbiasedness.py` 同一门槛口径。
"""

import random

import pytest

from core.replay.merged_pool import build_merged_pool, evaluator_versions_hash
from core.replay.pooled_replay import PooledReplaySimulator
from core.replay.unbiasedness import verify_unbiasedness
from policies.base import Budget

pytestmark = pytest.mark.unbiasedness

AGENT = "agent-pool"
FORM = "movie"
TAU_THRESHOLD = 0.95

V1 = {"rule.x": "1.0.0", "proxy.y": "1.0.0"}
V2 = {"rule.x": "2.0.0", "proxy.y": "1.0.0"}

# 8 档结构键梯度 + 真实重跑得分谱系（0.20 ~ 0.725，8 个互不相同 → 区分度充分）
KEYS = tuple({"temperature": round(0.1 * (i + 1), 3), "key": f"k{i}"} for i in range(8))
LADDER = (0.2, 0.275, 0.35, 0.425, 0.5, 0.575, 0.65, 0.725)

# 项目覆盖：A 有 k0~k4、B 有 k3~k7、C 有 k5~k7（k5~k7 对 A 缺失 → 只能跨项目复用）
COVERAGE = (
    ("project-a", tuple(range(0, 5))),
    ("project-b", tuple(range(3, 8))),
    ("project-c", tuple(range(5, 8))),
)


def _real_scores() -> list[float]:
    """真实重跑序列：按结构键重算（确定性评分函数代表评估器重执行）。"""
    return [LADDER[i] for i in range(len(KEYS))]


def _versions_hash(versions: dict) -> str:
    return evaluator_versions_hash({"evaluator_versions": versions})


def _plant_group(make_pool_tree, versions: dict, ladder: tuple, *, base_created_at: float):
    """落一个版本组的树（项目覆盖同形；ladder 即该版本的"真实"得分口径）。"""
    planted: dict[str, object] = {}
    for offset, (project_id, indexes) in enumerate(COVERAGE):
        tree, _ = make_pool_tree(
            project_id=project_id,
            structure_keys=tuple(KEYS[i] for i in indexes),
            scores=tuple(ladder[i] for i in indexes),
            evaluator_versions=versions,
            created_at=base_created_at + offset,
        )
        planted[project_id] = tree
    return planted


@pytest.fixture()
def merged_pool_fixture(tree_store, make_pool_tree, pooling_config):
    """合并池夹具：v1 组记录真实重跑得分；v2 组同结构键但得分反转（选错版本的注入源）。"""
    v1_trees = _plant_group(make_pool_tree, V1, LADDER, base_created_at=1000.0)
    v2_trees = _plant_group(make_pool_tree, V2, tuple(reversed(LADDER)), base_created_at=2000.0)
    pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
    return pool, tree_store, v1_trees, v2_trees


def _root_id(store, tree) -> str:
    return next(node.node_id for node in store.nodes_of(tree.tree_id) if node.parent_id is None)


def _replay_scores(store, pool, *, version_hash: str, probe_tree, keys=KEYS):
    """合并池回放打分：以指定版本组的树根为父节点，逐结构键 probe（UNKNOWN 即断言失败）。"""
    simulator = PooledReplaySimulator.from_pool(
        pool,
        store,
        worker_count=1,
        budget=Budget(max_probes=len(keys)),
        latency_quantum_ms=0,
        version_hash=version_hash,
    )
    root_id = _root_id(store, probe_tree)
    scores: list[float] = []
    for key in keys:
        result = simulator.probe(root_id, key)
        assert result.status == "ok", f"结构键 {key} 未命中（池化匹配被破坏）"
        values = {node.score for node in result.nodes if node.score is not None}
        assert len(values) == 1, f"同一结构键命中得分不一致：{values}"
        scores.append(values.pop())
    return scores, simulator


class Test合并口径放行:
    def test_回放对真实重跑_tau达标(self, merged_pool_fixture):
        pool, store, v1_trees, _ = merged_pool_fixture
        real = _real_scores()
        assert len(set(real)) >= 6  # 得分区分度（梯度有效）
        replay, simulator = _replay_scores(
            store, pool, version_hash=_versions_hash(V1), probe_tree=v1_trees["project-a"]
        )
        report = verify_unbiasedness(real, replay, threshold=TAU_THRESHOLD)
        assert report.verdict == "pass"
        assert report.tau >= TAU_THRESHOLD
        assert simulator.budget.max_generation_calls == 0  # 回放零生成（原则三）

    def test_跨项目命中覆盖本树缺失键(self, merged_pool_fixture):
        """A 无 k5~k7 → 只能跨项目复用（B/C）；命中归属机检。"""
        pool, store, v1_trees, _ = merged_pool_fixture
        replay, simulator = _replay_scores(
            store, pool, version_hash=_versions_hash(V1), probe_tree=v1_trees["project-a"]
        )
        assert replay == _real_scores()
        assert [outcome.project_id for outcome in simulator.outcomes] == [
            "project-a",
            "project-a",
            "project-a",
            "project-a",
            "project-a",
            "project-b",
            "project-b",
            "project-b",
        ]

    def test_轨迹曲线与逐次得分一致(self, merged_pool_fixture):
        """轨迹口径可用于报告/做梦奖励：best_score_curve = 逐次得分的前缀最大值。"""
        pool, store, v1_trees, _ = merged_pool_fixture
        replay, simulator = _replay_scores(
            store, pool, version_hash=_versions_hash(V1), probe_tree=v1_trees["project-a"]
        )
        trajectory = simulator.trajectory()
        assert trajectory.probe_count == len(KEYS)
        expected: list[float] = []
        best = None
        for score in replay:
            best = score if best is None else max(best, score)
            expected.append(best)
        assert trajectory.best_score_curve == expected


class Test注入偏差百分之百拒绝:
    """篡改回放序列 / 选错版本组的合并口径必须 100% 被拒（C8）。"""

    def _fabricated_variants(self, real):
        rng = random.Random(20260921)
        shuffled = real[:]
        rng.shuffle(shuffled)
        return [
            ("reverse_逆序", real, list(reversed(real))),
            ("shuffle_乱序", real, shuffled),
            ("flatten_得分抹平", real, [0.5] * len(real)),
        ]

    def test_全部偏差形态被拒绝(self, merged_pool_fixture):
        pool, store, v1_trees, v2_trees = merged_pool_fixture
        real = _real_scores()
        variants = self._fabricated_variants(real)
        # 池化专有注入：选错版本组（跨版本混池）→ 回放拿到 v2 组的反转得分序列
        wrong_group, _ = _replay_scores(
            store, pool, version_hash=_versions_hash(V2), probe_tree=v2_trees["project-a"]
        )
        assert wrong_group == list(reversed(real))
        variants.append(("wrong_version_group_跨版本混池", real, wrong_group))
        assert len(variants) >= 3
        for name, real_seq, replay_seq in variants:
            report = verify_unbiasedness(real_seq, replay_seq, threshold=TAU_THRESHOLD)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < TAU_THRESHOLD

    def test_版本组隔离本身可机检(self, merged_pool_fixture):
        """正确版本组放行、错版本组拒绝——隔离语义由 τ 门槛机检（原则一）。"""
        pool, store, v1_trees, _ = merged_pool_fixture
        real = _real_scores()
        right, _ = _replay_scores(
            store, pool, version_hash=_versions_hash(V1), probe_tree=v1_trees["project-a"]
        )
        assert verify_unbiasedness(real, right, threshold=TAU_THRESHOLD).verdict == "pass"
