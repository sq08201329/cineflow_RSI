#!/usr/bin/env python
"""端到端演示：声音线上探索闭环（quickstart.md 六步，功能 006 收官验证）。

演示（确定性模拟生成器 + SQLite 内存库 + configs/movie.yaml，全程离线无需凭证）：
  1. 一轮探索：4 组参数（2 TTS + 1 SFX + 1 music）→ 4 wav 工件落树，
     成本按 gen_type 分账齐全
  2. 预算门禁：小预算上限（$1.0）下超额申请拒绝，已执行照常入账
  3. 幂等：同 round_id 二次触发 → 首轮结果重建（0 重复生成、0 重复扣费）
  4. 评估：四评估器分量 + gate 短路（响度违规总分 0）+ 定点归一重算逐位一致
  5. 无偏性：回放打分 vs 真实重跑（模拟生成器重执行）Kendall τ ≥ 0.95
  6. 做梦：champion 策略一轮做梦（演示档 M=8）→ reward 排名 + 首轮基线落盘

断言：六步全过 ok=true，退出码 0；生产环境切换仅装配层替换（PG/S3/真实
适配器/LLM 网关），代码路径不变（同 004 演示纪律）。
"""

import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from agents.sound.audio import decode_wav_samples, synthesize_wav  # noqa: E402
from agents.sound.config import SoundConfig  # noqa: E402
from agents.sound.db import create_gen_jobs_schema  # noqa: E402
from agents.sound.evaluators import build_sound_evaluators  # noqa: E402
from agents.sound.evaluators.composite import composite_sound  # noqa: E402
from agents.sound.evaluators.loudness import measure_loudness_lufs  # noqa: E402
from agents.sound.loop import _weights, freeze_round_tree, run_sound_round  # noqa: E402
from agents.sound.platform.simulated import (  # noqa: E402
    SimulatedMusicGen,
    SimulatedSFXGen,
    SimulatedTTSGen,
)
from agents.sound.timing import TimingSheet  # noqa: E402
from core.evaluators.base import ArtifactRef  # noqa: E402
from core.evaluators.quantize import quantize_score  # noqa: E402
from core.replay.pool import SimulatorPool  # noqa: E402
from core.replay.unbiasedness import verify_unbiasedness  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import CostRecord, NodeStatus, new_id  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from dreaming.candidates import MutatorGenerator  # noqa: E402
from dreaming.config import DreamConfig  # noqa: E402
from dreaming.pipeline import in_process_replay, run_dream_round  # noqa: E402
from policies.base import Budget  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
TIMING_SHEET = TimingSheet(
    utterances=[
        {"text": "你终于来了。", "start_ms": 0, "end_ms": 1000},
        {"text": "我等了很久。", "start_ms": 1200, "end_ms": 2000},
    ],
    effects=[{"kind": "door_slam", "at_ms": 1100}],
)
INPUTS = {"timing_sheet": TIMING_SHEET, "mood": "悬疑"}


def _calibrated(config: SoundConfig, gen_type: str, seed: int, **extra) -> dict:
    """演示参数：响度标定到分档目标（gate 通过），附加属性可控。"""
    tier = {"tts": "dialogue", "sfx": "sfx", "music": "music"}[gen_type]
    base = {
        "gen_type": gen_type,
        "seed": seed,
        "duration_s": 2.0,
        "event_times_ms": [0.0, 1000.0],
        "cer_injected": 0.0,
        "emotion_vector": [0.5, 0.5],
        **extra,
    }
    wav0, _ = synthesize_wav(
        {**base, "loudness_gain_db": 0.0}, config.simulated_gen, config.sample_rate
    )
    gain = float(config.loudness[tier]["target_lufs"]) - measure_loudness_lufs(
        decode_wav_samples(wav0), config.sample_rate
    )
    return {**base, "loudness_gain_db": gain}


class DemoSoundPolicy:
    """演示策略：4 组参数（2 TTS + 1 SFX + 1 music，C1 场景 1 配比）。"""

    policy_version = "demo-sound-v1"

    def __init__(self, config: SoundConfig):
        self._plans = [
            {"gen_type": "tts", "gen_params": _calibrated(config, "tts", 11, cer_injected=0.0)},
            {"gen_type": "tts", "gen_params": _calibrated(config, "tts", 22, cer_injected=0.1)},
            {"gen_type": "sfx", "gen_params": _calibrated(config, "sfx", 33)},
            {"gen_type": "music", "gen_params": _calibrated(config, "music", 44)},
        ]

    def plan(self, config, inputs):
        return self._plans


class _CountingAdapter:
    """调用计数包装（幂等 0 重复生成断言用）。"""

    def __init__(self, inner):
        self._inner = inner
        self.gen_type = inner.gen_type
        self.generate_calls = 0

    def estimate(self, params):
        return self._inner.estimate(params)

    def generate(self, params):
        self.generate_calls += 1
        return self._inner.generate(params)


