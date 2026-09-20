"""声音探索执行器测试（功能 006 / T612，先于实现编写）。

C1 场景 1~5：一轮 4 组参数（2 TTS+1 SFX+1 music）落树分账齐全；超预算部分拒绝、
已执行照常入账；同 round_id 二次触发重建首轮结果（0 重复生成/扣费，唯一键
(round_id, params_hash)）；失败 job 成本照计入账 status=failed；TimingSheet 非法
执行前拒绝（适配器 0 调用）。评估器以桩注入（US2 才接真实评估器，任务书显式取舍）。
"""

import copy
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from agents.sound.config import SoundConfig
from agents.sound.db import sound_gen_jobs
from agents.sound.loop import run_sound_round
from agents.sound.platform.base import SoundGenError, UnavailableError
from agents.sound.platform.simulated import (
    SimulatedMusicGen,
    SimulatedSFXGen,
    SimulatedTTSGen,
)
from core.tree.errors import ValidationError
from core.tree.models import NodeStatus
from tests.stubs import StubProxyEvaluator, StubRuleEvaluator

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _config(cap_usd: float | None = None) -> SoundConfig:
    """真实配置深拷贝（可覆盖单轮预算上限构造超支场景）。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    if cap_usd is not None:
        raw["sound"]["exploration_per_round_usd"] = cap_usd
    return SoundConfig.from_dict(raw)


class _StubPolicy:
    """手工策略桩：固定产 4 组参数（2 TTS + 1 SFX + 1 music，C1 场景 1 配比）。"""

    policy_version = "sound-stub-v1"

    def __init__(self, make_sound_gen_params):
        self._plans = [
            {"gen_type": "tts", "gen_params": make_sound_gen_params(gen_type="tts", seed=11)},
            {"gen_type": "tts", "gen_params": make_sound_gen_params(gen_type="tts", seed=22)},
            {"gen_type": "sfx", "gen_params": make_sound_gen_params(gen_type="sfx", seed=33)},
            {"gen_type": "music", "gen_params": make_sound_gen_params(gen_type="music", seed=44)},
        ]

    def plan(self, config, inputs):
        return self._plans


class _CountingAdapter:
    """调用计数包装（幂等重建/执行前拒绝的 0 调用断言用）。"""

    def __init__(self, inner):
        self._inner = inner
        self.gen_type = inner.gen_type
        self.generate_calls = 0

    def estimate(self, params):
        return self._inner.estimate(params)

    def generate(self, params):
        self.generate_calls += 1
        return self._inner.generate(params)


class _FailingAdapter:
    """生成必败适配器（C1 场景 4）：成本照计入账、status=failed。"""

    def __init__(self, inner):
        self._inner = inner
        self.gen_type = inner.gen_type

    def estimate(self, params):
        return self._inner.estimate(params)

    def generate(self, params):
        raise UnavailableError("模拟平台不可用（测试注入）")


def _stub_evaluators():
    """四评估器桩：evaluator_id 对齐 evaluator_weights.sound 权重键。"""
    return [
        StubRuleEvaluator("rule.loudness_compliance"),
        StubRuleEvaluator("rule.av_sync"),
        StubProxyEvaluator("proxy.asr_transcript"),
        StubProxyEvaluator("proxy.emotion_music_match"),
    ]


@pytest.fixture()
def adapters(sound_config):
    return {
        "tts": SimulatedTTSGen(sound_config.simulated_gen, sound_config.sample_rate),
        "sfx": SimulatedSFXGen(sound_config.simulated_gen, sound_config.sample_rate),
        "music": SimulatedMusicGen(sound_config.simulated_gen, sound_config.sample_rate),
    }


@pytest.fixture()
def inputs(make_timing_sheet):
    return {
        "timing_sheet": make_timing_sheet(),
        "mood": "悬疑",
    }


def _run(round_id, policy, tree_store, artifact_store, adapters, engine, config, inputs):
    return run_sound_round(
        round_id=round_id,
        policy=policy,
        store=tree_store,
        artifacts=artifact_store,
        adapters=adapters,
        engine=engine,
        config=config,
        inputs=inputs,
        evaluators=_stub_evaluators(),
    )


class Test场景1_一轮探索落树分账:
    def test_四工件落树且分账齐全(
        self, make_sound_gen_params, tree_store, artifact_store, adapters, sound_jobs_engine, inputs
    ):
        policy = _StubPolicy(make_sound_gen_params)
        result = _run(
            "r1", policy, tree_store, artifact_store, adapters, sound_jobs_engine, _config(), inputs
        )
        assert [j["status"] for j in result.jobs] == ["inserted"] * 4
        # 分账：TTS 2×0.4 + SFX 0.4 + music 0.4（模拟器实际扣费 cost_per_clip_usd）
        assert result.cost_by_type == {
            "tts": pytest.approx(0.8),
            "sfx": pytest.approx(0.4),
            "music": pytest.approx(0.4),
            "total": pytest.approx(1.6),
        }
        assert result.spent_usd == pytest.approx(1.6)

        nodes = tree_store.nodes_of(result.tree_id)
        children = [n for n in nodes if n.parent_id is not None]
        assert len(children) == 4
        for node in children:
            assert node.status is NodeStatus.EVALUATED
            assert node.score == pytest.approx(0.8)  # 桩：gate 满分 + 双 proxy 0.8×0.5+0.8×0.5
            assert len(node.artifact_hash) == 64
            # eval_breakdown 四分量齐全（evaluator_id@version 键格式）
            assert set(node.eval_breakdown) == {
                "rule.loudness_compliance@1.0.0",
                "rule.av_sync@1.0.0",
                "proxy.asr_transcript@1.0.0",
                "proxy.emotion_music_match@1.0.0",
            }
            assert node.cost.generation_api_cost_usd == pytest.approx(0.4)

        # 运营表：4 行 inserted，gen_type 分账粒度落库
        with sound_jobs_engine.connect() as conn:
            rows = conn.execute(
                select(sound_gen_jobs).where(sound_gen_jobs.c.round_id == "r1")
            ).all()
        assert len(rows) == 4
        assert {r.status for r in rows} == {"inserted"}
        assert sorted(r.gen_type for r in rows) == ["music", "sfx", "tts", "tts"]
        assert all(r.actual_cost_usd == pytest.approx(0.4) for r in rows)

    def test_节点_policy_version_为真实策略版本(
        self, make_sound_gen_params, tree_store, artifact_store, adapters, sound_jobs_engine, inputs
    ):
        """回归：谱系正确性（原则二）——节点 policy_version 必须落当前策略版本，
        不得硬编码 agent_id（007 US1 发现的 006 遗留缺陷）。"""
        policy = _StubPolicy(make_sound_gen_params)
        result = _run(
            "r1b",
            policy,
            tree_store,
            artifact_store,
            adapters,
            sound_jobs_engine,
            _config(),
            inputs,
        )
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert nodes
        assert all(n.policy_version == "sound-stub-v1" for n in nodes)


class Test场景2_超预算拒绝:
    def test_超额拒绝已执行照常入账(
        self, make_sound_gen_params, tree_store, artifact_store, adapters, sound_jobs_engine, inputs
    ):
        # 上限 $1.0：job1/job2 各耗 0.4；job3 起 0.8+0.5 > 1.0 拒绝
        policy = _StubPolicy(make_sound_gen_params)
        result = _run(
            "r2",
            policy,
            tree_store,
            artifact_store,
            adapters,
            sound_jobs_engine,
            _config(cap_usd=1.0),
            inputs,
        )
        statuses = [j["status"] for j in result.jobs]
        assert statuses == ["inserted", "inserted", "rejected", "rejected"]
        assert "预算门禁" in result.jobs[2]["reason"]
        assert result.spent_usd == pytest.approx(0.8)  # 已执行照常入账
        assert result.cost_by_type["tts"] == pytest.approx(0.8)
        assert result.cost_by_type["total"] == pytest.approx(0.8)

        nodes = tree_store.nodes_of(result.tree_id)
        children = {n.observation_context["job_id"]: n for n in nodes if n.parent_id is not None}
        rejected = [n for n in children.values() if "reject_reason" in n.observation_context]
        assert len(rejected) == 2
        for node in rejected:
            assert node.score == 0.0
            assert node.cost.generation_api_cost_usd == 0.0  # 拒绝零成本


class Test场景3_幂等重建:
    def test_同_round_id_二次触发零重复生成(
        self, make_sound_gen_params, tree_store, artifact_store, adapters, sound_jobs_engine, inputs
    ):
        counting = {k: _CountingAdapter(a) for k, a in adapters.items()}
        policy = _StubPolicy(make_sound_gen_params)
        first = _run(
            "r3", policy, tree_store, artifact_store, counting, sound_jobs_engine, _config(), inputs
        )
        calls_after_first = {k: a.generate_calls for k, a in counting.items()}
        assert sum(calls_after_first.values()) == 4

        second = _run(
            "r3",
            _StubPolicy(make_sound_gen_params),
            tree_store,
            artifact_store,
            counting,
            sound_jobs_engine,
            _config(),
            inputs,
        )
        # 0 重复生成、0 重复扣费；首轮结果完整重建
        assert {k: a.generate_calls for k, a in counting.items()} == calls_after_first
        assert second.tree_id == first.tree_id
        assert second.jobs == first.jobs
        assert second.spent_usd == first.spent_usd == pytest.approx(1.6)
        assert second.cost_by_type == first.cost_by_type

        with sound_jobs_engine.connect() as conn:
            total = conn.execute(
                select(func.sum(sound_gen_jobs.c.actual_cost_usd)).where(
                    sound_gen_jobs.c.round_id == "r3"
                )
            ).scalar()
        assert total == pytest.approx(1.6)  # 无重复扣费


class Test场景4_失败照计入账:
    def test_失败_job_成本入账_status_failed(
        self, make_sound_gen_params, tree_store, artifact_store, adapters, sound_jobs_engine, inputs
    ):
        adapters["sfx"] = _FailingAdapter(adapters["sfx"])
        policy = _StubPolicy(make_sound_gen_params)
        result = _run(
            "r4", policy, tree_store, artifact_store, adapters, sound_jobs_engine, _config(), inputs
        )
        statuses = {j["gen_type"]: j["status"] for j in result.jobs}
        assert statuses["sfx"] == "failed"
        assert statuses["music"] == "inserted"  # 轮次继续

        # 预估成本照常入账（原则二）：sfx 小计 = estimate 0.5
        assert result.cost_by_type["sfx"] == pytest.approx(0.5)
        assert result.cost_by_type["total"] == pytest.approx(0.8 + 0.5 + 0.4)

        with sound_jobs_engine.connect() as conn:
            row = conn.execute(
                select(sound_gen_jobs).where(
                    sound_gen_jobs.c.round_id == "r4", sound_gen_jobs.c.gen_type == "sfx"
                )
            ).one()
        assert row.status == "failed"
        assert row.error
        assert row.actual_cost_usd == pytest.approx(0.5)

        failed_nodes = [
            n
            for n in tree_store.nodes_of(result.tree_id)
            if n.parent_id is not None and n.status is NodeStatus.FAILED
        ]
        assert len(failed_nodes) == 1
        assert failed_nodes[0].cost.generation_api_cost_usd == pytest.approx(0.5)


class Test场景5_非法输入执行前拒绝:
    def test_TimingSheet_重叠_适配器零调用(
        self,
        make_sound_gen_params,
        tree_store,
        artifact_store,
        adapters,
        sound_jobs_engine,
    ):
        counting = {k: _CountingAdapter(a) for k, a in adapters.items()}
        # 上游 dict 形态的非法时序（重叠）：执行器构造校验必须在适配器调用前拒绝
        bad_inputs = {
            "timing_sheet": {
                "utterances": [
                    {"text": "你终于来了。", "start_ms": 0, "end_ms": 1000},
                    {"text": "我等了很久。", "start_ms": 800, "end_ms": 2000},
                ],
                "effects": [{"kind": "door_slam", "at_ms": 1100}],
            },
            "mood": "悬疑",
        }
        with pytest.raises(ValidationError):
            _run(
                "r5",
                _StubPolicy(make_sound_gen_params),
                tree_store,
                artifact_store,
                counting,
                sound_jobs_engine,
                _config(),
                bad_inputs,
            )
        assert sum(a.generate_calls for a in counting.values()) == 0  # 0 调用
        with sound_jobs_engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(sound_gen_jobs)).scalar()
        assert count == 0  # 0 成本

    def test_原始_dict_输入同样执行前校验(
        self, make_sound_gen_params, tree_store, artifact_store, adapters, sound_jobs_engine
    ):
        """上游以 dict 形态传入 TimingSheet 时，执行器负责构造校验（C1 场景 5）。"""
        bad_inputs = {
            "timing_sheet": {
                "utterances": [
                    {"text": "甲", "start_ms": 0, "end_ms": 2000},
                    {"text": "乙", "start_ms": 1000, "end_ms": 3000},
                ],
                "effects": [],
            },
            "mood": "悬疑",
        }
        with pytest.raises(ValidationError, match="重叠"):
            _run(
                "r5b",
                _StubPolicy(make_sound_gen_params),
                tree_store,
                artifact_store,
                adapters,
                sound_jobs_engine,
                _config(),
                bad_inputs,
            )


class Test错误类型对齐:
    def test_平台错误统一_SoundGenError_族(self):
        assert issubclass(UnavailableError, SoundGenError)
