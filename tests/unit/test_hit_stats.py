"""命中分布与稀释告警单测（功能 011 / T1012，先于实现编写；C4 场景 1~3）。

- per-project 与合并口径命中/UNKNOWN 双报告（合并 = 逐项目合计，双记账一致）；
- 判定口径 = **命中占比**（该项目命中数 / 总命中数）超阈 → DilutionAlert；
  树数占比（tree_ratio）作参考维度同报告——树多但命中少 → 不告警（澄清 Q2）；
- 单项目构成（tree_ratio = 1.0）→ 占比 1.0 必然超阈，note 如实标注"单项目构成"；
- 冲突记录进 HitDistribution.conflicts（UNKNOWN 的理由留痕，供 010/F7 消费）；
- 告警如实可见、不阻止回放；未命中（全 UNKNOWN）不产告警。
"""

import pytest

from core.replay.cross_match import MatchedHit, MatchResult
from core.replay.errors import ValidationError
from core.replay.hit_stats import ReplayOutcome, hit_stats, outcome_for_match
from core.replay.merged_pool import build_merged_pool
from core.replay.pooling_models import (
    ConflictHit,
    PoolingConfig,
    ProjectHitStats,
    ScoreConflict,
)

AGENT = "agent-pool"
FORM = "movie"


def _hit(tree, times: int = 1) -> list[ReplayOutcome]:
    return [
        ReplayOutcome(status="hit", tree_id=tree.tree_id, project_id=tree.project_id)
        for _ in range(times)
    ]


def _unknown(tree, times: int = 1, conflict=None) -> list[ReplayOutcome]:
    return [
        ReplayOutcome(
            status="unknown", tree_id=tree.tree_id, project_id=tree.project_id, conflict=conflict
        )
        for _ in range(times)
    ]


def _matched(tree, node_id: str, score: float) -> MatchedHit:
    return MatchedHit(
        tree_id=tree.tree_id, project_id=tree.project_id, node_id=node_id, score=score
    )


def _by_project(dist, project_id: str) -> ProjectHitStats:
    return next(stats for stats in dist.per_project if stats.project_id == project_id)


@pytest.fixture()
def four_tree_pool(tree_store, pooling_config, pool_multi_project_trees):
    """A/B 各 2 棵同版本集树（4 棵）：项目组成树数占比 0.5 / 0.5。"""
    return build_merged_pool(tree_store, AGENT, FORM, pooling_config), pool_multi_project_trees


