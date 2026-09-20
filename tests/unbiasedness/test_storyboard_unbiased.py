"""分镜回放无偏性验收（功能 008 / T828，先于实现编写，发布阻塞门禁 C15）。

分镜夹具池（2 棵不同时间树 × 4 组 ShotList 梯度）：回放打分（SimulatorPool probe
揭示的历史得分，`gen_params` 规范化精确匹配）vs 真实重跑（模拟渲染器重执行 +
真实五评估器编排 + 合成定点）得分序列 Kendall τ ≥ 0.95 放行；注入偏差
（篡改评估器版本口径 / judge 胜率 / 对齐口径 / 得分排序）≥3 形态 100% 拒绝。
复用 core/replay/unbiasedness.py（002 同套件，007 同口径接入）。
"""

import copy
import random
from pathlib import Path

import pytest
import yaml

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.evaluators import build_storyboard_evaluators
from agents.storyboard.evaluators.composite import evaluate_storyboard
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.storyboard.shotlist import ShotList
from core.evaluators.base import ArtifactRef
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.replay.pool import SimulatorPool
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.models import CostRecord, NodeStatus, new_id
from policies.base import Budget

pytestmark = pytest.mark.unbiasedness

REPO_ROOT = Path(__file__).resolve().parents[2]
_TAU_THRESHOLD = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))[
    "replay"
]["unbiasedness_tau_threshold"]
_MODEL_PRICES = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


def _config() -> StoryboardConfig:
    """无偏性夹具配置：渲染尺寸缩小提速（全部 ShotList 过三门禁）。"""
    raw = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    )
    raw["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(raw)


def _shotlist_ladder(make_shotlist) -> list[ShotList]:
    """8 组合法 ShotList 梯度（时长阶梯 → 对齐度/judge 得分区分度来源）。

    逐组拉长"非代表情绪镜头"的时长（scene-2 的 calm 镜、scene-1 的无标注镜），
    使预演画面基调相对剧本场景情绪的偏离度形成单调谱系；
    夹具形态本身满足三门禁（景别语法/覆盖率/轴规则）。
    """
    base = make_shotlist().to_dict()["shots"]
    ladder = []
    for k in range(8):
        shots = []
        for shot in base:
            duration_ms = shot["est_duration_ms"]
            if shot["shot_id"] == "shot-03":  # scene-1 承接 s1-l3（无情绪标注）
                duration_ms = 250 + k * 500
            elif shot["shot_id"] == "shot-06":  # scene-2 承接 s2-l3（calm）
                duration_ms = 250 + k * 500
            elif shot["shot_id"] == "shot-08":  # scene-3 承接 s3-l2（joyful）
                duration_ms = 1000 + ((k * 3) % 5) * 250
            shots.append({**shot, "est_duration_ms": duration_ms})
        ladder.append(ShotList(shots=shots))
    return ladder


def _real_rerun_score(shotlist, script, config) -> float:
    """真实重跑：模拟渲染器重执行 + 真实五评估器编排 + 合成定点（确定性）。"""
    adapter = SimulatedStoryboardRenderer()
    animatic = adapter.render(shotlist, script, config)
    gateway = LLMGateway(MockBackend(), price_book=_MODEL_PRICES, sleep=lambda _: None)
    artifact = ArtifactRef(artifact_hash="ab" * 32, metadata=animatic.metadata)
    ctx = {"shotlist": shotlist, "script": script}
    _, score, _ = evaluate_storyboard(
        build_storyboard_evaluators(config, gateway),
        artifact,
        ctx,
        config.evaluator_weights,
    )
    return score


def _storyboard_pool(tree_store, make_tree, make_node, shotlists, real_scores):
    """2 棵不同时间分镜树：子节点 gen_params = 规范化 ShotList，得分 = 真实重跑值。"""
    trees = []
    per_tree = len(shotlists) // 2
    for t in range(2):
        tree = make_tree(
            agent_id="storyboard",
            project_id="storyboard-unbiased",
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "shotlist", "shotlist_hash", "job_id"],
            },
        )
        tree_store.create_tree(tree)
        tree_store.append_node(
            make_node(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                agent_id="storyboard",
                eval_breakdown={},
                score=0.0,
                status=NodeStatus.EVALUATED,
                created_at=float(t * 100),
            )
        )
        for i in range(per_tree):
            idx = t * per_tree + i
            shotlist_dict = shotlists[idx].to_dict()
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="storyboard",
                    observation_context={
                        "gen_params": shotlist_dict,  # 回放匹配槽（002 规范化精确匹配键）
                        "shotlist": shotlist_dict,
                        "shotlist_hash": shotlists[idx].shotlist_hash(),
                        "job_id": f"ub-j{idx}",
                    },
                    score=real_scores[idx],
                    cost=CostRecord(generation_api_calls=1),
                    status=NodeStatus.EVALUATED,
                    created_at=float(t * 100 + 1 + i),
                )
            )
        trees.append(tree)
    return trees


