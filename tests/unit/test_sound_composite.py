"""声音合成评分与执行器接线测试（功能 006 / T618 analyze 修订版，先于实现编写）。

C8：gate 违规总分 0 短路、不适用分量跳过 + 适用权重归一合成、quantize 6 位定点、
版本元信息含实现哈希；宪章测试纪律补项——四评估器注册元数据断言
（cost_per_call ≥ 0 显式、deterministic=True、kind 正确）+ 与 004 视觉系
评估器对比样本（同工件经新旧评估器各评一次，得分域与 diagnostics 键结构一致）。
T621 接线验证：loop 以真实四评估器替换桩，节点 eval_breakdown 四分量齐全。
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from agents.sound.audio import decode_wav_samples, synthesize_wav
from agents.sound.evaluators import build_sound_evaluators
from agents.sound.evaluators.composite import COMPOSITE_POLICY, composite_sound
from agents.sound.evaluators.loudness import measure_loudness_lufs
from agents.sound.loop import run_sound_round
from agents.sound.platform.simulated import (
    SimulatedMusicGen,
    SimulatedSFXGen,
    SimulatedTTSGen,
)
from core.evaluators.base import ArtifactRef, EvalResult, EvaluatorKind
from core.evaluators.quantize import quantize_score
from core.tree.models import NodeStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
_SOUND = _CONFIG["sound"]
_DIST = _SOUND["simulated_gen"]
_SR = int(_SOUND["sample_rate"])

_WEIGHTS = {
    "rule.loudness_compliance": 0.0,  # gate → 权重 0（门禁语义在短路）
    "rule.av_sync": 0.0,
    "proxy.asr_transcript": 0.5,
    "proxy.emotion_music_match": 0.5,
}


def _frag(score, applicable=True):
    return {"score": score, "diagnostics": {"applicable": applicable}}


class Test合成口径:
    def test_gate_违规总分0短路(self):
        """任一 rule.* 判 0 → 总分 0（无视 proxy 得分，不可行解）。"""
        breakdown = {
            "rule.loudness_compliance@1.0.0+x": _frag(0.0),
            "rule.av_sync@1.0.0+x": _frag(1.0),
            "proxy.asr_transcript@1.0.0+x": _frag(0.9),
            "proxy.emotion_music_match@1.0.0+x": _frag(0.9),
        }
        assert composite_sound(breakdown, _WEIGHTS) == 0.0

    def test_不适用分量跳过且按适用归一(self):
        """TTS 工件：emotion 不适用跳过 → 总分 = asr 得分（适用权重归一）。"""
        breakdown = {
            "rule.loudness_compliance@1.0.0+x": _frag(1.0),
            "rule.av_sync@1.0.0+x": _frag(1.0),
            "proxy.asr_transcript@1.0.0+x": _frag(0.6),
            "proxy.emotion_music_match@1.0.0+x": _frag(0.0, applicable=False),
        }
        assert composite_sound(breakdown, _WEIGHTS) == pytest.approx(0.6)

    def test_双代理适用时加权合成(self):
        breakdown = {
            "rule.loudness_compliance@1.0.0+x": _frag(1.0),
            "rule.av_sync@1.0.0+x": _frag(1.0),
            "proxy.asr_transcript@1.0.0+x": _frag(0.6),
            "proxy.emotion_music_match@1.0.0+x": _frag(0.8),
        }
        assert composite_sound(breakdown, _WEIGHTS) == pytest.approx(0.7)

    def test_quantize_六位定点(self):
        breakdown = {
            "proxy.asr_transcript@1.0.0+x": _frag(1.0 / 3.0),
        }
        weights = {"proxy.asr_transcript": 1.0}
        score = quantize_score(composite_sound(breakdown, weights))
        assert score == round(1.0 / 3.0, 6)

    def test_合成口径进版本元信息(self):
        """归一口径（N/A 跳过 + 适用归一）必须进快照版本元信息（C8）。"""
        assert "归一" in COMPOSITE_POLICY
        assert "gate" in COMPOSITE_POLICY


class Test注册元数据断言:
    """宪章测试纪律：四评估器注册元数据显式合规。"""

    def test_四评估器_spec_元数据(self, sound_config):
        evaluators = build_sound_evaluators(sound_config)
        assert len(evaluators) == 4
        expected_kinds = {
            "rule.loudness_compliance": EvaluatorKind.RULE,
            "rule.av_sync": EvaluatorKind.RULE,
            "proxy.asr_transcript": EvaluatorKind.PROXY_MODEL,
            "proxy.emotion_music_match": EvaluatorKind.PROXY_MODEL,
        }
        for evaluator in evaluators:
            spec = evaluator.spec
            assert spec.evaluator_id in expected_kinds
            assert spec.kind is expected_kinds[spec.evaluator_id]
            assert spec.deterministic is True
            assert spec.cost_per_call is not None and spec.cost_per_call >= 0
            # 实现哈希入版本号（004 _versioning 同款：base+hash12）
            assert "+" in spec.version
            assert len(spec.version.rsplit("+", 1)[1]) == 12

    def test_对比样本_与004视觉系同构(self, sound_config, visual_config):
        """同工件经新旧评估器各评一次：得分域 [0,1] 与 diagnostics dict 结构一致。"""
        from agents.visual.evaluators.aesthetic import AestheticEvaluator

        artifact = ArtifactRef(artifact_hash="ef" * 32)
        # 004 视觉系：美学代理（确定性启发式同款哲学）
        visual_evaluator = AestheticEvaluator(visual_config.frame_sampling)
        frames = np.zeros((8, 64, 64, 3), dtype=np.uint8)
        visual_result = visual_evaluator.evaluate(
            artifact,
            {"samples": SimpleNamespace(frames_rgb=frames, frames_gray=frames[..., 0])},
        )
        # 006 声音系：响度门禁
        sound_evaluator = build_sound_evaluators(sound_config)[0]
        params = {"gen_type": "tts", "seed": 7, "duration_s": 2.0, "loudness_gain_db": 0.0}
        wav_bytes, metadata = synthesize_wav(params, _DIST, _SR)
        sound_result = sound_evaluator.evaluate(
            artifact,
            {
                "samples": decode_wav_samples(wav_bytes),
                "sample_rate": _SR,
                "gen_type": "tts",
                "metadata": metadata,
            },
        )
        for result in (visual_result, sound_result):
            assert isinstance(result, EvalResult)
            assert 0.0 <= result.score <= 1.0
            assert isinstance(result.diagnostics, dict) and result.diagnostics


def _calibrated_params(make_sound_gen_params, gen_type, seed):
    """响度标定到分档目标（真实评估器接线后 gate 必须能通过）。"""
    tier = {"tts": "dialogue", "sfx": "sfx", "music": "music"}[gen_type]
    target = float(_SOUND["loudness"][tier]["target_lufs"])
    wav0, _ = synthesize_wav(
        make_sound_gen_params(gen_type=gen_type, seed=seed, loudness_gain_db=0.0), _DIST, _SR
    )
    gain = target - measure_loudness_lufs(decode_wav_samples(wav0), _SR)
    return make_sound_gen_params(gen_type=gen_type, seed=seed, loudness_gain_db=gain)


class _RealEvalPolicy:
    """4 组参数桩（2 TTS + 1 SFX + 1 music），响度已标定。"""

    policy_version = "sound-real-eval-v1"

    def __init__(self, make_sound_gen_params):
        self._plans = [
            {"gen_type": gt, "gen_params": _calibrated_params(make_sound_gen_params, gt, seed)}
            for gt, seed in (("tts", 11), ("tts", 22), ("sfx", 33), ("music", 44))
        ]

    def plan(self, config, inputs):
        return self._plans


class Test执行器接线真实评估器:
    """T621：loop 不注入评估器 → 按 evaluator_weights.sound 装配真实四评估器。"""

    def test_真实评估器落树四分量齐全(
        self, make_sound_gen_params, make_timing_sheet, tree_store, artifact_store,
        sound_jobs_engine, sound_config,
    ):
        adapters = {
            "tts": SimulatedTTSGen(sound_config.simulated_gen, _SR),
            "sfx": SimulatedSFXGen(sound_config.simulated_gen, _SR),
            "music": SimulatedMusicGen(sound_config.simulated_gen, _SR),
        }
        inputs = {"timing_sheet": make_timing_sheet(), "mood": "悬疑"}
        result = run_sound_round(
            round_id="r-real",
            policy=_RealEvalPolicy(make_sound_gen_params),
            store=tree_store,
            artifacts=artifact_store,
            adapters=adapters,
            engine=sound_jobs_engine,
            config=sound_config,
            inputs=inputs,
        )
        assert [j["status"] for j in result.jobs] == ["inserted"] * 4

        tree = next(t for t in tree_store.trees_by(agent_id="sound") if t.tree_id == result.tree_id)
        snapshot = tree.config_snapshot
        # 版本元信息：四评估器实现哈希 + 合成口径
        assert set(snapshot["evaluator_versions"]) == {
            "rule.loudness_compliance",
            "rule.av_sync",
            "proxy.asr_transcript",
            "proxy.emotion_music_match",
        }
        assert all("+" in v for v in snapshot["evaluator_versions"].values())
        assert snapshot["composite_policy"] == COMPOSITE_POLICY

        children = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert len(children) == 4
        for node in children:
            assert node.status is NodeStatus.EVALUATED
            # eval_breakdown 四分量齐全（键 = evaluator_id@实现哈希版本）
            assert len(node.eval_breakdown) == 4
            bases = {k.rsplit("@", 1)[0] for k in node.eval_breakdown}
            assert bases == set(snapshot["evaluator_versions"])
            assert node.score == round(node.score, 6)  # quantize 6 位定点

        by_type = {n.observation_context["gen_type"]: n for n in children}
        # 类型不适用语义：TTS 节点 emotion 跳过注明；music 节点 asr 跳过注明
        tts_emotion = next(
            f for k, f in by_type["tts"].eval_breakdown.items() if "emotion" in k
        )
        assert tts_emotion["diagnostics"]["applicable"] is False
        music_asr = next(f for k, f in by_type["music"].eval_breakdown.items() if "asr" in k)
        assert music_asr["diagnostics"]["applicable"] is False
        # TTS：cer_injected=0 → asr 满分，归一后总分 = asr 得分 = 1.0
        assert by_type["tts"].score == pytest.approx(1.0)

    def test_重评估逐位一致(
        self, make_sound_gen_params, make_timing_sheet, tree_store, artifact_store,
        sound_jobs_engine, sound_config,
    ):
        """SC-004：同参数二次生成同一工件，真实评估器重评分逐位一致。"""
        evaluators = build_sound_evaluators(sound_config)
        params = _calibrated_params(make_sound_gen_params, "tts", 11)
        wav1, meta1 = synthesize_wav(params, _DIST, _SR)
        wav2, meta2 = synthesize_wav(params, _DIST, _SR)
        assert wav1 == wav2
        sheet = make_timing_sheet()
        ctx1 = {
            "samples": decode_wav_samples(wav1),
            "sample_rate": _SR,
            "gen_type": "tts",
            "metadata": meta1,
            "gen_params": params,
            "timing_sheet": sheet,
        }
        ctx2 = copy.deepcopy(ctx1)
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        for evaluator in evaluators:
            r1 = evaluator.evaluate(artifact, ctx1)
            r2 = evaluator.evaluate(artifact, ctx2)
            assert r1.score == r2.score
            assert r1.diagnostics == r2.diagnostics
