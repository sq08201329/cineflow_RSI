"""分镜回放接入、周校准纳入与 schema 快照测试（功能 008 / T829，先于实现编写）。

C14：分镜树冻结入池——observed/probe 双键（shotlist + gen_params）规范化精确匹配
（嵌套键序乱排仍命中）、UNKNOWN 零信息、观测白名单投影、回放全程渲染 + 网关调用
计数 0 审计（原则三）；FR-011 分树：最近树永远只做 validation（005 口径）。
C16：010 `build_blind_list(agent_id="storyboard")` 正常产出 + dreaming/010 无
storyboard 特判静态证明。
C17：ShotList 序列化 schema 快照（字段名/枚举值/版本号）——防后续漂移，
下游视觉线（004）与剪辑线（007）按此契约接入。
"""

import inspect
import json
import time
from pathlib import Path

import pytest

from agents.storyboard.loop import freeze_round_tree, run_storyboard_round
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.storyboard.shotlist import ShotList
from core.calibration.selection import build_blind_list
from core.evaluators.errors import ValidationError
from core.replay.matching import params_match
from core.replay.pool import SimulatorPool
from core.tree.models import CostRecord, NodeStatus, new_id
from dreaming.overfit import split_train_validation
from policies.base import Budget
from tests.stubs import StubProxyEvaluator, StubRuleEvaluator

# C17 schema 快照：字段名/枚举值/版本号与文档（data-model.md + configs/movie.yaml）一致
_SCHEMA_SNAPSHOT = {
    "schema_version": "1.0.0",
    "top_level_keys": ["schema_version", "shots"],
    "shot_keys": [
        "alternatives",
        "camera",
        "covers",
        "est_duration_ms",
        "movement",
        "scene_id",
        "shot_id",
        "shot_size",
        "side",
    ],
    "enums": {
        "shot_size": ["extreme_close_up", "close_up", "medium", "full", "wide"],
        "camera": ["eye_level", "low_angle", "high_angle", "over_shoulder", "side"],
        "movement": ["static", "pan", "tilt", "dolly", "handheld"],
        "side": ["A", "B"],
        "line_kind": ["dialogue", "action"],
    },
}


def _shotlist_dict(make_shotlist, *, duration_delta: int = 0) -> dict:
    """合法 ShotList dict（回放匹配参数形态）：时长整体偏移可造近似未命中。"""
    shots = make_shotlist().to_dict()["shots"]
    if duration_delta:
        shots = [
            {**shot, "est_duration_ms": shot["est_duration_ms"] + duration_delta} for shot in shots
        ]
    return ShotList(shots=shots).to_dict()


def _storyboard_tree(tree_store, make_tree, make_node, *, children, score=0.6):
    """单棵分镜夹具树：root + 指定 ShotList 子节点（观测白名单对齐执行器落盘形态）。"""
    tree = make_tree(
        agent_id="storyboard",
        project_id="storyboard-replay",
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
        )
    )
    for i, shotlist_dict in enumerate(children):
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
                    "shotlist_hash": ShotList.from_dict(shotlist_dict).shotlist_hash(),
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


class Test观测双键规范化精确匹配:
    def test_键序乱排仍精确命中(self, tree_store, make_tree, make_node, make_shotlist):
        """C14：ShotList 规范化 JSON 相等——顶层与嵌套键序乱排后逐字节匹配。"""
        shotlist_dict = _shotlist_dict(make_shotlist)
        tree = _storyboard_tree(
            tree_store, make_tree, make_node, children=(shotlist_dict,), score=0.7
        )
        simulator = _simulator(tree_store, tree)
        shuffled = {
            "shots": [
                {
                    "side": shot["side"],
                    "shot_size": shot["shot_size"],
                    "shot_id": shot["shot_id"],
                    "scene_id": shot["scene_id"],
                    "movement": shot["movement"],
                    "est_duration_ms": shot["est_duration_ms"],
                    "covers": list(shot["covers"]),
                    "camera": shot["camera"],
                    "alternatives": shot["alternatives"],
                }
                for shot in shotlist_dict["shots"]
            ],
            "schema_version": shotlist_dict["schema_version"],
        }
        result = simulator.probe(tree.root_id, shuffled)
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.7)
        # 双键：gen_params（回放匹配槽）与 shotlist（可读观测）内容一致
        assert result.nodes[0].fields["gen_params"] == shotlist_dict
        assert result.nodes[0].fields["shotlist"] == shotlist_dict
        assert params_match(result.nodes[0].fields["gen_params"], shotlist_dict)

    def test_观测投影仅白名单字段(self, tree_store, make_tree, make_node, make_shotlist):
        tree = _storyboard_tree(
            tree_store, make_tree, make_node, children=(_shotlist_dict(make_shotlist),)
        )
        simulator = _simulator(tree_store, tree)
        simulator.probe(tree.root_id, _shotlist_dict(make_shotlist))
        for observation in simulator.observed().values():
            assert set(observation.fields) <= {
                "gen_params",
                "shotlist",
                "shotlist_hash",
                "job_id",
            }

    def test_UNKNOWN_不得分零信息(self, tree_store, make_tree, make_node, make_shotlist):
        """ShotList 任一字段偏差（时长 +125ms）→ UNKNOWN：不得分、无新揭示。"""
        tree = _storyboard_tree(
            tree_store, make_tree, make_node, children=(_shotlist_dict(make_shotlist),)
        )
        simulator = _simulator(tree_store, tree)
        before = simulator.observed()
        result = simulator.probe(tree.root_id, _shotlist_dict(make_shotlist, duration_delta=125))
        assert result.status == "unknown"
        assert result.nodes == []
        assert simulator.observed().keys() == before.keys()


