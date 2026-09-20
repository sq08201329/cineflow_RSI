"""剪辑回放无偏性验收（功能 007 / T728，先于实现编写，发布阻塞门禁 C15）。

剪辑夹具池（2 棵不同时间树 × 4 EDL）：回放打分（SimulatorPool probe 揭示的
历史得分，EDL 规范化精确匹配）vs 真实重跑（模拟渲染器重执行 + 真实五评估器
+ 合成定点）得分序列 Kendall τ ≥ 0.95 放行；注入偏差（篡改评估器版本口径/
judge 胜率/节奏口径/排序）100% 拒绝。复用 core/replay/unbiasedness.py（002 同套件）。
"""

import copy
import random
from pathlib import Path

import pytest
import yaml

from agents.editing.config import EditingConfig
from agents.editing.edl import EditDecisionList
from agents.editing.evaluators import build_editing_evaluators
from agents.editing.evaluators.composite import evaluate_editing
from agents.editing.platform.simulated import SimulatedEditRenderer
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


def _config_dict() -> dict:
    """无偏性夹具配置：时长窗口放宽（全部 EDL 过 gate），渲染尺寸缩小提速。"""
    config = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    )
    config["editing"]["target_duration_s"] = 10
    config["editing"]["duration_tolerance_s"] = 15
    config["editing"]["render"].update(width=64, height=48)
    return config


def _edl_ladder() -> list[EditDecisionList]:
    """8 组合法 EDL 梯度（镜头时长阶梯 → pacing/judge 得分区分度来源）。

    分区顺序 a→b→c 不降、跨区 cut、出入点在镜头时长内（shot-1/3/5 分别 4000/5000/6000ms）。
    """
    ladder = []
    lengths = [
        (1000, 4000, 2000),
        (1500, 3500, 2500),
        (2000, 3000, 3000),
        (2500, 2500, 3500),
        (1000, 3000, 4000),
        (2000, 4500, 1500),
        (3000, 2000, 2500),
        (1200, 3800, 3200),
    ]
    for i, (a, b, c) in enumerate(lengths):
        ladder.append(
            EditDecisionList(
                clips=[
                    {
                        "shot_id": "shot-1",
                        "in_ms": 0,
                        "out_ms": a,
                        "transition": {"type": "cut", "duration_ms": 0},
                    },
                    {
                        "shot_id": "shot-3",
                        "in_ms": 0,
                        "out_ms": b,
                        "transition": {"type": "cut", "duration_ms": 0},
                    },
                    {
                        "shot_id": "shot-5",
                        "in_ms": 0,
                        "out_ms": c,
                        "transition": {"type": "cut", "duration_ms": 0},
                    },
                ],
                audio=[{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.8}] if i % 2 else [],
            )
        )
    return ladder


def _real_rerun_score(edl, library, structure, config) -> float:
    """真实重跑：模拟渲染器重执行 + 真实五评估器编排 + 合成定点（确定性）。"""
    adapter = SimulatedEditRenderer(config.render)
    film = adapter.render(edl, library)
    gateway = LLMGateway(MockBackend(), price_book=_MODEL_PRICES, sleep=lambda _: None)
    artifact = ArtifactRef(artifact_hash="ab" * 32, metadata=film.metadata)
    ctx = {"edl": edl, "shot_library": library, "scene_structure": structure}
    _, score, _ = evaluate_editing(
        build_editing_evaluators(config, gateway),
        artifact,
        ctx,
        config.evaluator_weights,
    )
    return score


def _editing_pool(tree_store, make_tree, make_node, edls, real_scores):
    """2 棵不同时间剪辑树：子节点 gen_params = EDL 规范化结构，得分 = 真实重跑值。"""
    trees = []
    per_tree = len(edls) // 2
    for t in range(2):
        tree = make_tree(
            agent_id="editing",
            project_id="editing-unbiased",
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "edl_hash", "job_id"],
            },
        )
        tree_store.create_tree(tree)
        tree_store.append_node(
            make_node(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                agent_id="editing",
                eval_breakdown={},
                score=0.0,
                status=NodeStatus.EVALUATED,
                created_at=float(t * 100),
            )
        )
        for i in range(per_tree):
            idx = t * per_tree + i
            edl_dict = edls[idx].to_dict()
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="editing",
                    observation_context={
                        "gen_params": edl_dict,  # 回放匹配槽（002 规范化精确匹配键）
                        "edl": edl_dict,
                        "edl_hash": edls[idx].edl_hash(),
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


def _replay_scores(trees, tree_store, edls):
    """回放打分：池化模拟器按 EDL 序列 probe，收集揭示得分（无匹配 UNKNOWN 不得分）。"""
    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    simulator = pool.build(
        worker_count=1, budget=Budget(max_probes=len(edls) * 2), latency_quantum_ms=0
    )
    scores = []
    per_tree = len(edls) // 2
    roots = {tree.tree_id: tree.root_id for tree in trees}
    for idx, edl in enumerate(edls):
        result = simulator.probe(roots[trees[idx // per_tree].tree_id], edl.to_dict())
        assert result.status == "ok", f"EDL {idx} 未命中（回放精确匹配被破坏）"
        scores.append(result.nodes[0].score)
    return scores


@pytest.fixture()
def editing_unbiased_fixture(
    tree_store, make_tree, make_node, make_shot_library, make_scene_structure
):
    """剪辑无偏性夹具：EDL 梯度序列 + 真实重跑序列 + 池化回放序列。"""
    library = make_shot_library()
    structure = make_scene_structure(library=library)
    config = EditingConfig.from_dict(_config_dict())
    edls = _edl_ladder()
    real = [_real_rerun_score(edl, library, structure, config) for edl in edls]
    assert len(set(real)) >= 6, f"得分区分度不足（梯度失效）：{real}"
    trees = _editing_pool(tree_store, make_tree, make_node, edls, real)
    replay = _replay_scores(trees, tree_store, edls)
    return real, replay


class Test剪辑无偏性放行:
    def test_回放对真实重跑_tau达标(self, editing_unbiased_fixture):
        real, replay = editing_unbiased_fixture
        assert len(real) == 8  # 2 棵树 × 4 EDL
        report = verify_unbiasedness(real, replay, threshold=_TAU_THRESHOLD)
        assert report.verdict == "pass"
        assert report.tau >= 0.95

    def test_门槛取_configs_口径(self, editing_unbiased_fixture):
        assert _TAU_THRESHOLD == 0.95
        real, replay = editing_unbiased_fixture
        assert verify_unbiasedness(real, replay).verdict == "pass"


class Test注入偏差百分之百拒绝:
    """篡改评估器版本口径/judge 胜率/节奏口径的回放序列必须 100% 被拒（C15）。"""

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
            # 篡改评估器版本 → 节奏口径反转（score = 1 − 原分），排序整体颠倒
            ("tampered_pacing_口径反转", real, [round(1.0 - s, 6) for s in real]),
            # 篡改 judge 胜率 → 全部记为平局 0.5（排序信息归零）
            ("tampered_judge_胜率抹平", real, [0.5] * len(real)),
        ]

    def test_全部偏差形态被拒绝(self, editing_unbiased_fixture):
        real, _ = editing_unbiased_fixture
        variants = self._variants(real)
        assert len(variants) >= 3
        for name, real_seq, replay_seq in variants:
            report = verify_unbiasedness(real_seq, replay_seq, threshold=_TAU_THRESHOLD)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < 0.95
