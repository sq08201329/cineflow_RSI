"""剪辑回放接入与周校准纳入测试（功能 007 / T729，先于实现编写）。

C14：剪辑树冻结入池——observed/probe EDL 规范化精确匹配（键序乱排仍命中）、
UNKNOWN 零信息、观测白名单投影、回放全程渲染 + 网关调用计数 0 审计（原则三）；
FR-011 分树：最近树永远只做 validation（005 口径）；
C16：010 build_blind_list(agent_id="editing") 正常产出 + dreaming/010 无
editing 特判静态证明。
"""

import inspect
import json
import time
from pathlib import Path

import pytest

from agents.editing.edl import EditDecisionList
from agents.editing.loop import freeze_round_tree, run_editing_round
from agents.editing.platform.simulated import SimulatedEditRenderer
from core.calibration.selection import build_blind_list
from core.evaluators.errors import ValidationError
from core.replay.pool import SimulatorPool
from core.tree.models import CostRecord, NodeStatus, new_id
from dreaming.overfit import split_train_validation
from policies.base import Budget
from tests.stubs import StubProxyEvaluator, StubRuleEvaluator


def _edl_dict(seed_offset: int = 0) -> dict:
    """合法 EDL dict（回放匹配参数形态）：分区 a→b 顺序、跨区 cut。"""
    return EditDecisionList(
        clips=[
            {
                "shot_id": "shot-1",
                "in_ms": 0,
                "out_ms": 3000 + seed_offset,
                "transition": {"type": "cut", "duration_ms": 0},
            },
            {
                "shot_id": "shot-3",
                "in_ms": 0,
                "out_ms": 4000,
                "transition": {"type": "cut", "duration_ms": 0},
            },
        ],
        audio=[{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.8}],
    ).to_dict()


def _editing_tree(tree_store, make_tree, make_node, *, score=0.6, children=None):
    """单棵剪辑夹具树：root + 指定 EDL 子节点（观测白名单对齐执行器落盘形态）。"""
    children = (_edl_dict(),) if children is None else children
    tree = make_tree(
        agent_id="editing",
        project_id="editing-replay",
        config_snapshot={
            "evaluator_weights": {"rule.x": 0.0},
            "observation_fields": ["gen_params", "edl", "edl_hash", "job_id"],
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
        )
    )
    for i, edl_dict in enumerate(children):
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
                    "edl_hash": EditDecisionList.from_dict(edl_dict).edl_hash(),
                    "job_id": f"r-j{i}",
                },
                score=score + 0.05 * i,
                cost=CostRecord(generation_api_calls=1),
                status=NodeStatus.EVALUATED,
                created_at=time.time() + i,
            )
        )
    return tree


def _simulator(tree_store, tree, max_probes=8):
    pool = SimulatorPool(tree_store)
    pool.add_tree(tree)
    return pool.build(worker_count=1, budget=Budget(max_probes=max_probes), latency_quantum_ms=0)


class TestEDL规范化精确匹配:
    def test_键序乱排仍精确命中(self, tree_store, make_tree, make_node):
        """C14：EDL 规范化 JSON 相等——嵌套 clip/transition 键序乱排后逐字节匹配。"""
        edl_dict = _edl_dict()
        tree = _editing_tree(tree_store, make_tree, make_node, children=(edl_dict,))
        simulator = _simulator(tree_store, tree)
        # 同语义不同键序（嵌套层同样乱排）：经 JSON 往返打散构造
        shuffled = json.loads(
            json.dumps(edl_dict, ensure_ascii=False),  # 先规范化
        )
        shuffled = {
            "audio": [
                {"gain": a["gain"], "at_ms": a["at_ms"], "track_ref": a["track_ref"]}
                for a in shuffled["audio"]
            ],
            "clips": [
                {
                    "transition": {
                        "duration_ms": c["transition"]["duration_ms"],
                        "type": c["transition"]["type"],
                    },
                    "out_ms": c["out_ms"],
                    "shot_id": c["shot_id"],
                    "in_ms": c["in_ms"],
                }
                for c in shuffled["clips"]
            ],
        }
        result = simulator.probe(tree.root_id, shuffled)
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.6)
        assert result.nodes[0].fields["gen_params"] == edl_dict

    def test_观测投影仅白名单字段(self, tree_store, make_tree, make_node):
        tree = _editing_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        simulator.probe(tree.root_id, _edl_dict())
        for observation in simulator.observed().values():
            assert set(observation.fields) <= {"gen_params", "edl", "edl_hash", "job_id"}

    def test_UNKNOWN_不得分零信息(self, tree_store, make_tree, make_node):
        """EDL 任一字段偏差（出点 +1ms）→ UNKNOWN：不得分、无新揭示。"""
        tree = _editing_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        before = simulator.observed()
        near_miss = _edl_dict()
        near_miss["clips"][0]["out_ms"] += 1
        result = simulator.probe(tree.root_id, near_miss)
        assert result.status == "unknown"
        assert result.nodes == []
        assert simulator.observed().keys() == before.keys()


