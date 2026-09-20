"""声音回放无偏性验收（功能 006 / T622，先于实现编写，发布阻塞门禁 C14）。

声音夹具池（≥2 棵不同时间树）：回放打分（SimulatorPool probe 揭示的历史得分）
vs 真实重跑（模拟生成器重执行 + 真实四评估器 + 合成定点）得分序列
Kendall τ ≥ 0.95 放行；注入偏差（篡改评估器版本口径/得分）100% 拒绝。
复用 core/replay/unbiasedness.py 口径（002 同套件）。
"""

import random
from pathlib import Path

import pytest
import yaml

from agents.sound.audio import decode_wav_samples, synthesize_wav
from agents.sound.evaluators import build_sound_evaluators
from agents.sound.evaluators.composite import composite_sound
from agents.sound.loop import _weights
from core.evaluators.base import ArtifactRef
from core.evaluators.quantize import quantize_score
from core.replay.pool import SimulatorPool
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.models import CostRecord, NodeStatus, new_id

pytestmark = pytest.mark.unbiasedness

REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
_SOUND = _CONFIG["sound"]
_DIST = _SOUND["simulated_gen"]
_SR = int(_SOUND["sample_rate"])
_TAU_THRESHOLD = _CONFIG["replay"]["unbiasedness_tau_threshold"]

# 夹具参数序列：cer 梯度驱动得分区分度（asr 映射 1 − cer/0.2）
_CER_LADDER = (0.0, 0.05, 0.1, 0.15)


def _real_rerun_score(params, timing_sheet, sound_config):
    """真实重跑：模拟生成器重执行 + 真实四评估器 + 合成定点（确定性）。"""
    wav_bytes, metadata = synthesize_wav(params, _DIST, _SR)
    ctx = {
        "samples": decode_wav_samples(wav_bytes),
        "sample_rate": _SR,
        "gen_type": params["gen_type"],
        "metadata": metadata,
        "gen_params": params,
        "timing_sheet": timing_sheet,
    }
    breakdown = {}
    artifact = ArtifactRef(artifact_hash="ab" * 32)
    for evaluator in build_sound_evaluators(sound_config):
        result = evaluator.evaluate(artifact, ctx)
        breakdown[evaluator.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    return quantize_score(composite_sound(breakdown, _weights(sound_config)))


def _calibrated_params(make_sound_gen_params, make_timing_sheet, sound_config, seed, cer):
    """TTS 参数：响度标定到对白档（gate 通过）+ cer 注入（得分区分度来源）。"""
    from agents.sound.evaluators.loudness import measure_loudness_lufs

    base = make_sound_gen_params(gen_type="tts", seed=seed, cer_injected=cer)
    wav0, _ = synthesize_wav({**base, "loudness_gain_db": 0.0}, _DIST, _SR)
    gain = float(_SOUND["loudness"]["dialogue"]["target_lufs"]) - measure_loudness_lufs(
        decode_wav_samples(wav0), _SR
    )
    return {**base, "loudness_gain_db": gain}


def _sound_pool(tree_store, make_tree, make_node, params_seq, real_scores):
    """≥2 棵不同时间声音树（uuid7 tree_id 时间有序）：节点得分 = 真实重跑值。"""
    trees = []
    per_tree = len(params_seq) // 2
    for t in range(2):
        tree = make_tree(agent_id="sound", project_id="sound-unbiased")
        tree_store.create_tree(tree)
        root = make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            agent_id="sound",
            eval_breakdown={},
            score=0.0,
            status=NodeStatus.EVALUATED,
            created_at=float(t * 100),
        )
        tree_store.append_node(root)
        for i in range(per_tree):
            idx = t * per_tree + i
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="sound",
                    observation_context={"gen_params": params_seq[idx]},
                    score=real_scores[idx],
                    cost=CostRecord(generation_api_calls=1),
                    status=NodeStatus.EVALUATED,
                    created_at=float(t * 100 + 1 + i),
                )
            )
        trees.append(tree)
    return trees


def _replay_scores(trees, tree_store, params_seq):
    """回放打分：池化模拟器按参数序列 probe，收集揭示得分（无匹配 UNKNOWN 不得分）。"""
    from policies.base import Budget

    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    simulator = pool.build(
        worker_count=1, budget=Budget(max_probes=len(params_seq) * 2), latency_quantum_ms=0
    )
    scores = []
    roots = {tree.tree_id: tree.root_id for tree in trees}
    per_tree = len(params_seq) // 2
    for idx, params in enumerate(params_seq):
        root_id = roots[trees[idx // per_tree].tree_id]
        result = simulator.probe(root_id, params)
        assert result.status == "ok", f"参数 {params} 未命中（回放精确匹配被破坏）"
        scores.append(result.nodes[0].score)
    return scores


@pytest.fixture()
def sound_unbiased_fixture(
    tree_store, make_tree, make_node, make_sound_gen_params, make_timing_sheet, sound_config
):
    """声音无偏性夹具：参数序列 + 真实重跑序列 + 池化回放序列。"""
    timing_sheet = make_timing_sheet()
    params_seq = [
        _calibrated_params(make_sound_gen_params, make_timing_sheet, sound_config, seed, cer)
        for seed, cer in enumerate(_CER_LADDER * 2, start=1)
    ]
    real = [_real_rerun_score(params, timing_sheet, sound_config) for params in params_seq]
    trees = _sound_pool(tree_store, make_tree, make_node, params_seq, real)
    replay = _replay_scores(trees, tree_store, params_seq)
    return real, replay


class Test声音无偏性放行:
    def test_回放对真实重跑_tau达标(self, sound_unbiased_fixture):
        real, replay = sound_unbiased_fixture
        assert len(real) >= 8  # ≥2 棵树 × 4 参数
        report = verify_unbiasedness(real, replay, threshold=_TAU_THRESHOLD)
        assert report.verdict == "pass"
        assert report.tau >= 0.95

    def test_门槛取_configs_口径(self, sound_unbiased_fixture):
        assert _TAU_THRESHOLD == 0.95
        real, replay = sound_unbiased_fixture
        assert verify_unbiasedness(real, replay).verdict == "pass"  # 默认门槛同口径


class Test注入偏差百分之百拒绝:
    """篡改评估器版本口径/得分的回放序列必须 100% 被拒（C14/SC-002 同口径）。"""

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
            # 篡改评估器版本 → CER 映射口径反转（score = cer/cap），排序整体颠倒
            (
                "tampered_version_口径反转",
                real,
                [round(1.0 - score, 6) for score in real],
            ),
        ]

    def test_全部偏差形态被拒绝(self, sound_unbiased_fixture):
        real, _ = sound_unbiased_fixture
        variants = self._variants(real)
        assert len(variants) >= 4
        for name, real_seq, replay_seq in variants:
            report = verify_unbiasedness(real_seq, replay_seq, threshold=_TAU_THRESHOLD)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < 0.95
