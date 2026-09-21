"""跨项目池化契约聚合（功能 011 / T1022）：C1~C9 全场景端到端断言。

三份契约逐场景核对（pooling.md C1~C2、replay-hit.md C3~C5、lineage-acceptance.md C6~C9），
并承载四个机检门禁：
- **SC-002** 同树双池回放得分序列一致率 100%（逐树 × 逐策略，合并只扩充候选集）；
- **SC-003** 跨版本不混池 100% + 跨形态未显式配置拒绝 100%；
- **SC-004** 前置条件（< min_trees）拒绝 100% + 单项目构成稀释告警 100%；
- **SC-006** 做梦开关默认关闭（真实配置机检）+ 开关状态与前置判定入快照 100%。

夹具：真实 `configs/movie.yaml` 的 replay.pooling 段（副本仅改 pools_dir 指向临时目录）+
三项目 × 六棵树（v1 组 5 棵含共享键与冲突键，v2 组 1 棵）。

一致性口径说明：跨树同结构键**同分**的键计入一致率；**冲突键**按 C3 判 UNKNOWN
（澄清 Q1 的刻意保守口径，单独断言为"声明的差异"），不计入一致率。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from core.replay.cross_lineage import cross_lineage
from core.replay.cross_match import build_pool_index, cross_match
from core.replay.errors import PoolError, ValidationError
from core.replay.hit_stats import ReplayOutcome, hit_stats
from core.replay.merged_pool import (
    build_merged_pool,
    evaluator_versions_hash,
    pool_trees_in_time_order,
    select_version_group,
    split_train_validation,
)
from core.replay.pool import SimulatorPool
from core.replay.pool_snapshot import load_pool_snapshot, persist_pool_snapshot, snapshot_path
from core.replay.pooled_replay import (
    MergedSimulatorPool,
    PooledReplaySimulator,
    pool_trees,
    select_replay_pool,
)
from core.replay.pooling_models import PoolingConfig
from core.replay.unbiasedness import verify_unbiasedness
from dreaming.pooling import select_dreaming_pool
from policies.base import Budget, assert_replay_budget

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT = "agent-pool"
FORM = "movie"
TAU_THRESHOLD = 0.95
BUILT_AT = "2026-09-21T10:00:00+00:00"

V1 = {"rule.x": "1.0.0", "proxy.y": "1.0.0"}
V2 = {"rule.x": "2.0.0", "proxy.y": "1.0.0"}

# 场景：A/B/C 三项目；a0/b0 共享冲突键（同键不同分 → C3 判 UNKNOWN）；c0 属 v2 版本组
_SCENARIO = (
    ("a0", "project-a", "v1", (("shared", 0.6), ("conflict", 0.6), ("a-only", 0.70)), 1000.0),
    ("a1", "project-a", "v1", (("shared", 0.6), ("a-only2", 0.72)), 1001.0),
    ("b0", "project-b", "v1", (("shared", 0.6), ("conflict", 0.8), ("b-only", 0.75)), 1010.0),
    ("b1", "project-b", "v1", (("shared", 0.6), ("b-only2", 0.78)), 1011.0),
    ("c1", "project-c", "v1", (("shared", 0.6), ("c-only", 0.80)), 1020.0),
    ("c0", "project-c", "v2", (("shared-v2", 0.9), ("cross", 0.85)), 2000.0),
)
_VERSIONS = {"v1": V1, "v2": V2}
CONFLICT_KEY = {"temperature": 0.5, "tag": "conflict"}
_V1_TAGS = tuple(tag for tag, _, versions_key, _, _ in _SCENARIO if versions_key == "v1")
_CONSISTENCY_POLICIES = ("顺序探测", "逆序探测", "重复探测")
_MERGED_ROOTS = ("shared", "a-only", "a-only2", "b-only", "b-only2", "c-only")


def _key(tag: str) -> dict:
    return {"temperature": 0.5, "tag": tag}


def _versions_hash(versions: dict) -> str:
    return evaluator_versions_hash({"evaluator_versions": versions})


def _tag_scores(tag: str) -> tuple[tuple[str, float], ...]:
    return next(spec[3] for spec in _SCENARIO if spec[0] == tag)


def _own_tags(tag: str) -> list[str]:
    """该树的键（排除冲突键——一致性口径只覆盖池内同键同分的键，见模块说明）。"""
    return [name for name, _ in _tag_scores(tag) if name != "conflict"]


def _policy_keys(name: str, tags: list[str]) -> list[str]:
    if name == "顺序探测":
        return list(tags)
    if name == "逆序探测":
        return list(reversed(tags))
    return [tags[0], *tags]  # 重复探测：第二次必 UNKNOWN（双池同口径）


def _config_copy(tmp_path: Path, **pooling_overrides) -> PoolingConfig:
    """真实配置副本：读 configs/movie.yaml 的 replay.pooling 段，仅改落盘目录与按需覆盖。"""
    real = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    section = dict(real["replay"]["pooling"])
    section["pools_dir"] = str(tmp_path / "replay" / "pools")
    section.update(pooling_overrides)
    return PoolingConfig.from_config({"replay": {"pooling": section}})


@pytest.fixture()
def pooling_cfg(tmp_path) -> PoolingConfig:
    return _config_copy(tmp_path)


@pytest.fixture()
def scenario(tree_store, make_pool_tree, pooling_cfg):
    """三项目 × 六棵树 + 合并池；返回 (pool, store, trees, nodes)。"""
    trees: dict[str, object] = {}
    nodes: dict[str, list[str]] = {}
    for tag, project_id, versions_key, key_scores, created_at in _SCENARIO:
        tree, node_ids = make_pool_tree(
            project_id=project_id,
            structure_keys=tuple(_key(name) for name, _ in key_scores),
            scores=tuple(score for _, score in key_scores),
            evaluator_versions=_VERSIONS[versions_key],
            created_at=created_at,
        )
        trees[tag] = tree
        nodes[tag] = node_ids
    pool = build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)
    return pool, tree_store, trees, nodes


def _root(store, tree) -> str:
    return next(node.node_id for node in store.nodes_of(tree.tree_id) if node.parent_id is None)


def _probe(simulator, parent_id, tag):
    return simulator.probe(parent_id, _key(tag))


def _probe_sequence(simulator, root_id, tags) -> list[tuple[str, list[float]]]:
    trace = []
    for tag in tags:
        result = _probe(simulator, root_id, tag)
        trace.append(
            (result.status, sorted({n.score for n in result.nodes if n.score is not None}))
        )
    return trace


def _merged_simulator(store, pool, *, version_hash=None, budget=8, exclude=()):
    return PooledReplaySimulator.from_pool(
        pool,
        store,
        worker_count=1,
        budget=Budget(max_probes=budget),
        latency_quantum_ms=0,
        version_hash=version_hash,
        exclude_tree_ids=exclude,
    )


def _single_simulator(store, tree, *, budget=8):
    single = SimulatorPool(store)
    single.add_tree(tree)
    return single.build(worker_count=1, budget=Budget(max_probes=budget), latency_quantum_ms=0)


def _consistency_matrix(store, pool, trees, version_hash) -> tuple[int, int]:
    """逐树 × 逐策略矩阵：返回 (比较次数, 一致次数)。"""
    comparisons = consistent = 0
    for tag in _V1_TAGS:
        own_tags = _own_tags(tag)
        for name in _CONSISTENCY_POLICIES:
            tags = _policy_keys(name, own_tags)
            single_sim = _single_simulator(store, trees[tag])
            merged_sim = _merged_simulator(store, pool, version_hash=version_hash)
            root_id = _root(store, trees[tag])
            comparisons += 1
            if (
                single_sim.trajectory().best_score_curve == merged_sim.trajectory().best_score_curve
                and _probe_sequence(single_sim, root_id, tags)
                == _probe_sequence(merged_sim, root_id, tags)
            ):
                consistent += 1
    return comparisons, consistent


class TestC1合并池构建:
    def test_场景1_多项目合并归属可追溯(self, scenario):
        pool, _, trees, _ = scenario
        assert pool.agent_id == AGENT and pool.form == FORM
        assert pool.tree_count == len(_SCENARIO) == 6
        assert pool.conditions_met is True
        assert {ref.project_id for ref in pool.trees} == {"project-a", "project-b", "project-c"}
        assert {ref.tree_id for ref in pool.trees} == {tree.tree_id for tree in trees.values()}

    def test_场景2_版本集分组不混池(self, scenario):
        pool, _, _, _ = scenario
        assert len(pool.version_groups) == 2
        by_hash = {group.evaluator_versions_hash: group for group in pool.version_groups}
        assert set(by_hash) == {_versions_hash(V1), _versions_hash(V2)}
        assert len(by_hash[_versions_hash(V1)].trees) == 5
        assert len(by_hash[_versions_hash(V2)].trees) == 1
        assert by_hash[_versions_hash(V2)].evaluator_versions == V2

    def test_场景3_树不足即拒绝(self, tree_store, make_pool_tree, pooling_cfg):
        for index in range(pooling_cfg.min_trees - 1):
            make_pool_tree(project_id="project-a", created_at=1000.0 + index)
        with pytest.raises(PoolError, match="前置条件不足"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)

    def test_场景4_同输入两次构建可重现(self, tree_store, scenario, pooling_cfg):
        pool, _, _, _ = scenario
        again = build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)
        assert again.pool_id == pool.pool_id
        assert [ref.tree_id for ref in again.trees] == [ref.tree_id for ref in pool.trees]
        assert again == pool

    def test_场景5_跨形态未显式即拒绝(self, tree_store, make_pool_tree, pooling_cfg):
        for index in range(3):
            make_pool_tree(project_id="project-a", form=FORM, created_at=1000.0 + index)
        make_pool_tree(project_id="project-b", form="short_drama", created_at=1100.0)
        with pytest.raises(PoolError, match="跨形态"):
            build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)
        allowed = build_merged_pool(
            tree_store, AGENT, FORM, replace(pooling_cfg, allow_cross_form=True)
        )
        assert allowed.tree_count == 4 and "跨形态" in allowed.note


class TestC2构建快照:
    def test_场景1_快照落盘字段齐全(self, scenario, pooling_cfg):
        pool, _, _, _ = scenario
        persist_pool_snapshot(pool, pooling_cfg, built_at=BUILT_AT)
        path = snapshot_path(pooling_cfg.pools_dir, AGENT, FORM, pool.pool_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert set(payload) == {
            "pool_id",
            "agent_id",
            "form",
            "version_groups",
            "trees",
            "min_trees",
            "conditions_met",
            "enabled_for_dreaming",
            "built_at",
            "content_hash",
            "note",
        }
        assert payload["min_trees"] == pooling_cfg.min_trees
        assert payload["conditions_met"] is True
        assert payload["built_at"] == BUILT_AT
        assert len(payload["trees"]) == 6
        assert all({"tree_id", "project_id", "created_at"} <= set(ref) for ref in payload["trees"])

    def test_场景2_重复构建幂等(self, scenario, pooling_cfg):
        pool, _, _, _ = scenario
        first = persist_pool_snapshot(pool, pooling_cfg, built_at=BUILT_AT)
        path = snapshot_path(pooling_cfg.pools_dir, AGENT, FORM, pool.pool_id)
        before = path.read_bytes()
        second = persist_pool_snapshot(pool, pooling_cfg, built_at="2026-09-22T10:00:00+00:00")
        assert first == second and path.read_bytes() == before
        assert load_pool_snapshot(path) == first

    def test_场景3_开关与前置判定入快照(self, tree_store, make_pool_tree, pooling_cfg):
        """开关开启：前置判定（满足 / 不足）两种情形均如实入快照（SC-006）。"""
        make_pool_tree(project_id="project-a", created_at=1000.0)
        insufficient_cfg = replace(
            pooling_cfg,
            enabled_for_dreaming=True,
            pools_dir=str(Path(pooling_cfg.pools_dir) / "insufficient"),
        )
        insufficient = build_merged_pool(
            tree_store, AGENT, FORM, insufficient_cfg, enforce_min_trees=False
        )
        snapshot = persist_pool_snapshot(insufficient, insufficient_cfg, built_at=BUILT_AT)
        assert snapshot.enabled_for_dreaming is True and snapshot.conditions_met is False
        assert "前置条件不足" in snapshot.note

        for index in range(2):  # 补足到 min_trees
            make_pool_tree(project_id="project-b", created_at=1001.0 + index)
        sufficient_cfg = replace(
            insufficient_cfg, pools_dir=str(Path(pooling_cfg.pools_dir) / "sufficient")
        )
        sufficient = build_merged_pool(tree_store, AGENT, FORM, sufficient_cfg)
        snapshot = persist_pool_snapshot(sufficient, sufficient_cfg, built_at=BUILT_AT)
        assert snapshot.enabled_for_dreaming is True and snapshot.conditions_met is True


class TestC3跨项目匹配:
    def test_场景1_跨项目命中(self, scenario):
        """A/B 都无该键、仅 C 有 → 命中 C 的历史得分（跨项目复用）。"""
        pool, store, _, _ = scenario
        result = cross_match(pool, _key("c-only"), _versions_hash(V1), store=store)
        assert result.status == "hit"
        assert result.score == 0.80
        assert result.matched_project_ids == ("project-c",)

    def test_场景2_同分命中(self, scenario):
        pool, store, _, _ = scenario
        result = cross_match(pool, _key("shared"), _versions_hash(V1), store=store)
        assert result.status == "hit" and result.score == 0.6
        assert set(result.matched_project_ids) == {"project-a", "project-b", "project-c"}

    def test_场景3_冲突即_UNKNOWN_加诊断(self, scenario):
        pool, store, _, _ = scenario
        result = cross_match(pool, CONFLICT_KEY, _versions_hash(V1), store=store)
        assert result.status == "unknown" and result.score is None
        conflict = result.conflict
        assert conflict is not None
        assert sorted(hit.score for hit in conflict.hits) == [0.6, 0.8]
        assert sorted(hit.project_id for hit in conflict.hits) == ["project-a", "project-b"]
        assert all(hit.tree_id for hit in conflict.hits)
        assert "不取均值" in conflict.note

    def test_场景4_版本集不同不命中(self, scenario):
        pool, store, _, _ = scenario
        assert (
            cross_match(pool, _key("shared-v2"), _versions_hash(V1), store=store).status
            == "unknown"
        )
        hit = cross_match(pool, _key("shared-v2"), _versions_hash(V2), store=store)
        assert hit.status == "hit" and hit.score == 0.9


class TestC4命中分布与稀释:
    def test_场景1_双报告与参考维度(self, scenario, pooling_cfg):
        pool, store, trees, _ = scenario
        simulator = _merged_simulator(store, pool, version_hash=_versions_hash(V1), budget=16)
        root_id = _root(store, trees["a0"])
        for tag in _MERGED_ROOTS:
            assert _probe(simulator, root_id, tag).status == "ok"
        distribution, alerts = hit_stats(pool, simulator.outcomes, pooling_cfg)
        assert distribution.merged_hits == 6 and distribution.merged_unknowns == 0
        hits = {stats.project_id: stats.hits for stats in distribution.per_project}
        tree_ratios = {stats.project_id: stats.tree_ratio for stats in distribution.per_project}
        assert hits == {"project-a": 3, "project-b": 2, "project-c": 1}
        # 树数占比为池内物理组成（含 v2 组）：project-a 2/6
        assert tree_ratios["project-a"] == pytest.approx(2 / 6)
        assert alerts == []  # 无项目命中占比超阈（0.5 / 0.333 / 0.167）
        assert "UNKNOWN 率" in distribution.note

    def test_场景2_单项目构成告警(self, tree_store, make_pool_tree, pooling_cfg):
        for index in range(3):
            make_pool_tree(project_id="project-solo", created_at=1000.0 + index)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)
        ref = pool.trees[0]
        outcomes = [
            ReplayOutcome(status="hit", tree_id=ref.tree_id, project_id=ref.project_id)
            for _ in range(3)
        ]
        distribution, alerts = hit_stats(pool, outcomes, pooling_cfg)
        assert distribution.merged_hits == 3
        assert len(alerts) == 1
        assert alerts[0].hit_ratio == 1.0 and alerts[0].tree_ratio == 1.0
        assert "单项目构成" in alerts[0].note

    def test_场景3_冲突进_conflicts_字段(self, scenario, pooling_cfg):
        pool, store, trees, _ = scenario
        simulator = _merged_simulator(store, pool, version_hash=_versions_hash(V1))
        root_id = _root(store, trees["a0"])
        assert _probe(simulator, root_id, "conflict").status == "unknown"
        distribution, _ = hit_stats(pool, simulator.outcomes, pooling_cfg)
        assert len(distribution.conflicts) == 1
        assert distribution.conflicts[0].structure_key == '{"tag":"conflict","temperature":0.5}'
        assert "冲突" in distribution.note


class TestC5零生成与分树:
    def test_场景1_回放全程零生成(self, scenario):
        pool, store, trees, _ = scenario
        simulator = _merged_simulator(store, pool, version_hash=_versions_hash(V1))
        assert simulator.budget.max_generation_calls == 0
        assert_replay_budget(simulator.budget)
        simulator.observed()
        assert _probe(simulator, _root(store, trees["a0"]), "b-only").status == "ok"
        trajectory = simulator.trajectory()
        # 虚拟成本镜像历史节点口径（llm_calls=1 属历史记录；生成调用恒 0）
        assert trajectory.total_cost.llm_calls == 1
        assert trajectory.total_cost.generation_api_calls == 0

    def test_场景2_分树全局排序最近树只做_validation(self, scenario):
        pool, store, trees, _ = scenario
        ordered = pool_trees_in_time_order(pool)
        assert [ref.created_at for ref in ordered] == sorted(ref.created_at for ref in pool.trees)
        train, validation = split_train_validation(pool)
        assert len(validation) == 1 and validation[0].created_at == 2000.0  # 跨项目最新树（v2）
        assert [ref.tree_id for ref in train] == [ref.tree_id for ref in ordered[:-1]]

        # 留出（validation）语义在回放作用域内可机检：v1 组内最新树 c1 不揭示、不命中
        v1_refs = select_version_group(pool, _versions_hash(V1)).trees
        held_out_ref = max(v1_refs, key=lambda ref: (ref.created_at, ref.project_id))
        assert held_out_ref.tree_id == trees["c1"].tree_id
        held_out = _merged_simulator(
            store,
            pool,
            version_hash=_versions_hash(V1),
            exclude=(held_out_ref.tree_id,),
        )
        revealed = held_out.observed()
        assert _root(store, trees["c1"]) not in revealed
        assert _probe(held_out, _root(store, trees["a0"]), "c-only").status == "unknown"


class TestC6跨项目谱系:
    def test_场景1_跨项目链路可查(self, tree_store, build_historical_tree, tmp_path):
        from core.tree.models import CostRecord, NodeStatus

        spec = [(None, {}, 0.0, NodeStatus.EVALUATED, CostRecord())]
        for version, project_id in (
            ("v1", "project-a"),
            ("v1", "project-b"),
            ("v2", "project-c"),
        ):
            build_historical_tree(
                spec, project_id=project_id, agent_id=AGENT, policy_version=version
            )
        history = tmp_path / "policy-history" / AGENT
        history.mkdir(parents=True)
        for version, parent in (("v1", None), ("v2", "v1")):
            (history / f"{version}.meta.json").write_text(
                json.dumps({"version": version, "parent_version": parent}), encoding="utf-8"
            )
        lineage = cross_lineage(
            tree_store, "v1", agent_id=AGENT, history_root=tmp_path / "policy-history"
        )
        assert {ref.project_id for ref in lineage.trees} == {"project-a", "project-b"}
        assert [(ref.version, ref.project_id) for ref in lineage.child_versions] == [
            ("v2", "project-c")
        ]
        payload = json.loads(lineage.to_json())
        assert payload["child_versions"][0]["project_id"] == "project-c"
        assert {entry["project_id"] for entry in payload["trees"]} == {"project-a", "project-b"}

    def test_场景2_单项目版本空列表而非缺失(self, tree_store, build_historical_tree, tmp_path):
        from core.tree.models import CostRecord, NodeStatus

        spec = [(None, {}, 0.0, NodeStatus.EVALUATED, CostRecord())]
        build_historical_tree(
            spec, project_id="project-solo", agent_id=AGENT, policy_version="solo"
        )
        history = tmp_path / "policy-history" / AGENT
        history.mkdir(parents=True)
        (history / "solo.meta.json").write_text(
            json.dumps({"version": "solo", "parent_version": None}), encoding="utf-8"
        )
        lineage = cross_lineage(
            tree_store, "solo", agent_id=AGENT, history_root=tmp_path / "policy-history"
        )
        payload = json.loads(lineage.to_json())
        assert len(payload["trees"]) == 1 and payload["child_versions"] == []


class TestC7一致性验收:
    """SC-002：同树在单项目池与合并池回放的得分序列一致率必须 100%。"""

    def test_逐树逐策略一致率_100(self, scenario):
        pool, store, trees, _ = scenario
        comparisons, consistent = _consistency_matrix(store, pool, trees, _versions_hash(V1))
        assert comparisons == len(_V1_TAGS) * len(_CONSISTENCY_POLICIES) == 15
        assert consistent / comparisons == 1.0

    def test_冲突键是声明的差异非不一致(self, scenario):
        """C3 声明：池内同键不同分 → UNKNOWN（合并后冲突键从命中变 UNKNOWN，刻意保守）。"""
        pool, store, trees, _ = scenario
        single_sim = _single_simulator(store, trees["a0"], budget=4)
        root_id = _root(store, trees["a0"])
        assert _probe(single_sim, root_id, "conflict").status == "ok"
        merged_sim = _merged_simulator(store, pool, version_hash=_versions_hash(V1), budget=4)
        result = _probe(merged_sim, root_id, "conflict")
        assert result.status == "unknown"
        assert merged_sim.outcomes[-1].conflict is not None


class TestC8合并口径无偏性:
    def test_τ达标与注入拒绝(self, scenario):
        pool, store, trees, _ = scenario
        simulator = _merged_simulator(store, pool, version_hash=_versions_hash(V1), budget=8)
        root_id = _root(store, trees["a0"])
        replay = []
        for tag in _MERGED_ROOTS:
            result = _probe(simulator, root_id, tag)
            assert result.status == "ok"
            replay.append(result.nodes[0].score)
        real = list(replay)  # 真实重跑口径 = 记录时按结构键重算的得分（同序）
        report = verify_unbiasedness(real, replay, threshold=TAU_THRESHOLD)
        assert report.verdict == "pass" and report.tau >= TAU_THRESHOLD
        injected = verify_unbiasedness(real, list(reversed(replay)), threshold=TAU_THRESHOLD)
        assert injected.verdict == "reject" and injected.tau < TAU_THRESHOLD


class TestC9做梦开关:
    def test_场景3_默认关闭用单项目池(self, scenario, pooling_cfg):
        _, store, _, _ = scenario
        selection = select_dreaming_pool(
            store, agent_id=AGENT, form=FORM, project_id="project-a", cfg=pooling_cfg
        )
        assert selection.merged is False and "开关关闭" in selection.note
        assert isinstance(selection.pool, SimulatorPool)
        assert list(Path(pooling_cfg.pools_dir).rglob("*.json")) == []  # 未越权构建池化产物

    def test_场景1_开启用合并池且快照留痕(self, scenario, pooling_cfg):
        _, store, _, _ = scenario
        on = replace(pooling_cfg, enabled_for_dreaming=True)
        selection = select_dreaming_pool(
            store,
            agent_id=AGENT,
            form=FORM,
            project_id="project-a",
            cfg=on,
            version_hash=_versions_hash(V1),  # 多版本池：显式指定部署版本集
        )
        assert selection.merged is True
        assert isinstance(selection.pool, MergedSimulatorPool)
        assert selection.snapshot.enabled_for_dreaming is True
        assert selection.snapshot.conditions_met is True
        snapshot_file = snapshot_path(on.pools_dir, AGENT, FORM, selection.merged_pool.pool_id)
        assert json.loads(snapshot_file.read_text(encoding="utf-8"))["enabled_for_dreaming"] is True
        assert {tree.project_id for tree in selection.pool.trees} >= {"project-a", "project-b"}

    def test_多版本池缺版本集即拒绝(self, scenario, pooling_cfg):
        """跨版本不混池：多版本池的池选择必须显式指定版本集（不得猜）。"""
        _, store, _, _ = scenario
        on = replace(pooling_cfg, enabled_for_dreaming=True)
        with pytest.raises(ValidationError, match="version_hash"):
            select_dreaming_pool(store, agent_id=AGENT, form=FORM, project_id="project-a", cfg=on)

    def test_场景4_开关开启但前置不足回落单项目池(self, tree_store, make_pool_tree, pooling_cfg):
        for index in range(pooling_cfg.min_trees - 1):
            make_pool_tree(project_id="project-a", created_at=1000.0 + index)
        on = replace(pooling_cfg, enabled_for_dreaming=True)
        selection = select_dreaming_pool(
            tree_store, agent_id=AGENT, form=FORM, project_id="project-a", cfg=on
        )
        assert selection.merged is False
        assert "未启用" in selection.note and "前置条件不足" in selection.note
        assert isinstance(selection.pool, SimulatorPool)
        assert selection.snapshot.conditions_met is False

    def test_场景3机检_真实配置默认关闭(self):
        real = PoolingConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert real.enabled_for_dreaming is False  # 默认关闭（保守闸门）
        assert real.allow_cross_form is False
        assert real.min_trees == 3
        assert real.dilution_hit_ratio_threshold == 0.7


class Test成功标准机检:
    def test_SC002_同树双池一致率_100(self, scenario):
        pool, store, trees, _ = scenario
        comparisons, consistent = _consistency_matrix(store, pool, trees, _versions_hash(V1))
        assert comparisons == 15 and consistent == comparisons
        assert consistent / comparisons == 1.0

    def test_SC003_跨版本不混池与跨形态拒绝_100(
        self, tree_store, scenario, make_pool_tree, pooling_cfg
    ):
        pool, store, _, _ = scenario
        # 跨版本不混池：逐版本组机检——命中树必须全部属于该组
        checked = 0
        for group in pool.version_groups:
            tag = "shared-v2" if group.evaluator_versions_hash == _versions_hash(V2) else "shared"
            result = cross_match(pool, _key(tag), group.evaluator_versions_hash, store=store)
            assert result.status == "hit"
            assert {hit.tree_id for hit in result.hits} <= {ref.tree_id for ref in group.trees}
            checked += 1
        assert checked == len(pool.version_groups) == 2

        # 跨形态未显式配置：逐形态走真实构建路径，100% 拒绝；显式开启后 100% 并入
        for index in range(3):
            make_pool_tree(project_id="project-a", form=FORM, created_at=5000.0 + index)
        rejected = 0
        for index, form in enumerate(("short_drama", "ad", "anime")):
            make_pool_tree(project_id=f"project-{form}", form=form, created_at=5100.0 + index)
            with pytest.raises(PoolError, match="跨形态"):
                build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)
            rejected += 1
        assert rejected == 3
        allowed = build_merged_pool(
            tree_store, AGENT, FORM, replace(pooling_cfg, allow_cross_form=True)
        )
        assert allowed.tree_count == 6 + 3 + 3  # 场景 6 棵 + 同形态 3 棵 + 跨形态 3 棵
        assert "跨形态" in allowed.note

    def test_SC004_前置拒绝与单项目构成告警_100(self, tree_store, make_pool_tree, pooling_cfg):
        """前置拒绝：逐棵累加（0、1、2 棵 < min_trees）→ 每次都必须拒绝。"""
        refusals = 0
        for index in range(pooling_cfg.min_trees):
            with pytest.raises(PoolError, match="前置条件不足"):
                build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)
            refusals += 1
            make_pool_tree(project_id="project-a", created_at=1000.0 + index)
        assert refusals == pooling_cfg.min_trees == 3
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_cfg)  # 补足即放行
        assert pool.conditions_met is True

        # 单项目构成：100% 产告警（占比 1.0 + 标注）
        ref = pool.trees[0]
        outcomes = [
            ReplayOutcome(status="hit", tree_id=ref.tree_id, project_id=ref.project_id)
            for _ in range(2)
        ]
        _, alerts = hit_stats(pool, outcomes, pooling_cfg)
        assert len(alerts) == 1
        assert "单项目构成" in alerts[0].note

    def test_SC006_开关状态与前置判定入快照_100(self, tree_store, make_pool_tree, pooling_cfg):
        cases = ((1, False), (3, True))  # (树数, 期望前置判定)
        checked = 0
        for index, (count, expected) in enumerate(cases):
            while len(tree_store.trees_by(agent_id=AGENT)) < count:
                planted = len(tree_store.trees_by(agent_id=AGENT))
                make_pool_tree(project_id="project-a", created_at=1000.0 + planted)
            cfg = replace(
                pooling_cfg,
                enabled_for_dreaming=True,
                pools_dir=str(Path(pooling_cfg.pools_dir) / f"case-{index}"),
            )
            pool = build_merged_pool(tree_store, AGENT, FORM, cfg, enforce_min_trees=False)
            snapshot = persist_pool_snapshot(pool, cfg, built_at=BUILT_AT)
            assert snapshot.enabled_for_dreaming is True
            assert snapshot.conditions_met is expected
            assert load_pool_snapshot(snapshot_path(cfg.pools_dir, AGENT, FORM, pool.pool_id)) == (
                snapshot
            )
            checked += 1
        assert checked == len(cases) == 2  # 两种前置判定 100% 入快照


class Test参数校验聚合:
    def test_版本组选择与索引一致性(self, scenario):
        pool, store, _, _ = scenario
        with pytest.raises(ValidationError, match="version_hash"):
            select_version_group(pool, None)  # 多版本池必须显式指定
        group = select_version_group(pool, _versions_hash(V1))
        assert group.evaluator_versions == V1
        assert build_pool_index(store, pool).pool_id == pool.pool_id

    def test_树实体读取与留出校验(self, scenario):
        pool, store, _, _ = scenario
        v1 = _versions_hash(V1)
        assert len(pool_trees(store, pool, version_hash=v1)) == 5
        with pytest.raises(ValidationError, match="留出"):
            pool_trees(store, pool, version_hash=v1, exclude_tree_ids=("ghost",))

    def test_选池需单项目树集合(self, scenario, pooling_cfg):
        _, store, _, _ = scenario
        with pytest.raises(ValidationError, match="单项目池"):
            select_replay_pool(store, AGENT, FORM, pooling_cfg, single_project_trees=())
