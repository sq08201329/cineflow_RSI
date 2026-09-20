#!/usr/bin/env python
"""端到端演示：剪辑线上探索闭环（quickstart.md 六步，功能 007 收官验证）。

演示（确定性模拟渲染器 + SQLite 内存库 + configs/movie.yaml，全程离线无需凭证）：
  1. 一轮剪辑：夹具镜头库（6 镜头 3 分区）→ 3 组 EDL → 3 成片落树，
     成本入账（成片时长 × 价目）+ 两方对账一致
  2. EDL 执行前校验：非法 EDL（越界/跨分区/非法转场）→ 拒绝、0 渲染 0 成本
  3. 预算门禁与幂等：小预算超额拒绝、已执行照常入账；同 round_id 重建
     0 重复渲染 0 重复扣费
  4. 评估：五评估器分量齐全 + gate 短路（含 300ms 镜头判 0，judge 0 调用）
     + 定点归一重算逐位一致
  5. 无偏性：回放打分 vs 真实重跑（模拟渲染器重执行 + 五评估器重算）
     Kendall τ ≥ 0.95
  6. 做梦：champion 策略一轮做梦（演示档 M=8）→ reward 排名 + 首轮基线落盘

断言：六步全过 ok=true，退出码 0；生产环境切换仅装配层替换（PG/S3/真实
适配器/LLM 网关），代码路径不变（同 004/006 演示纪律）。
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

from agents.editing.config import EditingConfig  # noqa: E402
from agents.editing.db import create_render_jobs_schema  # noqa: E402
from agents.editing.edl import EditDecisionList  # noqa: E402
from agents.editing.evaluators import build_editing_evaluators  # noqa: E402
from agents.editing.evaluators.composite import composite_editing, evaluate_editing  # noqa: E402
from agents.editing.loop import freeze_round_tree, run_editing_round  # noqa: E402
from agents.editing.platform.simulated import SimulatedEditRenderer  # noqa: E402
from agents.editing.shots import Scene, SceneStructure, ShotEntry, ShotLibrary  # noqa: E402
from core.evaluators.base import ArtifactRef  # noqa: E402
from core.evaluators.quantize import quantize_score  # noqa: E402
from core.llm_gateway.backends.mock import MockBackend  # noqa: E402
from core.llm_gateway.gateway import LLMGateway  # noqa: E402
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
MODEL_PRICES = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


def _config() -> EditingConfig:
    """演示配置：真实 movie.yaml + 时长窗口收窄到夹具可行域 + 渲染尺寸缩小提速。"""
    config = EditingConfig.from_yaml(MOVIE_YAML)
    return replace(
        config,
        target_duration_s=12.0,
        duration_tolerance_s=8.0,
        render={**config.render, "width": 64, "height": 48},
    )


def _library() -> tuple[ShotLibrary, SceneStructure]:
    """夹具镜头库：6 镜头 3 分区 + 1 音轨（与 conftest 工厂同构）。"""
    shots = [
        ShotEntry(shot_id=f"shot-{i}", artifact_hash=f"{i:064x}", duration_ms=d, scene_id=s)
        for i, (d, s) in enumerate(
            [
                (4000, "scene-a"),
                (4000, "scene-a"),
                (5000, "scene-b"),
                (4000, "scene-b"),
                (6000, "scene-c"),
                (4000, "scene-c"),
            ],
            start=1,
        )
    ]
    library = ShotLibrary(shots=shots, audio_tracks=("bgm-01",))
    structure = SceneStructure(
        scenes=[
            Scene(scene_id="scene-a", shot_ids=("shot-1", "shot-2")),
            Scene(scene_id="scene-b", shot_ids=("shot-3", "shot-4")),
            Scene(scene_id="scene-c", shot_ids=("shot-5", "shot-6")),
        ],
        shot_library=library,
    )
    return library, structure


def _demo_edls() -> list[EditDecisionList]:
    """三组哈希不同的合法 EDL（分区顺序不降、同区叠化、跨区 cut）。"""
    return [
        EditDecisionList(
            clips=[
                {
                    "shot_id": "shot-1",
                    "in_ms": 500,
                    "out_ms": 3500,
                    "transition": {"type": "dissolve", "duration_ms": 750},
                },
                {
                    "shot_id": "shot-2",
                    "in_ms": 0,
                    "out_ms": 3000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-3",
                    "in_ms": 0,
                    "out_ms": 4000,
                    "transition": {"type": "dissolve", "duration_ms": 500},
                },
                {
                    "shot_id": "shot-4",
                    "in_ms": 200,
                    "out_ms": 3200,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-5",
                    "in_ms": 0,
                    "out_ms": 5000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ],
            audio=[{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.8}],
        ),
        EditDecisionList(
            clips=[
                {
                    "shot_id": "shot-3",
                    "in_ms": 0,
                    "out_ms": 4000,
                    "transition": {"type": "dissolve", "duration_ms": 500},
                },
                {
                    "shot_id": "shot-4",
                    "in_ms": 0,
                    "out_ms": 3000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-5",
                    "in_ms": 0,
                    "out_ms": 5000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ],
            audio=[{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.6}],
        ),
        EditDecisionList(
            clips=[
                {
                    "shot_id": "shot-1",
                    "in_ms": 0,
                    "out_ms": 3000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-6",
                    "in_ms": 1000,
                    "out_ms": 4000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ]
        ),
    ]


def _illegal_edls() -> list[EditDecisionList]:
    """三类非法 EDL（quickstart 步骤 2：出点越界/跨分区/非法转场）。"""
    base = _demo_edls()[0].to_dict()
    out_of_bounds = json.loads(json.dumps(base))
    out_of_bounds["clips"][0]["out_ms"] = 4500  # shot-1 时长 4000ms
    cross_partition = json.loads(json.dumps(base))
    cross_partition["clips"][0]["shot_id"] = "shot-orphan"  # 不在镜头库
    illegal_transition = json.loads(json.dumps(base))
    illegal_transition["clips"][0]["transition"] = {"type": "wipe", "duration_ms": 500}
    return [
        EditDecisionList.from_dict(out_of_bounds),
        EditDecisionList.from_dict(cross_partition),
        EditDecisionList.from_dict(illegal_transition),
    ]


class _DemoPolicy:
    def __init__(self, edls, version="demo-editing-v1"):
        self._edls = edls
        self.policy_version = version

    def plan(self, config, inputs):
        return list(self._edls)


class _CountingRenderer:
    """调用计数包装（幂等/拒绝路径 0 渲染断言用）。"""

    def __init__(self, inner):
        self._inner = inner
        self.render_calls = 0

    def estimate(self, edl, shots):
        return self._inner.estimate(edl, shots)

    def render(self, edl, shots):
        self.render_calls += 1
        return self._inner.render(edl, shots)


def _gateway() -> LLMGateway:
    return LLMGateway(MockBackend(), price_book=MODEL_PRICES, sleep=lambda _: None)


def _real_rerun_score(edl, library, structure, config) -> float:
    """真实重跑：模拟渲染器重执行 + 真实五评估器编排 + 合成定点（无偏性对照侧）。"""
    film = SimulatedEditRenderer(config.render).render(edl, library)
    artifact = ArtifactRef(artifact_hash="ab" * 32, metadata=film.metadata)
    ctx = {"edl": edl, "shot_library": library, "scene_structure": structure}
    _, score, _ = evaluate_editing(
        build_editing_evaluators(config, _gateway()),
        artifact,
        ctx,
        config.evaluator_weights,
    )
    return score


def main() -> int:
    started = time.perf_counter()
    config = _config()
    dream_config = DreamConfig.from_yaml(MOVIE_YAML)
    library, structure = _library()
    inputs = {"shot_library": library, "scene_structure": structure}
    report: dict = {"steps": {}, "ok": False}

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    create_render_jobs_schema(engine)
    store = create_tree_store(engine)

    with tempfile.TemporaryDirectory(prefix="cineflow-editing-demo-") as tmp:
        artifacts = LocalArtifactStore(Path(tmp) / "artifacts")
        edls = _demo_edls()
        policy = _DemoPolicy(edls)
        gateway = _gateway()

        # ---- 步骤 1：一轮剪辑（3 组 EDL 落树 + 成本入账 + 对账）----
        renderer = _CountingRenderer(SimulatedEditRenderer(config.render))
        result = run_editing_round(
            round_id="demo-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapter=renderer,
            engine=engine,
            config=config,
            inputs=inputs,
            evaluators=None,  # 默认装配真实五评估器（T727 接线形态）
            gateway=gateway,
        )
        step1 = {
            "jobs": [j["status"] for j in result.jobs],
            "spent_usd": result.spent_usd,
            "reconciliation": result.cost_reconciliation,
        }
        step1["ok"] = (
            step1["jobs"] == ["inserted"] * 3
            and result.spent_usd > 0
            and result.cost_reconciliation["consistent"]
        )
        report["steps"]["1_一轮剪辑落树对账"] = step1

        # ---- 步骤 2：非法 EDL 执行前拒绝（0 渲染 0 成本）----
        illegal_renderer = _CountingRenderer(SimulatedEditRenderer(config.render))
        illegal = run_editing_round(
            round_id="demo-r2",
            policy=_DemoPolicy(_illegal_edls(), version="demo-editing-bad-v1"),
            store=store,
            artifacts=artifacts,
            adapter=illegal_renderer,
            engine=engine,
            config=config,
            inputs=inputs,
            evaluators=None,
            gateway=_gateway(),
        )
        step2 = {
            "jobs": [j["status"] for j in illegal.jobs],
            "render_calls": illegal_renderer.render_calls,
            "spent_usd": illegal.spent_usd,
        }
        step2["ok"] = (
            step2["jobs"] == ["rejected"] * 3
            and illegal_renderer.render_calls == 0
            and illegal.spent_usd == 0.0
        )
        report["steps"]["2_非法EDL零渲染零成本"] = step2

        # ---- 步骤 3：预算门禁与幂等 ----
        small = replace(config, exploration_per_round_usd=0.9)  # 只够第一组（≈$0.84）
        budget_renderer = _CountingRenderer(SimulatedEditRenderer(config.render))
        budget_result = run_editing_round(
            round_id="demo-r3",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapter=budget_renderer,
            engine=engine,
            config=small,
            inputs=inputs,
            evaluators=None,
            gateway=_gateway(),
        )
        calls_before = renderer.render_calls
        rebuilt = run_editing_round(
            round_id="demo-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapter=renderer,
            engine=engine,
            config=config,
            inputs=inputs,
            evaluators=None,
            gateway=_gateway(),
        )
        step3 = {
            "budget_jobs": [j["status"] for j in budget_result.jobs],
            "regenerated": renderer.render_calls - calls_before,
            "jobs_equal": rebuilt.jobs == result.jobs,
            "spent_equal": rebuilt.spent_usd == result.spent_usd,
        }
        step3["ok"] = (
            step3["budget_jobs"] == ["inserted", "rejected", "rejected"]
            and step3["regenerated"] == 0
            and step3["jobs_equal"]
            and step3["spent_equal"]
        )
        report["steps"]["3_预算门禁与幂等"] = step3

        # ---- 步骤 4：五评估器 + gate 短路 + 定点归一重算一致 ----
        nodes = [n for n in store.nodes_of(result.tree_id) if n.parent_id is not None]
        recompute_ok = all(
            quantize_score(composite_editing(n.eval_breakdown, config.evaluator_weights)) == n.score
            for n in nodes
        )
        breakdown_sizes = sorted({len(n.eval_breakdown) for n in nodes})
        # gate 短路演示：含 300ms 镜头（< min_shot_ms 500）→ 分布 gate 判 0，judge 0 调用
        gate_gateway = _gateway()
        gate_edl = EditDecisionList(
            clips=[
                {
                    "shot_id": "shot-1",
                    "in_ms": 0,
                    "out_ms": 300,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-5",
                    "in_ms": 0,
                    "out_ms": 6000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ]
        )
        gate_result = run_editing_round(
            round_id="demo-r4",
            policy=_DemoPolicy([gate_edl], version="demo-editing-gate-v1"),
            store=store,
            artifacts=artifacts,
            adapter=SimulatedEditRenderer(config.render),
            engine=engine,
            config=config,
            inputs=inputs,
            evaluators=None,
            gateway=gate_gateway,
        )
        gate_node = next(n for n in store.nodes_of(gate_result.tree_id) if n.parent_id is not None)
        step4 = {
            "breakdown_sizes": breakdown_sizes,
            "recompute_consistent": recompute_ok,
            "gate_violation_score": gate_node.score,
            "gate_judge_llm_calls": gate_node.cost.llm_calls,
        }
        step4["ok"] = (
            breakdown_sizes == [5]
            and recompute_ok
            and gate_node.score == 0.0
            and gate_node.cost.llm_calls == 0  # gate 短路：judge 未跑
        )
        report["steps"]["4_五评估器gate短路定点重算"] = step4

        # ---- 步骤 5：无偏性（回放 vs 真实重跑 τ ≥ 0.95）----
        freeze_round_tree("demo-r1", store, engine)
        pool = SimulatorPool(store)
        pool.add_tree(
            next(t for t in store.trees_by(agent_id="editing") if t.tree_id == result.tree_id)
        )
        simulator = pool.build(worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0)
        root_id = store.get_node(f"{result.tree_id}-root").node_id
        replay_scores = []
        for edl in edls:
            probe = simulator.probe(root_id, edl.to_dict())
            assert probe.status == "ok"
            replay_scores.append(probe.nodes[0].score)
        real_scores = [_real_rerun_score(e, library, structure, config) for e in edls]
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
        champion_path = next((REPO_ROOT / "policies" / "history" / "editing").glob("*.py"))
        champion_source = champion_path.read_text(encoding="utf-8")
        dream_pool = _dream_pool(store)
        history_root = Path(tmp) / "dreaming" / "history"
        dream_round = run_dream_round(
            "editing",
            champion_source,
            MutatorGenerator("editing-demo-seed"),
            dream_pool,
            None,
            dream_config,
            replay_fn=in_process_replay,
            history_root=history_root,
            m=dream_config.demo_candidates,
        )
        baseline = history_root / "editing" / "dream-editing-1.json"
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


def _dream_pool(store) -> SimulatorPool:
    """做梦演示池：3 棵不同时间树（champion EDL 网格 gen_params，逐树得分递增）。"""
    import importlib.util

    champion_path = next((REPO_ROOT / "policies" / "history" / "editing").glob("*.py"))
    spec = importlib.util.spec_from_file_location("editing_champion", champion_path)
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
            project_id="editing",
            agent_id="editing",
            policy_version="demo-champion",
            root_id=root_id,
            node_ids=[],
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "edl_hash", "job_id"],
            },
        )
        store.create_tree(tree)
        store.append_node(
            TreeNode(
                node_id=root_id,
                tree_id=tree_id,
                parent_id=None,
                depth=0,
                agent_id="editing",
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
        for i, edl_dict in enumerate(grid):
            store.append_node(
                TreeNode(
                    node_id=new_id(),
                    tree_id=tree_id,
                    parent_id=root_id,
                    depth=1,
                    agent_id="editing",
                    policy_version="demo-champion",
                    prompt="",
                    observation_context={
                        "gen_params": edl_dict,  # 回放匹配槽（与执行器落盘形态一致）
                        "edl_hash": f"{i:064x}",
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
