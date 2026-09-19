"""美学统计代理评估器单测（US1 / T311，proxy.aesthetic）。

帧统计合成映射 [0,1]、退化输入（全黑/全白）合法值不崩溃、
同工件重算逐字节一致（quantize 后）、版本号携带实现+采样哈希。
"""

import numpy as np
import pytest

from agents.visual.evaluators.aesthetic import AestheticEvaluator
from agents.visual.frames import sample_frames
from core.evaluators.base import ArtifactRef, EvaluatorKind


@pytest.fixture()
def evaluator(visual_config):
    return AestheticEvaluator(visual_config.frame_sampling)


@pytest.fixture()
def samples(clip_file, visual_config):
    return sample_frames(clip_file, visual_config.frame_sampling)


def _flat_samples(value: int, visual_config):
    """退化输入：纯色帧序列（全黑或全白）。"""
    n = visual_config.frame_sampling["count"]
    size = visual_config.frame_sampling["size"]
    from agents.visual.frames import FrameSamples

    rgb = np.full((n, size, size, 3), value, dtype=np.uint8)
    return FrameSamples(
        frames_gray=np.full((n, size, size), value, dtype=np.uint8),
        frames_rgb=rgb,
        frame_indices=list(range(n)),
        sampling_spec=visual_config.frame_sampling,
    )


class Test评分映射:
    def test_得分在合法区间(self, evaluator, samples):
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"samples": samples, "gen_params": {}}
        )
        assert 0.0 <= result.score <= 1.0
        assert set(result.diagnostics) >= {"brightness", "contrast", "colorfulness", "sharpness"}

    def test_程序化片段得分高于全黑(self, evaluator, samples, visual_config):
        good = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"samples": samples, "gen_params": {}}
        )
        black = evaluator.evaluate(
            ArtifactRef(artifact_hash="cd" * 32),
            {"samples": _flat_samples(0, visual_config), "gen_params": {}},
        )
        assert good.score > black.score

    def test_退化输入合法值不产生NaN(self, evaluator, visual_config):
        for value in (0, 255):
            result = evaluator.evaluate(
                ArtifactRef(artifact_hash="ab" * 32),
                {"samples": _flat_samples(value, visual_config), "gen_params": {}},
            )
            assert 0.0 <= result.score <= 1.0
            assert result.score == result.score  # 非 NaN


class Test确定性与版本:
    def test_重算逐字节一致(self, evaluator, samples):
        ctx = {"samples": samples, "gen_params": {}}
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        assert evaluator.evaluate(artifact, ctx) == evaluator.evaluate(artifact, ctx)

    def test_版本号携带实现与采样哈希(self, evaluator, visual_config):
        assert evaluator.spec.evaluator_id == "proxy.aesthetic"
        assert evaluator.spec.version.startswith("1.0.0+")
        assert len(evaluator.spec.version) == len("1.0.0+") + 12
        other = AestheticEvaluator({"count": 4, "size": 64})  # 采样变 → 版本变
        assert other.spec.version != evaluator.spec.version

    def test_spec_kind与确定性(self, evaluator):
        assert evaluator.spec.kind is EvaluatorKind.PROXY_MODEL
        assert evaluator.spec.deterministic is True
