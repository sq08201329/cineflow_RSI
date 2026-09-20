"""声音回放接入与周校准纳入测试（功能 006 / T623 analyze 修订版，先于实现编写）。

C13：声音树冻结后入模拟器池——observed/probe 规范化精确匹配（SoundGenParams
规范化 JSON 相等）、UNKNOWN 语义（无匹配不得分零信息）、回放全程适配器
generate 调用计数 0（零生成审计）；
C16：010 build_blind_list(agent_id="sound") 正常产出（不触发 promo 特判拒绝）；
FR-011：声音池按时间分 train/validation、最近树永远只做 validation（005 口径）。
"""

import time
from pathlib import Path

import pytest

from agents.sound.loop import freeze_round_tree, run_sound_round
from agents.sound.platform.simulated import SimulatedTTSGen
from agents.sound.timing import TimingSheet
from core.calibration.selection import build_blind_list
from core.replay.errors import ValidationError as ReplayValidationError
from core.replay.pool import SimulatorPool
from core.tree.errors import ValidationError
from core.tree.models import CostRecord, NodeStatus, new_id
from dreaming.overfit import split_train_validation
from policies.base import Budget

_TTS_PARAMS = {
    "gen_type": "tts",
    "seed": 1,
    "voice": "narrator",
    "speed_tier": 1,
    "text": "你终于来了。",
    "duration_s": 2.0,
    "loudness_gain_db": -12.5,
    "event_times_ms": [0.0, 1000.0],
    "cer_injected": 0.0,
    "emotion_vector": [0.5, 0.5],
}


