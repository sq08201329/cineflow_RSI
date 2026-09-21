"""池化一致性验收与做梦开关单测（功能 011 / T1018，先于实现编写；C7/C9）。

- C7 一致性验收（SC-002）：**逐树逐策略**——同树在单项目池（002 SimulatorPool）与
  合并池（PooledReplaySimulator）回放同一策略 → 得分序列一致（合并只扩充候选集）；
  夹具保证跨树同结构键**同分**（冲突键按设计判 UNKNOWN，不在一致性口径内）；
- C9 做梦开关（SC-006）：默认关闭 → 单项目池（不构建合并池、不落快照）；
  开启且前置条件满足 → 合并池 + 快照记录开关状态与前置判定；
  开启但前置不足 → 回落单项目池 + 注明"未启用：前置条件不足"（不抛错、不静默）；
  开关读取不特化任何 Agent（源码级机检，原则五）。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.replay.merged_pool import build_merged_pool
from core.replay.pool import SimulatorPool
from core.replay.pool_snapshot import snapshot_path
from core.replay.pooled_replay import PooledReplaySimulator
from core.replay.pooling_models import PoolingConfig
from dreaming import pooling as dreaming_pooling_module
from dreaming.pipeline import in_process_replay
from dreaming.pooling import select_dreaming_pool
from policies.base import Budget

AGENT = "agent-pool"
FORM = "movie"
REPO_ROOT = Path(__file__).resolve().parents[2]

_SHARED_KEYS = {"temperature": 0.5, "tag": "shared-0"}
_SHARED_KEYS_2 = {"temperature": 0.5, "tag": "shared-1"}

# 一致性夹具规格：A/B 各 2 棵；共享键跨项目同分，独占键各树一份
_TREE_SPEC = (
    ("project-a", (("shared-0", 0.6), ("a-only-0", 0.65)), 1000.0),
    ("project-a", (("shared-1", 0.7), ("a-only-1", 0.72)), 1001.0),
    ("project-b", (("shared-0", 0.6), ("b-only-0", 0.8)), 1010.0),
    ("project-b", (("shared-1", 0.7), ("b-only-1", 0.85)), 1011.0),
)

POLICY_SOURCE = """
class Policy:
    \"\"\"确定性贪心参考策略（同一策略跑单项目池与合并池）。\"\"\"

    def solve(self, env, budget):
        grid = %s
        best_id = None
        probes = 0
        for round_no in range(6):
            observations = env.observed()
            scored = sorted(
                ((o.score, o.node_id) for o in observations.values() if o.score is not None),
                key=lambda item: (-item[0], item[1]),
            )
            if scored:
                best_id = scored[0][1]
            if best_id is None or probes >= budget.max_probes:
                break
            env.probe(best_id, grid[round_no %% len(grid)])
            probes += 1
        return best_id or ""
