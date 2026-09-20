#!/usr/bin/env python
"""端到端演示：分镜线上探索闭环（quickstart.md 六步，功能 008 收官验证）。

演示（确定性模拟渲染器 + SQLite 内存库 + configs/movie.yaml，全程离线无需凭证）：
  1. 一轮分镜：夹具剧本（3 场景 9 行含关键行）→ 3 组 ShotList（champion 网格工艺）
     → 3 预演落树，成本入账（镜头数 × 价目）+ 三方对账一致
  2. ShotList 执行前校验：四类非法（引用不存在行/场景无镜头/关键行未承接/档位越界）
     → 拒绝、0 渲染 0 成本
  3. 预算门禁与幂等：小预算超额拒绝、已执行照常入账；同 round_id 重建
     0 重复渲染 0 重复扣费
  4. 评估：五评估器分量齐全 + gate 短路（景别跳跃违规判 0，judge 0 调用）
     + 定点归一重算逐位一致
  5. 无偏性：回放打分 vs 真实重跑（模拟渲染器重执行 + 五评估器重算）
     Kendall τ ≥ 0.95
  6. 做梦：champion 策略一轮做梦（演示档 M=8）→ reward 排名 + 首轮基线落盘

断言：六步全过 ok=true，退出码 0；生产环境切换仅装配层替换（PG/S3/真实预演服务/
LLM 网关），代码路径不变（同 004/006/007 演示纪律）。
"""

import importlib.util
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from agents.storyboard.config import StoryboardConfig  # noqa: E402
from agents.storyboard.db import create_render_jobs_schema  # noqa: E402
from agents.storyboard.evaluators import build_storyboard_evaluators  # noqa: E402
from agents.storyboard.evaluators.composite import (  # noqa: E402
    composite_storyboard,
    evaluate_storyboard,
)
from agents.storyboard.loop import freeze_round_tree, run_storyboard_round  # noqa: E402
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer  # noqa: E402
from agents.storyboard.script import ScriptSegment  # noqa: E402
from agents.storyboard.shotlist import ShotList  # noqa: E402
from core.evaluators.base import ArtifactRef  # noqa: E402
from core.evaluators.quantize import quantize_score  # noqa: E402
from core.llm_gateway.backends.mock import MockBackend  # noqa: E402
from core.llm_gateway.gateway import LLMGateway  # noqa: E402
from core.replay.pool import SimulatorPool  # noqa: E402
from core.replay.unbiasedness import verify_unbiasedness  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from dreaming.candidates import MutatorGenerator  # noqa: E402
from dreaming.config import DreamConfig  # noqa: E402
from dreaming.pipeline import in_process_replay, run_dream_round  # noqa: E402
from policies.base import Budget  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
STORYBOARD_POLICY_DIR = REPO_ROOT / "policies" / "history" / "storyboard"
MODEL_PRICES = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