class Test双报告与稀释告警:
    def test_A_命中_8_B_命中_2_双报告与告警(self, four_tree_pool, pooling_config):
        """C4 场景 1：A 命中 8 / B 命中 2 → 双报告 + A 占比 0.8 > 0.7 告警。"""
        pool, trees = four_tree_pool
        a_trees = [t for t in trees if t.project_id == "project-a"]
        b_trees = [t for t in trees if t.project_id == "project-b"]
        outcomes = _hit(a_trees[0], 5) + _hit(a_trees[1], 3) + _hit(b_trees[0], 2)
        outcomes += _unknown(a_trees[0]) + _unknown(b_trees[1])

        dist, alerts = hit_stats(pool, outcomes, pooling_config)
        assert dist.merged_hits == 10 and dist.merged_unknowns == 2
        stats_a = _by_project(dist, "project-a")
        assert (stats_a.hits, stats_a.unknowns) == (8, 1)
        assert stats_a.hit_ratio == pytest.approx(0.8)
        assert (stats_a.tree_count, stats_a.tree_ratio) == (2, 0.5)
        stats_b = _by_project(dist, "project-b")
        assert (stats_b.hits, stats_b.unknowns) == (2, 1)
        assert stats_b.hit_ratio == pytest.approx(0.2)
        assert (stats_b.tree_count, stats_b.tree_ratio) == (2, 0.5)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.project_id == "project-a"
        assert alert.hit_ratio == pytest.approx(0.8)
        assert alert.tree_ratio == 0.5
        assert alert.threshold == pooling_config.dilution_hit_ratio_threshold
        assert "命中占比" in alert.note  # 判定口径
        assert "参考" in alert.note  # 树数占比为参考维度

    def test_树数占比高但命中占比低不告警(self, tree_store, pooling_config, make_pool_tree):
        """判定口径 = 命中占比（澄清 Q2）：树多不代表稀释，命中来源主导才算。"""
        for i in range(3):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        make_pool_tree(project_id="project-b", created_at=2000.0)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        a_ref = next(ref for ref in pool.trees if ref.project_id == "project-a")
        b_ref = next(ref for ref in pool.trees if ref.project_id == "project-b")
        outcomes = _hit(a_ref) + _hit(b_ref, 4)
        dist, alerts = hit_stats(pool, outcomes, pooling_config)
        assert _by_project(dist, "project-a").tree_ratio == 0.75  # 树数占比高
        assert _by_project(dist, "project-a").hit_ratio == pytest.approx(0.2)  # 命中占比低
        assert _by_project(dist, "project-b").hit_ratio == pytest.approx(0.8)
        assert [alert.project_id for alert in alerts] == ["project-b"]

    def test_阈值可配置(self, four_tree_pool, pooling_config):
        pool, trees = four_tree_pool
        a_trees = [t for t in trees if t.project_id == "project-a"]
        b_trees = [t for t in trees if t.project_id == "project-b"]
        outcomes = _hit(a_trees[0], 8) + _hit(b_trees[0], 2)
        _, alerts_default = hit_stats(pool, outcomes, pooling_config)
        _, alerts_strict = hit_stats(
            pool, outcomes, PoolingConfig(dilution_hit_ratio_threshold=0.85)
        )
        _, alerts_custom = hit_stats(
            pool, outcomes, PoolingConfig(dilution_hit_ratio_threshold=0.75)
        )
        assert [a.project_id for a in alerts_default] == ["project-a"]
        assert alerts_strict == []  # 0.8 ≤ 0.85：不告警（阈值配置化，原则五）
        assert [a.project_id for a in alerts_custom] == ["project-a"]

    def test_无命中不告警(self, four_tree_pool, pooling_config):
        pool, trees = four_tree_pool
        dist, alerts = hit_stats(pool, _unknown(trees[0], 3), pooling_config)
        assert dist.merged_hits == 0 and dist.merged_unknowns == 3
        assert alerts == []
        assert all(stats.hit_ratio == 0.0 for stats in dist.per_project)

    def test_空结果为零分布(self, four_tree_pool, pooling_config):
        pool, _ = four_tree_pool
        dist, alerts = hit_stats(pool, [], pooling_config)
        assert dist.merged_hits == 0 and dist.merged_unknowns == 0
        assert len(dist.per_project) == 2  # 池内项目都在报告里（零命中同样可见）
        assert alerts == []


class Test单项目构成:
    def test_单项目构成必然超阈并如实标注(self, tree_store, pooling_config, make_pool_tree):
        """C4 场景 2：单项目池 → 占比 1.0 告警 + "单项目构成"标注。"""
        for i in range(3):
            make_pool_tree(project_id="project-solo", created_at=1000.0 + i)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        assert pool.tree_count == 3
        ref = pool.trees[0]
        outcomes = [
            ReplayOutcome(status="hit", tree_id=ref.tree_id, project_id=ref.project_id)
            for _ in range(4)
        ]
        dist, alerts = hit_stats(pool, outcomes, pooling_config)
        stats = _by_project(dist, "project-solo")
        assert stats.hit_ratio == 1.0 and stats.tree_ratio == 1.0
        assert len(alerts) == 1
        assert alerts[0].hit_ratio == 1.0
        assert "单项目构成" in alerts[0].note