def _adapters(config: SoundConfig, counting: bool = False) -> dict:
    adapters = {
        "tts": SimulatedTTSGen(config.simulated_gen, config.sample_rate),
        "sfx": SimulatedSFXGen(config.simulated_gen, config.sample_rate),
        "music": SimulatedMusicGen(config.simulated_gen, config.sample_rate),
    }
    return {k: _CountingAdapter(a) for k, a in adapters.items()} if counting else adapters


def _real_rerun_score(params, config: SoundConfig) -> float:
    """真实重跑：模拟生成器重执行 + 真实四评估器 + 合成定点（无偏性对照侧）。"""
    wav_bytes, metadata = synthesize_wav(params, config.simulated_gen, config.sample_rate)
    ctx = {
        "samples": decode_wav_samples(wav_bytes),
        "sample_rate": config.sample_rate,
        "gen_type": params["gen_type"],
        "metadata": metadata,
        "gen_params": params,
        "timing_sheet": TIMING_SHEET,
    }
    breakdown = {}
    artifact = ArtifactRef(artifact_hash="ab" * 32)
    for evaluator in build_sound_evaluators(config):
        result = evaluator.evaluate(artifact, ctx)
        breakdown[evaluator.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    return quantize_score(composite_sound(breakdown, _weights(config)))


def main() -> int:
    started = time.perf_counter()
    config = SoundConfig.from_yaml(MOVIE_YAML)
    dream_config = DreamConfig.from_yaml(MOVIE_YAML)
    report: dict = {"steps": {}, "ok": False}

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    create_gen_jobs_schema(engine)
    store = create_tree_store(engine)

    with tempfile.TemporaryDirectory(prefix="cineflow-sound-demo-") as tmp:
        artifacts = LocalArtifactStore(Path(tmp) / "artifacts")
        policy = DemoSoundPolicy(config)
        plans = policy.plan(config, INPUTS)

        # ---- 步骤 1：一轮探索（4 组参数落树 + 按类型分账）----
        counting = _adapters(config, counting=True)
        result = run_sound_round(
            round_id="demo-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapters=counting,
            engine=engine,
            config=config,
            inputs=INPUTS,
        )
        step1 = {
            "jobs": [j["status"] for j in result.jobs],
            "cost_by_type": result.cost_by_type,
            "reconciliation": result.cost_reconciliation,
        }
        step1["ok"] = (
            step1["jobs"] == ["inserted"] * 4
            and result.cost_by_type["tts"] > 0
            and result.cost_by_type["sfx"] > 0
            and result.cost_by_type["music"] > 0
            and result.cost_reconciliation["consistent"]
        )
        report["steps"]["1_一轮探索落树分账"] = step1

        # ---- 步骤 2：预算门禁（$1.0 上限：2 过 2 拒，已执行照常入账）----
        small = replace(config, exploration_per_round_usd=1.0)
        budget_result = run_sound_round(
            round_id="demo-r2",
            policy=DemoSoundPolicy(config),
            store=store,
            artifacts=artifacts,
            adapters=_adapters(config),
            engine=engine,
            config=small,
            inputs=INPUTS,
        )
        step2 = {
            "jobs": [j["status"] for j in budget_result.jobs],
            "spent_usd": budget_result.spent_usd,
        }
        step2["ok"] = step2["jobs"] == ["inserted", "inserted", "rejected", "rejected"] and (
            budget_result.spent_usd == 0.8
        )
        report["steps"]["2_预算超界拒绝入账"] = step2

        # ---- 步骤 3：幂等重建（0 重复生成、0 重复扣费）----
        calls_before = sum(a.generate_calls for a in counting.values())
        rebuilt = run_sound_round(
            round_id="demo-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapters=counting,
            engine=engine,
            config=config,
            inputs=INPUTS,
        )
        calls_after = sum(a.generate_calls for a in counting.values())
        step3 = {
            "regenerated": calls_after - calls_before,
            "jobs_equal": rebuilt.jobs == result.jobs,
            "spent_equal": rebuilt.spent_usd == result.spent_usd,
        }
        step3["ok"] = step3["regenerated"] == 0 and step3["jobs_equal"] and step3["spent_equal"]
        report["steps"]["3_幂等重建零重复"] = step3

        # ---- 步骤 4：四评估器 + gate 短路 + 定点归一重算一致 ----
        weights = _weights(config)
        nodes = [n for n in store.nodes_of(result.tree_id) if n.parent_id is not None]
        recompute_ok = all(
            quantize_score(composite_sound(n.eval_breakdown, weights)) == n.score for n in nodes
        )
        breakdown_sizes = sorted({len(n.eval_breakdown) for n in nodes})
        # gate 短路演示：未标定响度（gain=0 → ≈-14 LUFS，距对白档 -27±2 超限）→ 总分 0
        gate_result = run_sound_round(
            round_id="demo-r3",
            policy=_GateFailPolicy(),
            store=store,
            artifacts=artifacts,
            adapters=_adapters(config),
            engine=engine,
            config=config,
            inputs=INPUTS,
        )
        gate_node = next(n for n in store.nodes_of(gate_result.tree_id) if n.parent_id is not None)
        step4 = {
            "breakdown_sizes": breakdown_sizes,
            "recompute_consistent": recompute_ok,
            "gate_violation_score": gate_node.score,
        }
        step4["ok"] = breakdown_sizes == [4] and recompute_ok and gate_node.score == 0.0
        report["steps"]["4_四评估器gate定点重算"] = step4

        # ---- 步骤 5：无偏性（回放 vs 真实重跑 τ ≥ 0.95）----
        freeze_round_tree("demo-r1", store, engine)
        pool = SimulatorPool(store)
        pool.add_tree(
            next(t for t in store.trees_by(agent_id="sound") if t.tree_id == result.tree_id)
        )
        simulator = pool.build(worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0)
        replay_scores = []
        for plan_item in plans:
            probe = simulator.probe(
                store.get_node(f"{result.tree_id}-root").node_id, plan_item["gen_params"]
            )
            assert probe.status == "ok"
            replay_scores.append(probe.nodes[0].score)
        real_scores = [_real_rerun_score(p["gen_params"], config) for p in plans]
        unbiased = verify_unbiasedness(real_scores, replay_scores)
        step5 = {
            "tau": unbiased.tau,
            "verdict": unbiased.verdict,
            "real": real_scores,
            "replay": replay_scores,
        }
        step5["ok"] = unbiased.verdict == "pass" and unbiased.tau >= 0.95
        report["steps"]["5_无偏性tau"] = step5

        # ---- 步骤 6：做梦首轮基线（champion 策略，演示档 M=8）----
        champion_path = next((REPO_ROOT / "policies" / "history" / "sound").glob("*.py"))
        champion_source = champion_path.read_text(encoding="utf-8")
        dream_pool = _dream_pool(store)
        history_root = Path(tmp) / "dreaming" / "history"
        dream_round = run_dream_round(
            "sound",
            champion_source,
            MutatorGenerator("sound-demo-seed"),
            dream_pool,
            None,
            dream_config,
            replay_fn=in_process_replay,
            history_root=history_root,
            m=dream_config.demo_candidates,
        )
        baseline = history_root / "sound" / "dream-sound-1.json"
        step6 = {
            "candidates": len(dream_round.candidates),
            "winner_version": dream_round.winner_version,
            "zero_generation": dream_round.diagnostics["zero_generation"],
            "baseline": str(baseline.name),
        }
        step6["ok"] = (
            len(dream_round.candidates) == 8
            and dream_round.winner_version is not None
            and dream_round.diagnostics["zero_generation"]
            and baseline.exists()
        )
        report["steps"]["6_做梦首轮基线"] = step6

    report["ok"] = all(step["ok"] for step in report["steps"].values())
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


class _GateFailPolicy:
    """gate 演示策略：响度未标定（gain=0 → 超对白档容差）。"""

    policy_version = "demo-sound-gate-v1"

    def plan(self, config, inputs):
        return [
            {
                "gen_type": "tts",
                "gen_params": {
                    "gen_type": "tts",
                    "seed": 99,
                    "duration_s": 2.0,
                    "loudness_gain_db": 0.0,
                    "event_times_ms": [0.0],
                    "cer_injected": 0.0,
                    "emotion_vector": [0.5, 0.5],
                },
            }
        ]


def _dream_pool(store) -> SimulatorPool:
    """做梦演示池：3 棵不同时间树（champion 网格 gen_params，逐树得分递增）。"""
    import importlib.util

    champion_path = next((REPO_ROOT / "policies" / "history" / "sound").glob("*.py"))
    spec = importlib.util.spec_from_file_location("sound_champion", champion_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    grid = module._GRID

    from core.tree.models import DiscoveryTree, TreeNode

    pool = SimulatorPool(store)
    for t in range(3):
        tree_id = new_id()
        root_id = new_id()
        tree = DiscoveryTree(
            tree_id=tree_id,
            project_id="sound",
            agent_id="sound",
            policy_version="demo-champion",
            root_id=root_id,
            node_ids=[],
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "gen_type", "job_id"],
            },
        )
        store.create_tree(tree)
        store.append_node(
            TreeNode(
                node_id=root_id,
                tree_id=tree_id,
                parent_id=None,
                depth=0,
                agent_id="sound",
                policy_version="demo-champion",
                prompt="",
                observation_context={"gen_params": {}},
                artifact_hash="ab" * 32,
                eval_breakdown={},
                score=0.4,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=float(t * 10),
            )
        )
        for i, params in enumerate(grid):
            store.append_node(
                TreeNode(
                    node_id=new_id(),
                    tree_id=tree_id,
                    parent_id=root_id,
                    depth=1,
                    agent_id="sound",
                    policy_version="demo-champion",
                    prompt="",
                    observation_context={
                        "gen_params": params,
                        "gen_type": params["gen_type"],
                        "job_id": f"demo-j{i}",
                    },
                    artifact_hash="ab" * 32,
                    eval_breakdown={"rule.x@1": {"score": 0.5}},
                    score=0.5 + 0.05 * t + 0.05 * i,
                    cost=CostRecord(llm_calls=1),
                    status=NodeStatus.EVALUATED,
                    created_at=float(t * 10 + 1 + i),
                )
            )
        pool.add_tree(tree)
    return pool


if __name__ == "__main__":
    sys.exit(main())
