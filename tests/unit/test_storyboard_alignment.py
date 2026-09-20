"""proxy.emotion_alignment 评估器单测（功能 008 / T821，先于实现编写）。

C7（澄清 Q2 口径）：**输入 = 预演画面帧像素**——分镜卡帧由 board_render.storyboard_cards
同一函数产出（帧哈希与渲染元数据一致，禁止"评估看到的"与"渲染出的"两套帧）；
帧特征 = 逐镜分镜卡像素均值（3 维归一化色板向量），与剧本情绪基调向量（该场景已标注
行的情绪向量均值）做余弦 → 映射得分（cos ≤ cos_floor → 0 分，定点 6 位）。
对齐 vs 背离分差显著；手算平行口径误差 < 1e-6；同预演重评估逐位一致；
情绪基调缺失 → "不适用"注明（合成按适用分量归一）。
"""

import copy
import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from agents.storyboard.board_render import storyboard_cards
from agents.storyboard.config import StoryboardConfig
from agents.storyboard.evaluators.alignment import EmotionAlignmentEvaluator
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList
from core.evaluators.base import ArtifactRef, EvaluatorKind

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_VECTORS = {
    "calm": [0.30, 0.55, 0.45],
    "tense": [0.75, 0.18, 0.15],
    "joyful": [0.90, 0.78, 0.20],
    "sorrow": [0.15, 0.25, 0.60],
    "awe": [0.55, 0.30, 0.80],
}
_ALIGNMENT = {"cos_floor": 0.90}