class Test冲突留痕:
    def _conflict(self, trees, key: str) -> ScoreConflict:
        return ScoreConflict(
            structure_key=key,
            hits=(
                ConflictHit(tree_id=trees[0].tree_id, project_id="project-a", score=0.6),
                ConflictHit(tree_id=trees[2].tree_id, project_id="project-b", score=0.8),
            ),
            note="多棵命中且得分不同 → UNKNOWN 不得分",
        )

    def test_冲突进_conflicts_字段(self, four_tree_pool, pooling_config):
        """C4 场景 3：冲突发生的回放 → conflicts 字段含 ScoreConflict（供 010/F7 消费）。"""
        pool, trees = four_tree_pool
        conflict = self._conflict(trees, '{"shared":true}')
        outcomes = _hit(trees[0], 3) + _hit(trees[2]) + _unknown(trees[2], 2, conflict)
        dist, alerts = hit_stats(pool, outcomes, pooling_config)
        assert dist.conflicts == (conflict,)  # 同一冲突去重留痕
        assert "冲突" in dist.note and "2" in dist.note  # 频次如实记录
        assert dist.merged_unknowns == 2
        assert [alert.project_id for alert in alerts] == ["project-a"]  # 命中占比 0.75 > 0.7

    def test_无关冲突不进分布(self, four_tree_pool, pooling_config):
        pool, trees = four_tree_pool
        dist, _ = hit_stats(pool, _hit(trees[0], 2), pooling_config)
        assert dist.conflicts == ()

    def test_冲突去重且按结构键排序(self, four_tree_pool, pooling_config):
        pool, trees = four_tree_pool
        later = self._conflict(trees, '{"b":2}')
        earlier = self._conflict(trees, '{"a":1}')
        outcomes = _unknown(trees[0], conflict=later)
        outcomes += _unknown(trees[0], conflict=earlier)
        outcomes += _unknown(trees[0], conflict=later)
        dist, _ = hit_stats(pool, outcomes, pooling_config)
        assert [c.structure_key for c in dist.conflicts] == ['{"a":1}', '{"b":2}']


class Test归属校验:
    def test_池外树即拒绝(self, four_tree_pool, pooling_config):
        pool, _ = four_tree_pool
        outcome = ReplayOutcome(status="hit", tree_id="outside-tree", project_id="project-x")
        with pytest.raises(ValidationError, match="池"):
            hit_stats(pool, [outcome], pooling_config)

    def test_池与配置类型校验(self, four_tree_pool, pooling_config):
        pool, _ = four_tree_pool
        with pytest.raises(ValidationError, match="pool"):
            hit_stats({}, [], pooling_config)
        with pytest.raises(ValidationError, match="cfg"):
            hit_stats(pool, [], {"min_trees": 3})

    def test_结果元素类型校验(self, four_tree_pool, pooling_config):
        pool, _ = four_tree_pool
        with pytest.raises(ValidationError, match="ReplayOutcome"):
            hit_stats(pool, [{"status": "hit"}], pooling_config)


class TestReplayOutcome模型:
    def test_命中不得携带冲突(self):
        conflict = ScoreConflict(
            structure_key="k",
            hits=(
                ConflictHit(tree_id="t1", project_id="p1", score=0.6),
                ConflictHit(tree_id="t2", project_id="p2", score=0.8),
            ),
        )
        with pytest.raises(ValidationError, match="冲突"):
            ReplayOutcome(status="hit", tree_id="t1", project_id="p1", conflict=conflict)

    def test_状态枚举与标识非空(self):
        with pytest.raises(ValidationError, match="status"):
            ReplayOutcome(status="maybe", tree_id="t1", project_id="p1")
        with pytest.raises(ValidationError, match="tree_id"):
            ReplayOutcome(status="hit", tree_id="", project_id="p1")
        with pytest.raises(ValidationError, match="project_id"):
            ReplayOutcome(status="unknown", tree_id="t1", project_id="")


class Test结果归属构造:
    def test_命中归属首个命中树(self, four_tree_pool):
        _, trees = four_tree_pool
        result = MatchResult(
            status="hit",
            structure_key='{"k":1}',
            version_hash="a" * 64,
            hits=(_matched(trees[2], "n-b", 0.8), _matched(trees[0], "n-a", 0.8)),
            score=0.8,
        )
        outcome = outcome_for_match(result, probe_tree_id=trees[2].tree_id)
        assert outcome.status == "hit"
        assert outcome.project_id == "project-b"  # 首个命中（池序）归属
        assert outcome.tree_id == trees[2].tree_id

    def test_未命中归属被探测树(self, four_tree_pool):
        _, trees = four_tree_pool
        conflict = ScoreConflict(
            structure_key='{"shared":true}',
            hits=(
                ConflictHit(tree_id=trees[0].tree_id, project_id="project-a", score=0.6),
                ConflictHit(tree_id=trees[2].tree_id, project_id="project-b", score=0.8),
            ),
        )
        result = MatchResult(
            status="unknown",
            structure_key='{"shared":true}',
            version_hash="a" * 64,
            conflict=conflict,
        )
        outcome = outcome_for_match(
            result, probe_tree_id=trees[0].tree_id, probe_project_id="project-a"
        )
        assert outcome.status == "unknown"
        assert outcome.tree_id == trees[0].tree_id
        assert outcome.project_id == "project-a"
        assert outcome.conflict == conflict