def _config() -> StoryboardConfig:
    """演示配置：真实 movie.yaml + 渲染尺寸缩小提速（形态参数不变）。"""
    import copy

    import yaml

    raw = copy.deepcopy(yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8")))
    raw["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(raw)


def _script() -> ScriptSegment:
    """夹具剧本：3 场景 9 行（含关键行标注 + 情绪基调 + 轴向基准）。"""
    return ScriptSegment(
        scenes=[
            {
                "scene_id": "scene-1",
                "axis_base": "A",
                "lines": [
                    {
                        "line_id": "s1-l1",
                        "kind": "dialogue",
                        "text": "夜里三点，走廊的灯一闪一闪。",
                        "emotion": "tense",
                    },
                    {
                        "line_id": "s1-l2",
                        "kind": "action",
                        "text": "她握紧门把手，缓缓推开。",
                        "key": True,
                        "emotion": "tense",
                    },
                    {"line_id": "s1-l3", "kind": "dialogue", "text": "有人在吗？"},
                ],
            },
            {
                "scene_id": "scene-2",
                "axis_base": "A",
                "lines": [
                    {
                        "line_id": "s2-l1",
                        "kind": "dialogue",
                        "text": "我不该回来的。",
                        "key": True,
                        "emotion": "sorrow",
                    },
                    {
                        "line_id": "s2-l2",
                        "kind": "action",
                        "text": "窗外的雨敲打着玻璃。",
                        "emotion": "sorrow",
                    },
                    {
                        "line_id": "s2-l3",
                        "kind": "dialogue",
                        "text": "但你回来了。",
                        "emotion": "calm",
                    },
                ],
            },
            {
                "scene_id": "scene-3",
                "axis_base": "B",
                "lines": [
                    {
                        "line_id": "s3-l1",
                        "kind": "action",
                        "text": "两人隔着长长的走廊对视。",
                        "emotion": "awe",
                    },
                    {
                        "line_id": "s3-l2",
                        "kind": "dialogue",
                        "text": "这一次，我不会再走。",
                        "emotion": "joyful",
                    },
                    {
                        "line_id": "s3-l3",
                        "kind": "action",
                        "text": "镜头缓缓拉远，灯光暗下。",
                        "key": True,
                        "emotion": "sorrow",
                    },
                ],
            },
        ]
    )


def _champion() -> tuple[str, str, list[dict]]:
    """champion 单源加载：policies/history/storyboard/ 内唯一 {version}.py。"""
    path = next(STORYBOARD_POLICY_DIR.glob("*.py"))
    source = path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("storyboard_champion", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path.stem, source, module._GRID


def _illegal_shotlists(grid: list[dict]) -> list[dict]:
    """四类非法 ShotList（quickstart 步骤 2，执行前三层校验各拒绝一类）。"""
    base = json.loads(json.dumps(grid[0], ensure_ascii=False))
    unknown_line = json.loads(json.dumps(base, ensure_ascii=False))
    unknown_line["shots"][1]["covers"] = ["s1-l9"]  # 引用不存在的剧本行
    scene_uncovered = json.loads(json.dumps(base, ensure_ascii=False))
    scene_uncovered["shots"] = [s for s in scene_uncovered["shots"] if s["scene_id"] != "scene-2"]
    key_line_uncovered = json.loads(json.dumps(base, ensure_ascii=False))
    key_line_uncovered["shots"] = [
        s for s in key_line_uncovered["shots"] if s["shot_id"] != "shot-04"
    ]  # scene-2 仍有镜头，但关键行 s2-l1 无承接
    size_out_of_range = json.loads(json.dumps(base, ensure_ascii=False))
    size_out_of_range["shots"][4]["shot_size"] = "extreme_wide"  # 档位越界
    return [unknown_line, scene_uncovered, key_line_uncovered, size_out_of_range]


class _DemoPolicy:
    def __init__(self, shotlists, version="demo-storyboard-v1"):
        self._shotlists = shotlists
        self.policy_version = version

    def plan(self, config, inputs):
        return list(self._shotlists)


class _CountingRenderer:
    """调用计数包装（幂等/拒绝路径 0 渲染断言用）。"""

    def __init__(self, inner):
        self._inner = inner
        self.render_calls = 0

    def estimate(self, shotlist, cfg):
        return self._inner.estimate(shotlist, cfg)

    def render(self, shotlist, script, cfg):
        self.render_calls += 1
        return self._inner.render(shotlist, script, cfg)


def _gateway() -> LLMGateway:
    return LLMGateway(MockBackend(), price_book=MODEL_PRICES, sleep=lambda _: None)


def _real_rerun_score(shotlist, script, config) -> float:
    """真实重跑：模拟渲染器重执行 + 真实五评估器编排 + 合成定点（无偏性对照侧）。"""
    animatic = SimulatedStoryboardRenderer().render(shotlist, script, config)
    artifact = ArtifactRef(artifact_hash="ab" * 32, metadata=animatic.metadata)
    ctx = {"shotlist": shotlist, "script": script}
    _, score, _ = evaluate_storyboard(
        build_storyboard_evaluators(config, _gateway()),
        artifact,
        ctx,
        config.evaluator_weights,
    )
    return score


def _dream_pool(store, grid) -> SimulatorPool:
    """做梦演示池：3 棵不同时间树（champion ShotList 网格 gen_params，逐树得分递增）。"""
    pool = SimulatorPool(store)
    for t in range(3):
        tree_id, root_id = new_id(), new_id()
        tree = DiscoveryTree(
            tree_id=tree_id,
            project_id="storyboard",
            agent_id="storyboard",
            policy_version="demo-champion",
            root_id=root_id,
            node_ids=[],
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "shotlist", "shotlist_hash", "job_id"],
            },
        )
        store.create_tree(tree)
        store.append_node(
            TreeNode(
                node_id=root_id,
                tree_id=tree_id,
                parent_id=None,
                depth=0,
                agent_id="storyboard",
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
        for i, shotlist_dict in enumerate(grid):
            store.append_node(
                TreeNode(
                    node_id=new_id(),
                    tree_id=tree_id,
                    parent_id=root_id,
                    depth=1,
                    agent_id="storyboard",
                    policy_version="demo-champion",
                    prompt="",
                    observation_context={
                        "gen_params": shotlist_dict,  # 回放匹配槽（与执行器落盘形态一致）
                        "shotlist": shotlist_dict,
                        "shotlist_hash": ShotList.from_dict(shotlist_dict).shotlist_hash(),
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


def main() -> int:
    started = time.perf_counter()
    config = _config()
    dream_config = DreamConfig.from_yaml(MOVIE_YAML)
    script = _script()
    inputs = {"script": script}
    version, champion_source, grid = _champion()
    report: dict = {"champion_version": version, "steps": {}, "ok": False}

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    create_render_jobs_schema(engine)
    store = create_tree_store(engine)

    with tempfile.TemporaryDirectory(prefix="cineflow-storyboard-demo-") as tmp:
        artifacts = LocalArtifactStore(Path(tmp) / "artifacts")
        shotlists = [ShotList.from_dict(item) for item in grid]
        policy = _DemoPolicy(grid)
        gateway = _gateway()

        # ---- 步骤 1：一轮分镜（3 组 ShotList 落树 + 成本入账 + 对账）----
        renderer = _CountingRenderer(SimulatedStoryboardRenderer())
        result = run_storyboard_round(
            round_id="demo-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapter=renderer,
            engine=engine,
            config=config,
            inputs=inputs,
            evaluators=None,  # 默认装配真实五评估器（T827 接线形态）
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
        report["steps"]["1_一轮分镜落树对账"] = step1

        # ---- 步骤 2：非法 ShotList 执行前拒绝（0 渲染 0 成本）----
        illegal_renderer = _CountingRenderer(SimulatedStoryboardRenderer())
        illegal = run_storyboard_round(
            round_id="demo-r2",
            policy=_DemoPolicy(_illegal_shotlists(grid), version="demo-storyboard-bad-v1"),
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
            step2["jobs"] == ["rejected"] * 4
            and illegal_renderer.render_calls == 0
            and illegal.spent_usd == 0.0
        )
        report["steps"]["2_非法ShotList零渲染零成本"] = step2

        # ---- 步骤 3：预算门禁与幂等 ----
        first_estimate = SimulatedStoryboardRenderer().estimate(shotlists[0], config)
        small = replace(config, exploration_per_round_usd=round(first_estimate * 1.1, 2))
        budget_renderer = _CountingRenderer(SimulatedStoryboardRenderer())
        budget_result = run_storyboard_round(
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
        rebuilt = run_storyboard_round(
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
            quantize_score(composite_storyboard(n.eval_breakdown, config.evaluator_weights))
            == n.score
            for n in nodes
        )
        breakdown_sizes = sorted({len(n.eval_breakdown) for n in nodes})
        # gate 短路演示：景别相邻跳跃 3 > max_size_jump 2 → 语法 gate 判 0，judge 0 调用
        gate_gateway = _gateway()
        gate_shots = json.loads(json.dumps(grid[0], ensure_ascii=False))
        gate_shots["shots"][1]["shot_size"] = "wide"  # close_up → wide 跳跃 3
        gate_result = run_storyboard_round(
            round_id="demo-r4",
            policy=_DemoPolicy([gate_shots], version="demo-storyboard-gate-v1"),
            store=store,
            artifacts=artifacts,
            adapter=SimulatedStoryboardRenderer(),
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
            next(t for t in store.trees_by(agent_id="storyboard") if t.tree_id == result.tree_id)
        )
        simulator = pool.build(worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0)
        root_id = store.get_node(f"{result.tree_id}-root").node_id
        replay_scores = []
        for shotlist in shotlists:
            probe = simulator.probe(root_id, shotlist.to_dict())
            assert probe.status == "ok"
            replay_scores.append(probe.nodes[0].score)
        real_scores = [_real_rerun_score(sl, script, config) for sl in shotlists]
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
        dream_pool = _dream_pool(store, grid)
        history_root = Path(tmp) / "dreaming" / "history"
        dream_round = run_dream_round(
            "storyboard",
            champion_source,
            MutatorGenerator("storyboard-demo-seed"),
            dream_pool,
            None,
            dream_config,
            replay_fn=in_process_replay,
            history_root=history_root,
            m=dream_config.demo_candidates,
        )
        baseline = history_root / "storyboard" / "dream-storyboard-1.json"
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


if __name__ == "__main__":
    sys.exit(main())