def _config() -> StoryboardConfig:
    """渲染配置：真实 storyboard 段缩小尺寸（64x48）以控制单测耗时。"""
    config = copy.deepcopy(_REAL_CONFIG)
    config["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(config)


@pytest.fixture()
def config():
    return _config()


def _script(*, emotions: dict[str, str | None] | None = None) -> ScriptSegment:
    """对照剧本：scene-1 两行（s1-l1 关键行 + s1-l2），情绪基调可注入。"""
    values = {
        "s1-l1": "tense",
        "s1-l2": "sorrow",
        **(emotions or {}),
    }
    return ScriptSegment(
        scenes=[
            {
                "scene_id": "scene-1",
                "axis_base": "A",
                "lines": [
                    {
                        "line_id": "s1-l1",
                        "kind": "dialogue",
                        "text": "别出声。",
                        "key": True,
                        "emotion": values["s1-l1"],
                    },
                    {
                        "line_id": "s1-l2",
                        "kind": "action",
                        "text": "她缓缓坐下。",
                        "emotion": values["s1-l2"],
                    },
                ],
            }
        ]
    )


def _shot(shot_id: str, covers: list[str], *, est_duration_ms: int = 1000) -> dict:
    return {
        "shot_id": shot_id,
        "scene_id": "scene-1",
        "covers": covers,
        "shot_size": "medium",
        "camera": "eye_level",
        "side": "A",
        "movement": "static",
        "est_duration_ms": est_duration_ms,
        "alternatives": 1,
    }


def _aligned_shotlist() -> ShotList:
    """对齐：逐行承接（预演画面的情绪分布 = 剧本场景的情绪分布）。"""
    return ShotList(shots=[_shot("shot-01", ["s1-l1"]), _shot("shot-02", ["s1-l2"])])


def _diverged_shotlist() -> ShotList:
    """背离：两镜全部承接 tense 行（预演画面基调偏离场景整体情绪）。"""
    return ShotList(shots=[_shot("shot-01", ["s1-l1"]), _shot("shot-02", ["s1-l1"])])


@pytest.fixture()
def evaluator(config):
    return EmotionAlignmentEvaluator(
        config.alignment, config.render, config.shot_grammar, config.emotion_vectors
    )


def _artifact(shotlist: ShotList, script: ScriptSegment, config: StoryboardConfig) -> ArtifactRef:
    """走真实渲染路径取工件（元数据即渲染元数据，含 frames_hash）。"""
    animatic = SimulatedStoryboardRenderer().render(shotlist, script, config)
    return ArtifactRef(artifact_hash="ab" * 32, metadata=animatic.metadata)


def _ctx(shotlist: ShotList, script: ScriptSegment) -> dict:
    return {"shotlist": shotlist, "script": script}


class Test对齐度打分:
    def test_对齐预演得分接近满分(self, evaluator, config):
        """C7 场景 4：对齐预演（情绪分布一致）→ 高得分；帧来源经元数据校验一致。"""
        script, shotlist = _script(), _aligned_shotlist()
        artifact = _artifact(shotlist, script, config)
        result = evaluator.evaluate(artifact, _ctx(shotlist, script))
        assert result.diagnostics["applicable"] is True
        assert result.diagnostics["frames_verified"] is True
        assert result.diagnostics["frames_hash"] == artifact.metadata["frames_hash"]
        assert result.diagnostics["cosine"] > 0.99
        assert result.score > 0.95

    def test_背离预演判0且分差显著(self, evaluator, config):
        """C7 场景 4：背离预演（画面基调偏离剧本场景情绪）→ 分差显著。"""
        script = _script()
        aligned = evaluator.evaluate(
            _artifact(_aligned_shotlist(), script, config), _ctx(_aligned_shotlist(), script)
        )
        diverged = evaluator.evaluate(
            _artifact(_diverged_shotlist(), script, config), _ctx(_diverged_shotlist(), script)
        )
        assert diverged.score == 0.0  # 余弦低于下限 → 0 分
        assert aligned.score - diverged.score > 0.5  # 分差显著

    def test_手算平行口径误差小于1e_6(self, evaluator, config):
        """独立手算（像素均值 → 场景聚合 → 余弦 → 下限映射）复算得分。"""
        script, shotlist = _script(), _aligned_shotlist()
        result = evaluator.evaluate(_artifact(shotlist, script, config), _ctx(shotlist, script))
        cards = storyboard_cards(
            shotlist,
            script,
            render_cfg=config.render,
            grammar_rules=config.shot_grammar,
            emotion_vectors=config.emotion_vectors,
        )
        features = [
            cards.frames[index].reshape(-1, 3).mean(axis=0) / 255.0
            for index in range(len(shotlist.shots))
        ]
        feature = np.asarray(features).mean(axis=0)  # 两镜时长相同 → 权重相等
        target = np.asarray([_VECTORS["tense"], _VECTORS["sorrow"]]).mean(axis=0)
        cosine = float(np.dot(feature, target) / (np.linalg.norm(feature) * np.linalg.norm(target)))
        floor = _ALIGNMENT["cos_floor"]
        expected = max(0.0, min(1.0, (cosine - floor) / (1.0 - floor)))
        assert result.score == pytest.approx(round(expected, 6), abs=1e-6)
        assert result.score == round(result.score, 6)  # 定点 6 位
        assert result.diagnostics["cosine"] == pytest.approx(cosine, abs=1e-9)

    def test_同预演重评估逐位一致(self, evaluator, config):
        """C7 场景 4：同预演重评估 → score/diagnostics 逐位一致。"""
        script, shotlist = _script(), _aligned_shotlist()
        artifact = _artifact(shotlist, script, config)
        first = evaluator.evaluate(artifact, _ctx(shotlist, script))
        second = evaluator.evaluate(artifact, _ctx(shotlist, script))
        assert first.score == second.score
        assert first.diagnostics == second.diagnostics

    def test_对齐口径配置驱动(self, config):
        """cos_floor 配置驱动：下限放宽（0.5）→ 背离预演不再是 0 分。"""
        script, shotlist = _script(), _diverged_shotlist()
        artifact = _artifact(shotlist, script, config)
        lenient = EmotionAlignmentEvaluator(
            {"cos_floor": 0.5}, config.render, config.shot_grammar, config.emotion_vectors
        )
        strict = EmotionAlignmentEvaluator(
            {"cos_floor": 0.9}, config.render, config.shot_grammar, config.emotion_vectors
        )
        lenient_result = lenient.evaluate(artifact, _ctx(shotlist, script))
        assert strict.evaluate(artifact, _ctx(shotlist, script)).score == 0.0
        assert lenient_result.diagnostics["cos_floor"] == 0.5
        assert 0.0 < lenient_result.score < 1.0  # 余弦 ∈ (0.5, 1) → 线性映射生效


class Test不适用与来源一致:
    def test_情绪缺失不适用注明(self, evaluator, config):
        """剧本未标注情绪 → 不适用（score=0 + 注明；合成按适用分量归一）。"""
        script = _script(emotions={"s1-l1": None, "s1-l2": None})
        shotlist = _aligned_shotlist()
        result = evaluator.evaluate(_artifact(shotlist, script, config), _ctx(shotlist, script))
        assert result.score == 0.0
        assert result.diagnostics["applicable"] is False
        assert "不适用" in result.diagnostics["note"]

    def test_帧哈希不一致如实标注不适用(self, evaluator, config):
        """禁止两套帧：元数据帧哈希与同一函数重算不符 → 不适用 + 注明来源不一致。"""
        script, shotlist = _script(), _aligned_shotlist()
        artifact = _artifact(shotlist, script, config)
        tampered = ArtifactRef(
            artifact_hash=artifact.artifact_hash,
            metadata={**artifact.metadata, "frames_hash": "cd" * 32},
        )
        result = evaluator.evaluate(tampered, _ctx(shotlist, script))
        assert result.score == 0.0
        assert result.diagnostics["applicable"] is False
        assert result.diagnostics["frames_verified"] is False
        assert "两套帧" in result.diagnostics["note"] or "不一致" in result.diagnostics["note"]

    def test_读帧特征与逐镜情绪清单落盘(self, evaluator, config):
        """评估输入可审计：逐镜情绪与帧哈希入 diagnostics（judge/回放可核对）。"""
        script, shotlist = _script(), _aligned_shotlist()
        artifact = _artifact(shotlist, script, config)
        result = evaluator.evaluate(artifact, _ctx(shotlist, script))
        assert result.diagnostics["emotions"] == ["tense", "sorrow"]
        assert result.diagnostics["labeled_shot_count"] == 2
        assert result.diagnostics["shot_count"] == 2
        assert len(result.diagnostics["frame_hashes"]) == 2


class Test注册元数据:
    def test_注册元数据(self, evaluator):
        """宪章三件套之注册元数据：PROXY_MODEL / deterministic / 零成本显式 / 实现哈希版本。"""
        spec = evaluator.spec
        assert spec.evaluator_id == "proxy.emotion_alignment"
        assert spec.kind is EvaluatorKind.PROXY_MODEL
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert re.fullmatch(r"1\.0\.0\+a[0-9a-f]{12}f[0-9a-f]{8}", spec.version)

    def test_版本含帧产出函数哈希(self, evaluator):
        """帧产出函数（board_render）变更即对齐代理版本变更（原则一）。"""
        from agents.storyboard import board_render

        assert board_render.frame_function_hash() in evaluator.spec.version
