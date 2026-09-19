"""帧采样单测（T307，research 决策 1/3）。

ffprobe 元数据解析、等间隔 N=8 采样 64×64、同一片段逐字节确定性、
损坏文件受控报错不崩溃。
"""

import numpy as np
import pytest

from agents.visual.frames import (
    FrameDecodeError,
    probe_clip,
    sample_frames,
    sampling_spec_hash,
)


class Test探测:
    def test_ffprobe_元数据(self, clip_file):
        meta = probe_clip(clip_file)
        assert meta["width"] == 320 and meta["height"] == 240
        assert meta["fps"] == 8
        assert meta["duration_seconds"] == pytest.approx(2.0)
        assert meta["codec"] == "h264"

    def test_损坏文件受控报错(self, tmp_path):
        broken = tmp_path / "broken.mp4"
        broken.write_bytes(b"not-a-video")
        with pytest.raises(FrameDecodeError):
            probe_clip(broken)
        with pytest.raises(FrameDecodeError):
            sample_frames(broken, {"count": 8, "size": 64})

    def test_不存在文件受控报错(self, tmp_path):
        with pytest.raises(FrameDecodeError):
            probe_clip(tmp_path / "ghost.mp4")


class Test采样:
    def test_采样形状与规格(self, clip_file, visual_config):
        samples = sample_frames(clip_file, visual_config.frame_sampling)
        assert samples.frames_gray.shape == (8, 64, 64)
        assert samples.frames_rgb.shape == (8, 64, 64, 3)
        assert len(samples.frame_indices) == 8
        # 等间隔含首尾
        assert samples.frame_indices[0] == 0
        assert samples.frame_indices[-1] == 15  # 16 帧片段的最后一帧
        assert samples.sampling_spec == visual_config.frame_sampling

    def test_同一片段逐字节确定(self, clip_file, visual_config):
        a = sample_frames(clip_file, visual_config.frame_sampling)
        b = sample_frames(clip_file, visual_config.frame_sampling)
        np.testing.assert_array_equal(a.frames_gray, b.frames_gray)
        np.testing.assert_array_equal(a.frames_rgb, b.frames_rgb)
        assert a.frame_indices == b.frame_indices

    def test_采样规格哈希稳定(self, visual_config):
        h1 = sampling_spec_hash(visual_config.frame_sampling)
        h2 = sampling_spec_hash(dict(reversed(list(visual_config.frame_sampling.items()))))
        assert h1 == h2 and len(h1) == 12

    def test_FrameSamples_灰度值域(self, clip_file, visual_config):
        samples = sample_frames(clip_file, visual_config.frame_sampling)
        assert samples.frames_gray.min() >= 0 and samples.frames_gray.max() <= 255