"""
_POLICY_SOURCE = POLICY_SOURCE % (
    '[{"temperature": 0.5, "tag": "shared-0"}, {"temperature": 0.5, "tag": "shared-1"}]'
)


def _key(tag: str) -> dict:
    return {"temperature": 0.5, "tag": tag}


@pytest.fixture()
def consistency_trees(tree_store, make_pool_tree, pooling_config):
    """A/B 各 2 棵：共享键跨项目**同分**（一致性口径），另有各自独占键。

    返回 (pool, [(tree, node_ids), ...])——顺序：A0、A1、B0、B1。
    """
    built = []
    for project_id, key_scores, created_at in _TREE_SPEC:
        built.append(
            make_pool_tree(
                project_id=project_id,
                structure_keys=tuple(_key(tag) for tag, _ in key_scores),
                scores=tuple(score for _, score in key_scores),
                created_at=created_at,
            )
        )
    pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
    return pool, built


def _single_pool(store, tree) -> SimulatorPool:
    pool = SimulatorPool(store)
    pool.add_tree(tree)
    return pool


def _probe_sequence(simulator, root_id, keys) -> list[tuple[str, list[float]]]:
    trace = []
    for key in keys:
        result = simulator.probe(root_id, key)
        scores = sorted({node.score for node in result.nodes if node.score is not None})
        trace.append((result.status, scores))
    return trace


class Test同树双池一致:
    def test_逐树逐策略得分序列一致(self, tree_store, consistency_trees, pooling_config):
        """SC-002 机检：一致率必须 100%（逐树 × 逐策略）。"""
        pool, built = consistency_trees
        policies = {
            "顺序探测": lambda tags: [_key(tag) for tag in tags],
            "逆序探测": lambda tags: [_key(tag) for tag in reversed(tags)],
        }
        comparisons = 0
        consistent = 0
        for index, (tree, node_ids) in enumerate(built):
            root_id = node_ids[0]
            tags = [tag for tag, _ in _TREE_SPEC[index][1]]
            for name, make_keys in policies.items():
                keys = make_keys(tags)
                single_sim = _single_pool(tree_store, tree).build(
                    worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0
                )
                merged_sim = PooledReplaySimulator.from_pool(
                    pool,
                    tree_store,
                    worker_count=1,
                    budget=Budget(max_probes=8),
                    latency_quantum_ms=0,
                )
                comparisons += 1
                single_trace = _probe_sequence(single_sim, root_id, keys)
                merged_trace = _probe_sequence(merged_sim, root_id, keys)
                single_curve = single_sim.trajectory().best_score_curve
                merged_curve = merged_sim.trajectory().best_score_curve
                assert single_trace == merged_trace, f"{name} 探测序列不一致（{tree.project_id}）"
                assert single_curve == merged_curve, f"{name} 得分曲线不一致（{tree.project_id}）"
                consistent += 1
        assert comparisons == len(built) * len(policies) == 8
        assert consistent / comparisons == 1.0  # SC-002 一致率 100%

    def test_本树键双池一致_缺失键由跨项目补足(self, tree_store, consistency_trees, pooling_config):
        """一致性口径只覆盖"本树有的键"；本树缺失的键正是合并的增益（UNKNOWN → 命中）。"""
        pool, built = consistency_trees
        tree, node_ids = built[0]  # A0：共享键 shared-0 + 独占键 a-only-0
        own_keys = [_key("shared-0"), _key("a-only-0")]
        single_sim = _single_pool(tree_store, tree).build(
            worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0
        )
        merged_sim = PooledReplaySimulator.from_pool(
            pool, tree_store, worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0
        )
        assert _probe_sequence(single_sim, node_ids[0], own_keys) == _probe_sequence(
            merged_sim, node_ids[0], own_keys
        )
        # 本树缺失的键：单项目池 UNKNOWN → 合并池跨项目命中（扩充候选集，合并不改已有语义）
        other_key = _key("b-only-1")
        assert _probe_sequence(single_sim, node_ids[0], [other_key]) == [("unknown", [])]
        assert _probe_sequence(merged_sim, node_ids[0], [other_key]) == [("ok", [0.85])]


class Test做梦开关:
    def _dispatch(self, store, pooling_config, *, project_id="project-a", enabled=None):
        cfg = (
            pooling_config
            if enabled is None
            else replace(pooling_config, enabled_for_dreaming=enabled)
        )
        return select_dreaming_pool(
            store, agent_id=AGENT, form=FORM, project_id=project_id, cfg=cfg
        )

    def test_默认关闭用单项目池且零池化产物(self, tree_store, consistency_trees, pooling_config):
        """C9 场景 3：默认关闭 → 做梦仍用单项目池（不构建合并池、不落快照）。"""
        selection = self._dispatch(tree_store, pooling_config)
        assert selection.merged is False
        assert "开关关闭" in selection.note
        assert selection.merged_pool is None and selection.snapshot is None
        assert isinstance(selection.pool, SimulatorPool)
        assert {tree.project_id for tree in selection.pool.trees} == {"project-a"}
        pools_dir = Path(pooling_config.pools_dir)
        assert list(pools_dir.rglob("*.json")) == []  # 未越权构建池化产物
        trajectory = in_process_replay(_POLICY_SOURCE, selection.pool)
        assert trajectory.probe_count > 0  # 做梦回放路径照常可用

    def test_开启且前置满足用合并池并落快照(self, tree_store, consistency_trees, pooling_config):
        """C9 场景 1 的做梦侧：开启 → 合并池；开关状态与前置判定 100% 入快照。"""
        selection = self._dispatch(tree_store, pooling_config, enabled=True)
        assert selection.merged is True
        assert selection.pool.__class__.__name__ == "MergedSimulatorPool"
        snapshot = selection.snapshot
        assert snapshot is not None
        assert snapshot.enabled_for_dreaming is True and snapshot.conditions_met is True
        payload = json.loads(
            snapshot_path(
                pooling_config.pools_dir, AGENT, FORM, selection.merged_pool.pool_id
            ).read_text(encoding="utf-8")
        )
        assert payload["enabled_for_dreaming"] is True
        assert payload["conditions_met"] is True
        # 跨项目树都在池内（合并池口径）
        assert {tree.project_id for tree in selection.pool.trees} == {"project-a", "project-b"}

    def test_开启但前置不足回落单项目池并注明(self, tree_store, pooling_config, make_pool_tree):
        """C9 场景 4：前置条件不足 → 单项目池 + 注明"未启用：前置条件不足"。"""
        for index in range(2):  # 仅 2 棵 < min_trees=3
            make_pool_tree(project_id="project-a", created_at=1000.0 + index)
        selection = self._dispatch(tree_store, pooling_config, enabled=True)
        assert selection.merged is False
        assert "未启用" in selection.note and "前置条件不足" in selection.note
        assert isinstance(selection.pool, SimulatorPool)
        snapshot = selection.snapshot
        assert snapshot is not None
        assert snapshot.enabled_for_dreaming is True and snapshot.conditions_met is False
        assert "前置条件不足" in snapshot.note

    def test_开关状态机检自真实配置(self, pooling_config):
        """SC-006：configs/movie.yaml 默认关闭（缺省即保守闸门）。"""
        real = PoolingConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert real.enabled_for_dreaming is False
        assert pooling_config.enabled_for_dreaming is False

    def test_开关读取不特化_Agent(self):
        """原则五：做梦侧开关读取只读配置与树库，无任何 Agent 名字面量。"""
        source = Path(dreaming_pooling_module.__file__).read_text(encoding="utf-8")
        for agent in ("visual", "promo", "sound", "editing", "storyboard", "screenplay"):
            assert agent not in source, f"做梦池选择不得特化 Agent：{agent}"

    def test_单项目池树集合取自三维索引(self, tree_store, consistency_trees):
        trees = dreaming_pooling_module.single_project_trees(
            tree_store, agent_id=AGENT, project_id="project-b"
        )
        assert {tree.project_id for tree in trees} == {"project-b"}
        assert len(trees) == 2