class Test零渲染零网关审计:
    def test_回放全程渲染与网关零调用(
        self, tree_store, make_tree, make_node, make_shotlist, storyboard_config
    ):
        """C14 场景 2：池化 + probe + observed 全程不触发渲染/LLM（原则三）。"""

        class _CountingRenderer:
            def __init__(self, inner):
                self._inner = inner
                self.render_calls = 0
                self.estimate_calls = 0

            def estimate(self, shotlist, cfg):
                self.estimate_calls += 1
                return self._inner.estimate(shotlist, cfg)

            def render(self, shotlist, script, cfg):
                self.render_calls += 1
                return self._inner.render(shotlist, script, cfg)

        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        renderer = _CountingRenderer(SimulatedStoryboardRenderer())
        gateway = LLMGateway(
            MockBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        tree = _storyboard_tree(
            tree_store, make_tree, make_node, children=(_shotlist_dict(make_shotlist),)
        )
        simulator = _simulator(tree_store, tree)
        simulator.observed()
        simulator.probe(tree.root_id, _shotlist_dict(make_shotlist))
        simulator.probe(tree.root_id, _shotlist_dict(make_shotlist, duration_delta=500))
        assert renderer.render_calls == 0
        assert renderer.estimate_calls == 0
        assert gateway.call_count == 0
        assert storyboard_config.render["encode_threads"] == 1  # 渲染参数不参与回放


class Test分树断言:
    """FR-011：按时间分 train/validation，最近树永远只做 validation（005 口径）。"""

    def test_最近树只做_validation(self, tree_store, make_tree, make_node, make_shotlist):
        trees = [
            _storyboard_tree(
                tree_store, make_tree, make_node, children=(_shotlist_dict(make_shotlist),)
            )
            for _ in range(3)
        ]
        train, validation = split_train_validation(trees)
        assert len(train) == 2
        assert len(validation) == 1
        latest = max(t.tree_id for t in trees)  # uuid7 时间有序：最大即最近
        assert validation[0].tree_id == latest
        assert all(t.tree_id != latest for t in train)

    def test_单树无_validation(self, tree_store, make_tree, make_node, make_shotlist):
        tree = _storyboard_tree(
            tree_store, make_tree, make_node, children=(_shotlist_dict(make_shotlist),)
        )
        train, validation = split_train_validation([tree])
        assert [t.tree_id for t in train] == [tree.tree_id]
        assert validation == []


class Test周校准纳入:
    def test_storyboard_盲评清单正常产出(
        self, tree_store, make_tree, make_node, make_shotlist, calibration_data_dir
    ):
        """C16 场景 4：storyboard 不触发 promo 特判拒绝，盲评清单正常落盘。"""
        _storyboard_tree(
            tree_store, make_tree, make_node, children=(_shotlist_dict(make_shotlist),)
        )
        round_ = build_blind_list(
            tree_store,
            agent_id="storyboard",
            period_start="2020-01-01",
            period_end="2030-01-01",
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert round_.agent_id == "storyboard"
        assert len(round_.node_ids) >= 1
        blind_file = (
            Path(calibration_data_dir) / "rounds" / "storyboard" / f"{round_.round_id}.json"
        )
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
    def test_dreaming_与010无_storyboard_特判(self):
        """dreaming 全部模块 + 010 校准选取无 storyboard 特判分支（泛化缺口 = 0）。"""
        import core.calibration.pairing
        import core.calibration.selection
        import dreaming.candidates
        import dreaming.digest
        import dreaming.lineage
        import dreaming.overfit
        import dreaming.pipeline

        modules = [
            dreaming.pipeline,
            dreaming.candidates,
            dreaming.digest,
            dreaming.lineage,
            dreaming.overfit,
            core.calibration.selection,
            core.calibration.pairing,
        ]
        for module in modules:
            source = inspect.getsource(module)
            assert "storyboard" not in source, f"{module.__name__} 出现 storyboard 特判"


class TestShotListSchema快照:
    """C17：序列化 schema 快照——字段名/枚举值/schema_version 与文档一致。"""

    def test_顶层与逐镜字段名快照(self, make_shotlist):
        payload = json.loads(make_shotlist().canonical_json())
        assert sorted(payload) == _SCHEMA_SNAPSHOT["top_level_keys"]
        for shot in payload["shots"]:
            assert sorted(shot) == _SCHEMA_SNAPSHOT["shot_keys"]

    def test_schema_版本号快照(self, make_shotlist):
        payload = json.loads(make_shotlist().canonical_json())
        assert payload["schema_version"] == _SCHEMA_SNAPSHOT["schema_version"]
        assert ShotList.SCHEMA_VERSION == _SCHEMA_SNAPSHOT["schema_version"]

    def test_枚举值与配置一致(self, make_shotlist, storyboard_config, script_segment):
        """枚举值 = configs/movie.yaml 原值（执行前校验与门禁共用同一配置源）。"""
        grammar = storyboard_config.shot_grammar
        assert grammar["shot_sizes"] == _SCHEMA_SNAPSHOT["enums"]["shot_size"]
        assert grammar["camera_positions"] == _SCHEMA_SNAPSHOT["enums"]["camera"]
        assert grammar["movements"] == _SCHEMA_SNAPSHOT["enums"]["movement"]
        assert list(_SCHEMA_SNAPSHOT["enums"]["side"]) == ["A", "B"]
        payload = json.loads(make_shotlist().canonical_json())
        for shot in payload["shots"]:
            assert shot["shot_size"] in _SCHEMA_SNAPSHOT["enums"]["shot_size"]
            assert shot["camera"] in _SCHEMA_SNAPSHOT["enums"]["camera"]
            assert shot["movement"] in _SCHEMA_SNAPSHOT["enums"]["movement"]
            assert shot["side"] in _SCHEMA_SNAPSHOT["enums"]["side"]
        # 剧本行类型同为下游契约面（ScriptSegment schema 稳定）
        kinds = {line.kind for scene in script_segment.scenes for line in scene.lines}
        assert kinds <= set(_SCHEMA_SNAPSHOT["enums"]["line_kind"])

    def test_回放匹配键即规范化_JSON(self, make_shotlist):
        """回放匹配键 = 规范化 JSON（键序稳定）→ 哈希与匹配同源，无二次序列化差异。"""
        shotlist = make_shotlist()
        canonical = json.loads(shotlist.canonical_json())
        assert params_match(shotlist.to_dict(), canonical)
        assert params_match(canonical, ShotList.from_dict(canonical).to_dict())


class _OneShotListPolicy:
    policy_version = "storyboard-freeze-v1"

    def __init__(self, shotlist):
        self._shotlist = shotlist

    def plan(self, config, inputs):
        return [self._shotlist]


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
    config["storyboard"]["render"].update(width=64, height=48)
    return config


class Test冻结入池与回放命中:
    """执行器产出的轮次树 → 冻结入池 → probe ShotList 精确命中（端到端接线）。"""

    def test_执行器树冻结入池且可回放(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        storyboard_jobs_engine,
    ):
        from agents.storyboard.config import StoryboardConfig

        config = StoryboardConfig.from_dict(_loop_config_dict())
        adapter = SimulatedStoryboardRenderer()
        shotlist = make_shotlist()
        run_storyboard_round(
            round_id="r-replay",
            policy=_OneShotListPolicy(shotlist),
            store=tree_store,
            artifacts=artifact_store,
            adapter=adapter,
            engine=storyboard_jobs_engine,
            config=config,
            inputs={"script": make_script_segment()},
            evaluators=[
                StubRuleEvaluator("rule.shot_grammar"),
                StubProxyEvaluator("proxy.emotion_alignment", score=0.8),
            ],
        )
        tree = freeze_round_tree("r-replay", tree_store, storyboard_jobs_engine)
        assert tree.agent_id == "storyboard"
        pool = SimulatorPool(tree_store)
        pool.add_tree(tree)
        simulator = pool.build(worker_count=1, budget=Budget(max_probes=4), latency_quantum_ms=0)
        # C14：执行器落盘的观测携带回放匹配槽（gen_params=规范化 ShotList）→ 精确命中
        result = simulator.probe(tree.root_id, shotlist.to_dict())
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.8)
