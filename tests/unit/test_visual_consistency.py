"""一致性验收单测（US3 / T328，contracts/consistency.md）。

全一致 pass、注入乱序采样变体 100% reject、漂移清单字段、
样本不足 reject、首轮 tau=null 分档、τ 对接 002 门禁。
"""

import json

import pytest

from agents.visual.consistency import verify_consistency
from agents.visual.loop import build_evaluators, freeze_round_tree, run_round
from core.evaluators.registry import Registry


class StubVisualPolicy:
    policy_version = "bbccdd112233"

    def __init__(self, clips):
        self._clips = clips

    def plan_clips(self, config):
        return list(self._clips)


CLIPS = [
    {"gen_params": {"style": "史诗", "shots": 2, "seed_tier": 1}},
    {"gen_params": {"style": "纪实", "shots": 1, "seed_tier": 2}},
    {"gen_params": {"style": "文艺", "shots": 2, "seed_tier": 3}},
]


def _run_round(round_id, clips, env):
    return run_round(
        round_id,
        StubVisualPolicy(clips),
        env["store"],
        env["artifacts"],
        env["adapter"],
        env["gateway"],
        env["engine"],
        env["config"],
    )


@pytest.fixture()
def env(tree_store, gen_jobs_engine, tmp_path, visual_config, mock_gateway):
    from agents.visual.platform.simulated import SimulatedVideoGen
    from core.tree.artifacts import LocalArtifactStore

    return {
        "store": tree_store,
        "engine": gen_jobs_engine,
        "artifacts": LocalArtifactStore(tmp_path / "artifacts"),
        "adapter": SimulatedVideoGen(visual_config.simulated_gen),
        "gateway": mock_gateway,
        "config": visual_config,
    }


@pytest.fixture()
def two_trees(env):
    """两轮同构探索（同 gen_params）冻结入池——回放轨迹由另一棵树产出。"""
    trees = []
    for round_id in ("c-r1", "c-r2"):
        _run_round(round_id, CLIPS, env)
        trees.append(freeze_round_tree(round_id, env["store"], env["engine"]))
    return trees


def _registry_of(env):
    """按冻结版本注册五评估器（build_evaluators 唯一装配点）。"""
    registry = Registry()
    for evaluator in build_evaluators(env["config"], env["gateway"], env["artifacts"])["all"]:
        registry.register(evaluator)
    return registry


class Test全一致放行:
    def test_重算逐字节一致_pass(self, env, two_trees):
        report = verify_consistency(
            two_trees[0].tree_id, env["store"], env["artifacts"], _registry_of(env)
        )
        assert report.checked_nodes == 3
        assert report.consistent_rate == 1.0
        assert report.drift_nodes == []
        assert report.verdict == "pass"

    def test_双树_tau_对接002门禁(self, env, two_trees):
        """≥2 棵树：真实 vs 回放（另一棵树）τ 参与判定（同构轮次 τ=1）。"""
        report = verify_consistency(
            two_trees[0].tree_id, env["store"], env["artifacts"], _registry_of(env)
        )
        assert report.tau == pytest.approx(1.0)
        assert report.threshold == 0.95


class Test首轮分档:
    def test_池内不足两树_tau为null(self, env):
        """首轮（池内 < 2 棵树）：只验重算一致率，tau=null 并注明。"""
        _run_round("c-solo", CLIPS, env)
        tree = freeze_round_tree("c-solo", env["store"], env["engine"])
        report = verify_consistency(tree.tree_id, env["store"], env["artifacts"], _registry_of(env))
        assert report.tau is None
        assert any("τ" in note or "第二棵" in note for note in report.notes)
        assert report.verdict == "pass"  # 重算一致率单独承载首轮验收线


class Test注入漂移拒绝:
    def test_乱序采样变体_100_reject(self, env, two_trees):
        """回归用例（SC-002）：同版本号但实现漂移（乱序帧）的评估器 → 必拒。

        注入点选 identity_consistency（相邻帧对汉明距离是顺序敏感的；
        均值/std 类统计对置换不变，不构成有效注入）。多镜头节点必漂移。
        """
        registry = _registry_of(env)
        original = registry.get("proxy.identity_consistency", _snapshot_version(env, two_trees[0]))

        from agents.visual.evaluators.identity import IdentityConsistencyEvaluator

        class ShuffledIdentity(IdentityConsistencyEvaluator):
            """注入变体：固定随机置换帧序（实现漂移，版本号被人为对齐）。"""

            def evaluate(self, artifact, context):
                import numpy as np

                samples = context["samples"]
                order = np.random.default_rng(42).permutation(len(samples.frames_gray))
                from agents.visual.frames import FrameSamples

                shuffled = FrameSamples(
                    frames_gray=samples.frames_gray[order],
                    frames_rgb=samples.frames_rgb[order],
                    frame_indices=samples.frame_indices,
                    sampling_spec=samples.sampling_spec,
                )
                return super().evaluate(artifact, {**context, "samples": shuffled})

        doctored = ShuffledIdentity(env["config"].frame_sampling)
        object.__setattr__(doctored.spec, "version", original.spec.version)  # 模拟版本纪律失守
        registry2 = Registry()
        for evaluator in build_evaluators(env["config"], env["gateway"], env["artifacts"])["all"]:
            registry2.register(
                doctored
                if evaluator.spec.evaluator_id == "proxy.identity_consistency"
                else evaluator
            )
        report = verify_consistency(two_trees[0].tree_id, env["store"], env["artifacts"], registry2)
        assert report.verdict == "reject"
        assert report.consistent_rate < 1.0
        assert report.drift_nodes
        entry = report.drift_nodes[0]
        assert set(entry) == {"node_id", "evaluator_key", "stored", "recomputed"}
        assert entry["evaluator_key"].startswith("proxy.identity_consistency@")


class Test样本不足:
    def test_已评估节点不足两个_reject(self, env):
        _run_round("c-thin", CLIPS[:1], env)
        tree = freeze_round_tree("c-thin", env["store"], env["engine"])
        report = verify_consistency(tree.tree_id, env["store"], env["artifacts"], _registry_of(env))
        assert report.verdict == "reject"
        assert any("样本不足" in note for note in report.notes)


class Test报告schema:
    def test_报告JSON可机读(self, env, two_trees):
        report = verify_consistency(
            two_trees[0].tree_id, env["store"], env["artifacts"], _registry_of(env)
        )
        data = json.loads(report.to_json())
        assert set(data) == {
            "tree_id",
            "checked_nodes",
            "consistent_rate",
            "drift_nodes",
            "tau",
            "threshold",
            "verdict",
            "notes",
        }
        assert data["tree_id"] == two_trees[0].tree_id


def _snapshot_version(env, tree):
    return tree.config_snapshot["evaluator_versions"]["proxy.identity_consistency"]