def _replay_scores(trees, tree_store, shotlists):
    """回放打分：池化模拟器按 ShotList 序列 probe，收集揭示得分（UNKNOWN 不得分）。"""
    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    simulator = pool.build(
        worker_count=1, budget=Budget(max_probes=len(shotlists) * 2), latency_quantum_ms=0
    )
    scores = []
    per_tree = len(shotlists) // 2
    roots = {tree.tree_id: tree.root_id for tree in trees}
    for idx, shotlist in enumerate(shotlists):
        result = simulator.probe(roots[trees[idx // per_tree].tree_id], shotlist.to_dict())
        assert result.status == "ok", f"ShotList {idx} 未命中（回放精确匹配被破坏）"
        scores.append(result.nodes[0].score)
    return scores


@pytest.fixture()
def storyboard_unbiased_fixture(tree_store, make_tree, make_node, make_shotlist, script_segment):
    """分镜无偏性夹具：ShotList 梯度序列 + 真实重跑序列 + 池化回放序列。"""
    config = _config()
    shotlists = _shotlist_ladder(make_shotlist)
    real = [_real_rerun_score(shotlist, script_segment, config) for shotlist in shotlists]
    assert len(set(real)) >= 6, f"得分区分度不足（梯度失效）：{real}"
    trees = _storyboard_pool(tree_store, make_tree, make_node, shotlists, real)
    replay = _replay_scores(trees, tree_store, shotlists)
    return real, replay


class Test分镜无偏性放行:
    def test_回放对真实重跑_tau达标(self, storyboard_unbiased_fixture):
        real, replay = storyboard_unbiased_fixture
        assert len(real) == 8  # 2 棵树 × 4 组 ShotList
        report = verify_unbiasedness(real, replay, threshold=_TAU_THRESHOLD)
        assert report.verdict == "pass"
        assert report.tau >= 0.95

    def test_门槛取_configs_口径(self, storyboard_unbiased_fixture):
        assert _TAU_THRESHOLD == 0.95
        real, replay = storyboard_unbiased_fixture
        assert verify_unbiasedness(real, replay).verdict == "pass"

    def test_回放得分即历史得分(self, storyboard_unbiased_fixture):
        """回放只读历史节点得分（不重算、不渲染）——序列逐位相等。"""
        real, replay = storyboard_unbiased_fixture
        assert replay == real


class Test注入偏差百分之百拒绝:
    """篡改评估器版本口径/对齐口径/judge 胜率/排序的回放序列必须 100% 被拒（C15）。"""

    def _variants(self, real):
        rng = random.Random(20260920)
        shuffled = real[:]
        rng.shuffle(shuffled)
        drop_top = real[:]
        for i in range(len(real) - 3, len(real)):  # 最高分被压低（reward hacking 形态）
            drop_top[i] = 0.01
        return [
            ("reverse_逆序", real, list(reversed(real))),
            ("shuffle_乱序", real, shuffled),
            ("drop_top_篡改得分", real, drop_top),
            # 篡改评估器版本 → 对齐口径反转（score = 1 − 原分），排序整体颠倒
            ("tampered_alignment_口径反转", real, [round(1.0 - s, 6) for s in real]),
            # 篡改 judge 胜率 → 全部记为平局 0.5（排序信息归零）
            ("tampered_judge_胜率抹平", real, [0.5] * len(real)),
            # 篡改对齐口径（cos_floor 放宽）→ 低分节点被抬到满分（取舍被伪造）
            ("tampered_alignment_放宽抬分", real, [1.0 if s < 0.75 else s for s in real]),
        ]

    def test_全部偏差形态被拒绝(self, storyboard_unbiased_fixture):
        real, _ = storyboard_unbiased_fixture
        variants = self._variants(real)
        assert len(variants) >= 3
        for name, real_seq, replay_seq in variants:
            report = verify_unbiasedness(real_seq, replay_seq, threshold=_TAU_THRESHOLD)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < 0.95