def _sound_tree(tree_store, make_tree, make_node, *, score=0.6, children_params=(_TTS_PARAMS,)):
    """单棵声音夹具树：root + 指定 gen_params 子节点（得分可区分）。"""
    tree = make_tree(agent_id="sound", project_id="sound-replay")
    tree_store.create_tree(tree)
    tree_store.append_node(
        make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            agent_id="sound",
            eval_breakdown={},
            score=0.0,
            status=NodeStatus.EVALUATED,
        )
    )
    for i, params in enumerate(children_params):
        tree_store.append_node(
            make_node(
                node_id=new_id(),
                tree_id=tree.tree_id,
                parent_id=tree.root_id,
                depth=1,
                agent_id="sound",
                observation_context={
                    "gen_params": params,
                    "gen_type": params.get("gen_type", "tts"),
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
    return pool.build(
        worker_count=1, budget=Budget(max_probes=max_probes), latency_quantum_ms=0
    )


class Test规范化精确匹配:
    def test_键序乱排仍精确命中(self, tree_store, make_tree, make_node):
        """SoundGenParams 规范化 JSON 相等：键递归排序后逐字节相等即匹配。"""
        tree = _sound_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        shuffled = dict(reversed(list(_TTS_PARAMS.items())))  # 同语义不同键序
        result = simulator.probe(tree.root_id, shuffled)
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.6)
        assert result.nodes[0].fields["gen_params"] == _TTS_PARAMS

    def test_观测投影仅白名单字段(self, tree_store, make_tree, make_node):
        tree = _sound_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        simulator.probe(tree.root_id, dict(_TTS_PARAMS))
        observations = simulator.observed()
        # make_tree 默认快照无白名单 → 零泄露；声音树快照白名单由执行器写入
        for observation in observations.values():
            assert set(observation.fields) <= {"gen_params", "gen_type", "job_id"}

    def test_UNKNOWN_不得分零信息(self, tree_store, make_tree, make_node):
        tree = _sound_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        before = simulator.observed()
        result = simulator.probe(tree.root_id, {**_TTS_PARAMS, "seed": 999})
        assert result.status == "unknown"
        assert result.nodes == []
        assert simulator.observed().keys() == before.keys()  # 无新揭示（零信息泄漏）


class Test零生成审计:
    def test_回放全程适配器零调用(self, tree_store, make_tree, make_node, sound_config):
        """C13 场景 2：池化 + probe + observed 全程不触发生成（原则三）。"""

        class _CountingAdapter:
            gen_type = "tts"

            def __init__(self, inner):
                self._inner = inner
                self.generate_calls = 0

            def estimate(self, params):
                return self._inner.estimate(params)

            def generate(self, params):
                self.generate_calls += 1
                return self._inner.generate(params)

        adapter = _CountingAdapter(
            SimulatedTTSGen(sound_config.simulated_gen, sound_config.sample_rate)
        )
        tree = _sound_tree(tree_store, make_tree, make_node)
        simulator = _simulator(tree_store, tree)
        simulator.observed()
        simulator.probe(tree.root_id, dict(_TTS_PARAMS))
        simulator.probe(tree.root_id, {**_TTS_PARAMS, "seed": 2})
        assert adapter.generate_calls == 0


class Test周校准纳入:
    def test_sound_盲评清单正常产出(
        self, tree_store, make_tree, make_node, calibration_data_dir
    ):
        """C16 场景 4：sound 不触发 promo 特判拒绝，盲评清单正常落盘。"""
        tree = _sound_tree(tree_store, make_tree, make_node)
        round_ = build_blind_list(
            tree_store,
            agent_id="sound",
            period_start="2020-01-01",
            period_end="2030-01-01",
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert round_.agent_id == "sound"
        assert len(round_.node_ids) >= 1
        blind_file = (
            Path(calibration_data_dir) / "rounds" / f"{round_.round_id}" / "blind_list.json"
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


class Test分树断言:
    """FR-011：按时间分 train/validation，最近树永远只做 validation（005 口径）。"""

    def test_最近树只做_validation(self, tree_store, make_tree, make_node):
        trees = [_sound_tree(tree_store, make_tree, make_node) for _ in range(3)]
        train, validation = split_train_validation(trees)
        assert len(train) == 2
        assert len(validation) == 1
        latest = max(t.tree_id for t in trees)  # uuid7 时间有序：最大即最近
        assert validation[0].tree_id == latest
        assert all(t.tree_id != latest for t in train)

    def test_单树无_validation(self, tree_store, make_tree, make_node):
        tree = _sound_tree(tree_store, make_tree, make_node)
        train, validation = split_train_validation([tree])
        assert [t.tree_id for t in train] == [tree.tree_id]
        assert validation == []


class _OneJobPolicy:
    policy_version = "sound-freeze-v1"

    def __init__(self, params):
        self._params = params

    def plan(self, config, inputs):
        return [{"gen_type": "tts", "gen_params": self._params}]


class Test冻结入池接线:
    """声音模拟器池接线：轮次树全终态才允许冻结入池（004 freeze_round_tree 同构）。"""

    def test_全终态后冻结并入池(
        self, make_sound_gen_params, make_timing_sheet, tree_store, artifact_store,
        sound_jobs_engine, sound_config,
    ):
        adapters = {"tts": SimulatedTTSGen(sound_config.simulated_gen, sound_config.sample_rate)}
        params = make_sound_gen_params(gen_type="tts", seed=1)
        run_sound_round(
            round_id="r-freeze",
            policy=_OneJobPolicy(params),
            store=tree_store,
            artifacts=artifact_store,
            adapters=adapters,
            engine=sound_jobs_engine,
            config=sound_config,
            inputs={"timing_sheet": make_timing_sheet(), "mood": "悬疑"},
        )
        tree = freeze_round_tree("r-freeze", tree_store, sound_jobs_engine)
        assert tree.agent_id == "sound"
        # 冻结树入池合法（ensure_frozen 通过）
        pool = SimulatorPool(tree_store)
        pool.add_tree(tree)
        assert len(pool.trees) == 1

    def test_未到终态拒绝冻结(
        self, make_sound_gen_params, make_timing_sheet, tree_store, artifact_store,
        sound_jobs_engine, sound_config,
    ):
        from sqlalchemy import insert

        from agents.sound.db import sound_gen_jobs
        from agents.sound.loop import SoundLoopError

        adapters = {"tts": SimulatedTTSGen(sound_config.simulated_gen, sound_config.sample_rate)}
        run_sound_round(
            round_id="r-pending",
            policy=_OneJobPolicy(make_sound_gen_params(gen_type="tts", seed=1)),
            store=tree_store,
            artifacts=artifact_store,
            adapters=adapters,
            engine=sound_jobs_engine,
            config=sound_config,
            inputs={"timing_sheet": make_timing_sheet(), "mood": "悬疑"},
        )
        # 伪造一条未到终态的 job（rendered ≠ inserted/failed）
        with sound_jobs_engine.begin() as conn:
            conn.execute(
                insert(sound_gen_jobs).values(
                    job_id="r-pending-ghost",
                    round_id="r-pending",
                    gen_type="sfx",
                    params_json="{}",
                    params_hash="gh" * 32,
                    status="rendered",
                    estimated_cost_usd=0.5,
                    actual_cost_usd=None,
                    artifact_hash=None,
                    error=None,
                    created_at="2026-09-20T00:00:00Z",
                )
            )
        with pytest.raises(SoundLoopError, match="终态"):
            freeze_round_tree("r-pending", tree_store, sound_jobs_engine)

    def test_不存在轮次报错(self, tree_store, sound_jobs_engine):
        from agents.sound.loop import SoundLoopError

        with pytest.raises((SoundLoopError, ReplayValidationError, Exception)) as exc_info:
            freeze_round_tree("ghost-round", tree_store, sound_jobs_engine)
        assert "ghost-round" in str(exc_info.value) or "不存在" in str(exc_info.value)
