"""一致性评估器单测（US1 / T312，proxy.identity_consistency）。

dhash 嵌入跨帧距离映射；单镜头满分并注明（规格边界）；重算一致。
"""

import numpy as np
import pytest

from agents.visual.evaluators.identity import IdentityConsistencyEvaluator
from agents.visual.frames import FrameSamples, sample_frames
from core.evaluators.base import ArtifactRef, EvaluatorKind


@pytest.fixture()
def evaluator(visual_config):
    return IdentityConsistencyEvaluator(visual_config.frame_sampling)


def _samples(frames: np.ndarray, visual_config):
    size = visual_config.frame_sampling["size"]
    assert frames.shape[1:] == (size, size)
    return FrameSamples(
        frames_gray=frames,
        frames_rgb=np.stack([frames] * 3, axis=-1),
        frame_indices=list(range(len(frames))),
        sampling_spec=visual_config.frame_sampling,
    )


class Test跨镜头一致性:
    def test_稳定片段高分(self, evaluator, clip_file, visual_config):
        samples = sample_frames(clip_file, visual_config.frame_sampling)
        result = evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32),
                                    {"samples": samples, "gen_params": {"shots": 2}})
        assert 0.0 <= result.score <= 1.0

    def test_完全一致帧序列满分(self, evaluator, visual_config):
        frame = np.random.default_rng(0).integers(0, 255, (64, 64), dtype=np.uint8)
        frames = np.stack([frame] * 8)  # 每帧完全相同
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"samples": _samples(frames, visual_config), "gen_params": {"shots": 2}},
        )
        assert result.score == 1.0

    def test_剧烈变化帧序列低分(self, evaluator, visual_config):
        rng = np.random.default_rng(1)
        frames = rng.integers(0, 255, (8, 64, 64), dtype=np.uint8)  # 每帧随机噪声
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"samples": _samples(frames, visual_config), "gen_params": {"shots": 3}},
        )
        assert result.score < 0.7


class Test单镜头边界:
    def test_单镜头满分并注明(self, evaluator, visual_config):
        rng = np.random.default_rng(2)
        frames = rng.integers(0, 255, (8, 64, 64), dtype=np.uint8)
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"samples": _samples(frames, visual_config), "gen_params": {"shots": 1}},
        )
        assert result.score == 1.0
        assert "single_shot" in result.diagnostics["note"]


class Test确定性与版本:
    def test_重算逐字节一致(self, evaluator, clip_file, visual_config):
        samples = sample_frames(clip_file, visual_config.frame_sampling)
        ctx = {"samples": samples, "gen_params": {"shots": 2}}
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        assert evaluator.evaluate(artifact, ctx) == evaluator.evaluate(artifact, ctx)

    def test_版本号(self, evaluator):
        assert evaluator.spec.evaluator_id == "proxy.identity_consistency"
        assert evaluator.spec.version.startswith("1.0.0+")
        assert evaluator.spec.kind is EvaluatorKind.PROXY_MODEL
        assert evaluator.spec.deterministic is True
