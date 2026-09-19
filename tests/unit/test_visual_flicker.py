"""闪烁/伪影评估器单测（US1 / T313，proxy.flicker）。

相邻帧亮度差方差 + 高频伪影能量，反向映射（越稳越高分）；
稳定片段高分、闪烁片段低分、退化输入合法、重算一致。
"""

import numpy as np
import pytest

from agents.visual.evaluators.flicker import FlickerEvaluator
from agents.visual.frames import FrameSamples, sample_frames
from core.evaluators.base import ArtifactRef


@pytest.fixture()
def evaluator(visual_config):
    return FlickerEvaluator(visual_config.frame_sampling)


def _samples(frames: np.ndarray, visual_config):
    return FrameSamples(
        frames_gray=frames,
        frames_rgb=np.stack([frames] * 3, axis=-1),
        frame_indices=list(range(len(frames))),
        sampling_spec=visual_config.frame_sampling,
    )


class Test闪烁检测:
    def test_稳定片段高分(self, evaluator, visual_config):
        frame = np.full((64, 64), 128, dtype=np.uint8)
        frames = np.stack([frame] * 8)  # 完全静止
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"samples": _samples(frames, visual_config), "gen_params": {}},
        )
        assert result.score == 1.0

    def test_闪烁片段低分(self, evaluator, visual_config):
        black = np.full((64, 64), 0, dtype=np.uint8)
        white = np.full((64, 64), 255, dtype=np.uint8)
        frames = np.stack([black, white] * 4)  # 黑白交替强闪烁
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"samples": _samples(frames, visual_config), "gen_params": {}},
        )
        assert result.score < 0.3

    def test_程序化片段得分居中(self, evaluator, clip_file, visual_config):
        samples = sample_frames(clip_file, visual_config.frame_sampling)
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"samples": samples, "gen_params": {}}
        )
        assert 0.0 <= result.score <= 1.0

    def test_退化输入合法不NaN(self, evaluator, visual_config):
        frames = np.zeros((8, 64, 64), dtype=np.uint8)
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"samples": _samples(frames, visual_config), "gen_params": {}},
        )
        assert result.score == result.score and 0.0 <= result.score <= 1.0


class Test确定性与版本:
    def test_重算逐字节一致(self, evaluator, clip_file, visual_config):
        samples = sample_frames(clip_file, visual_config.frame_sampling)
        ctx = {"samples": samples, "gen_params": {}}
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        assert evaluator.evaluate(artifact, ctx) == evaluator.evaluate(artifact, ctx)

    def test_版本号(self, evaluator):
        assert evaluator.spec.evaluator_id == "proxy.flicker"
        assert evaluator.spec.version.startswith("1.0.0+")
        assert evaluator.spec.deterministic is True