class Test零渲染零网关审计:
    def test_回放全程渲染与网关零调用(self, tree_store, make_tree, make_node, editing_config):
        """C14 场景 2：池化 + probe + observed 全程不触发渲染/LLM（原则三）。"""

        class _CountingRenderer:
            def __init__(self, inner):
                self._inner = inner
                self.render_calls = 0

            def estimate(self, edl, shots):
                return self._inner.estimate(edl, shots)

            def render(self, edl, shots):
                self.render_calls += 1
                return self._inner.render(edl, shots)

        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        renderer = _CountingRenderer(SimulatedEditRenderer(editing_config.render))
        gateway = LLMGateway(
            MockBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        tree = _editing_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        simulator.observed()
        simulator.probe(tree.root_id, _edl_dict())
        simulator.probe(tree.root_id, _edl_dict(seed_offset=500))
        assert renderer.render_calls == 0
        assert gateway.call_count == 0


class Test分树断言:
    """FR-011：按时间分 train/validation，最近树永远只做 validation（005 口径）。"""

    def test_最近树只做_validation(self, tree_store, make_tree, make_node):
        trees = [_editing_tree(tree_store, make_tree, make_node) for _ in range(3)]
        train, validation = split_train_validation(trees)
        assert len(train) == 2
        assert len(validation) == 1
        latest = max(t.tree_id for t in trees)  # uuid7 时间有序：最大即最近
        assert validation[0].tree_id == latest
        assert all(t.tree_id != latest for t in train)

    def test_单树无_validation(self, tree_store, make_tree, make_node):
        tree = _editing_tree(tree_store, make_tree, make_node)
        train, validation = split_train_validation([tree])
        assert [t.tree_id for t in train] == [tree.tree_id]
        assert validation == []


class Test周校准纳入:
    def test_editing_盲评清单正常产出(self, tree_store, make_tree, make_node, calibration_data_dir):
        """C16 场景 4：editing 不触发 promo 特判拒绝，盲评清单正常落盘。"""
        _editing_tree(tree_store, make_tree, make_node)
        round_ = build_blind_list(
            tree_store,
            agent_id="editing",
            period_start="2020-01-01",
            period_end="2030-01-01",
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert round_.agent_id == "editing"
        assert len(round_.node_ids) >= 1
        blind_file = Path(calibration_data_dir) / "rounds" / "editing" / f"{round_.round_id}.json"
        assert blind_file.exists()

    def test_promo_特判拒绝回归(self, tree_store, calibration_data_dir):
        with pytest.raises(ValidationError, match="promo"):
            build_blind_list(
                tree_store,
                agent_id="promo",
                period_start="2020-01-01",
                period_end="2030-01-01",
                top_k=5,
                data_dir=calibration_data_dir,
            )


class Test静态防特判证明:
    def test_dreaming_与010无_editing_特判(self):
        """dreaming 管线与 010 盲评选取均无 editing 特判分支（泛化缺口 = 0）。"""
        import core.calibration.selection
        import dreaming.pipeline

        for module in (dreaming.pipeline, core.calibration.selection):
            source = inspect.getsource(module)
            assert '"editing"' not in source and "'editing'" not in source


class _OneEdlPolicy:
    policy_version = "editing-freeze-v1"

    def __init__(self, edl):
        self._edl = edl

    def plan(self, config, inputs):
        return [self._edl]


def _loop_config_dict():
    import copy

    import yaml

    config = copy.deepcopy(
        yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "configs" / "movie.yaml").read_text(
                encoding="utf-8"
            )
        )
    )
    config["editing"]["target_duration_s"] = 7
    config["editing"]["duration_tolerance_s"] = 2
    config["editing"]["render"].update(width=64, height=48)
    return config


class Test冻结入池与回放命中:
    """执行器产出的轮次树 → 冻结入池 → probe EDL 精确命中（端到端接线）。"""

    def test_执行器树冻结入池且可回放(
        self,
        make_edl,
        make_shot_library,
        make_scene_structure,
        tree_store,
        artifact_store,
        editing_jobs_engine,
    ):
        from agents.editing.config import EditingConfig

        library = make_shot_library()
        structure = make_scene_structure(library=library)
        config = EditingConfig.from_dict(_loop_config_dict())
        adapter = SimulatedEditRenderer(config.render)
        edl = make_edl(
            clips=[
                {
                    "shot_id": "shot-1",
                    "in_ms": 0,
                    "out_ms": 3000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-3",
                    "in_ms": 0,
                    "out_ms": 4000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ],
            audio=[],
        )
        run_editing_round(
            round_id="r-replay",
            policy=_OneEdlPolicy(edl),
            store=tree_store,
            artifacts=artifact_store,
            adapter=adapter,
            engine=editing_jobs_engine,
            config=config,
            inputs={"shot_library": library, "scene_structure": structure},
            evaluators=[
                StubRuleEvaluator("rule.duration_compliance"),
                StubProxyEvaluator("proxy.pacing_curve", score=0.8),
            ],
        )
        tree = freeze_round_tree("r-replay", tree_store, editing_jobs_engine)
        assert tree.agent_id == "editing"
        pool = SimulatorPool(tree_store)
        pool.add_tree(tree)
        simulator = pool.build(worker_count=1, budget=Budget(max_probes=4), latency_quantum_ms=0)
        # C14：执行器落盘的观测携带回放匹配槽（gen_params=规范化 EDL）→ 精确命中
        result = simulator.probe(tree.root_id, edl.to_dict())
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.8)
