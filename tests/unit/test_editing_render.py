"""确定性渲染合成单测（功能 007 / T709，先于实现编写）。

C11 落点：EDL + 素材帧 → numpy 定点拼接/叠化 alpha 混合/混音增益叠加 → mp4；
编码固定单线程确定性档（research 决策 2，消除 004 x264 多线程 flake 根因）；
同 EDL 两次渲染逐字节一致（SC-002）；元数据 = 总时长/镜头时长序列/转场序列/音轨标记。
"""

import io

import imageio.v3 as iio
import pytest

from agents.editing import render
from agents.editing.edl import EditDecisionList
from core.tree.errors import ValidationError

_FPS = 8


@pytest.fixture()
def frames(make_shot_frames):
    return make_shot_frames(fps=_FPS)


@pytest.fixture()
def audio(make_audio_tracks):
    return make_audio_tracks()


def _render(edl, frames, audio_tracks=None):
    return render.render_film(
        edl, frames, audio_by_track=audio_tracks or {}, fps=_FPS, sample_rate=16000
    )


class Test确定性:
    def test_同_EDL_两次渲染逐字节一致(self, make_edl, frames, audio):
        """SC-002：同 EDL + 同素材 → mp4 字节完全一致（单线程编码档 + 定点运算）。"""
        edl = make_edl()
        mp4_a, _ = _render(edl, frames, audio)
        mp4_b, _ = _render(edl, frames, audio)
        assert mp4_a == mp4_b

    def test_无音轨同样逐字节一致(self, make_edl, frames):
        edl = make_edl(audio=[])
        assert _render(edl, frames)[0] == _render(edl, frames)[0]

    def test_编码参数为单线程确定性档(self):
        """004 flake 根因消除的断言点：编码参数固定 threads=1。"""
        assert "-threads" in render.ENCODE_FFMPEG_PARAMS
        assert render.ENCODE_FFMPEG_PARAMS[render.ENCODE_FFMPEG_PARAMS.index("-threads") + 1] == "1"


class Test拼接与转场:
    def test_元数据镜头时长序列与_EDL_一致(self, make_edl, frames, audio):
        edl = make_edl()
        _, meta = _render(edl, frames, audio)
        assert meta["shot_durations_ms"] == [c.out_ms - c.in_ms for c in edl.clips]
        assert meta["transitions"] == [c.transition.to_dict() for c in edl.clips]

    def test_总时长扣除叠化重叠(self, make_edl, frames):
        """叠化两段重叠一次：总时长 = 段长和 − Σ叠化时长（cut/fade 不压缩）。"""
        edl = make_edl(audio=[])
        _, meta = _render(edl, frames)
        expected = edl.total_duration_ms()
        assert meta["duration_ms"] == expected
        # 帧数口径复核：duration_ms = 帧数 × 1000 / fps
        decoded = iio.imread(io.BytesIO(_render(edl, frames)[0]), extension=".mp4")
        assert len(decoded) * 1000 // _FPS == expected

    def test_全_cut_总时长为段长和(self, make_edl, frames):
        clips = [
            {"shot_id": "shot-1", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-3", "in_ms": 0, "out_ms": 3000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ]
        _, meta = _render(make_edl(clips=clips, audio=[]), frames)
        assert meta["duration_ms"] == 5000

    def test_叠化帧内容为定点混合(self, frames):
        """叠化区间首尾帧分别接近前段尾与后段头（定点 alpha 混合生效）。"""
        import numpy as np

        edl = EditDecisionList(
            clips=[
                {"shot_id": "shot-1", "in_ms": 0, "out_ms": 2000,
                 "transition": {"type": "dissolve", "duration_ms": 1000}},
                {"shot_id": "shot-2", "in_ms": 0, "out_ms": 2000,
                 "transition": {"type": "cut", "duration_ms": 0}},
            ]
        )
        frames_out = render.compose_frames(edl, frames, fps=_FPS)
        # 总帧数 = 16 + 16 − 8（1 秒叠化）
        assert len(frames_out) == 24
        blend_start = frames_out[8]  # 叠化区首帧：α 小 → 接近前段
        blend_end = frames_out[15]  # 叠化区末帧：α 大 → 接近后段
        prev_tail = frames["shot-1"][8:]
        next_head = frames["shot-2"][:8]
        assert np.abs(blend_start.astype(int) - prev_tail[0].astype(int)).mean() < np.abs(
            blend_start.astype(int) - next_head[0].astype(int)
        ).mean()
        assert np.abs(blend_end.astype(int) - next_head[-1].astype(int)).mean() < np.abs(
            blend_end.astype(int) - prev_tail[-1].astype(int)
        ).mean()


class Test混音:
    def test_带音轨标记(self, make_edl, frames, audio):
        _, meta = _render(make_edl(), frames, audio)
        assert meta["has_audio"] is True

    def test_无音轨标记(self, make_edl, frames):
        _, meta = _render(make_edl(audio=[]), frames)
        assert meta["has_audio"] is False

    def test_混音增益叠加确定性(self, make_edl, frames, audio):
        """定点增益叠加：同输入重算采样序列逐位一致；增益 0.8 的 RMS 小于原轨。"""
        import numpy as np

        edl = make_edl()
        mix_a = render.mix_audio(edl, audio, total_ms=4000, sample_rate=16000)
        mix_b = render.mix_audio(edl, audio, total_ms=4000, sample_rate=16000)
        assert np.array_equal(mix_a, mix_b)
        head = mix_a[:16000]  # bgm-01 摆放在 0ms
        assert np.abs(head).mean() < np.abs(audio["bgm-01"]).mean()  # gain 0.8 衰减生效


class Test素材校验:
    def test_素材帧缺失拒绝(self, make_edl, frames):
        del frames["shot-1"]
        with pytest.raises(ValidationError, match="shot-1"):
            _render(make_edl(), frames)

    def test_帧数不足拒绝(self, make_edl, frames):
        """素材帧短于 clip 出点对应的帧号 → 执行前拒绝。"""
        frames["shot-5"] = frames["shot-5"][:10]  # 出点 5000ms 需 40 帧
        with pytest.raises(ValidationError, match="shot-5"):
            _render(make_edl(), frames)
