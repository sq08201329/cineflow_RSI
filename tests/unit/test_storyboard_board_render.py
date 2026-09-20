"""分镜卡帧生成与预演合成单测（功能 008 / T809，先于实现编写）。

C11 落点：ShotList → 每镜一张分镜卡（程序化构图表达景别/机位/运动 + 情绪色板注入）
→ 拼接（可选临时音轨）→ mp4；**编码单线程确定性档**（007 同参数，消除 x264 多线程
编码的非确定性）；同 ShotList 两次渲染逐字节一致（SC-002）。
**分镜卡帧由同一函数产出**（澄清 Q2）：元数据带帧哈希，评估器读同一函数产出——
禁止"评估看到的"与"渲染出的"两套帧。
"""

import io

import imageio.v3 as iio
import numpy as np
import pytest

from agents.storyboard import board_render
from agents.storyboard.shotlist import ShotList
from agents.visual.frames import probe_clip
from core.tree.errors import ValidationError

_FPS = 8

_GRAMMAR = {
    "shot_sizes": ["extreme_close_up", "close_up", "medium", "full", "wide"],
    "max_size_jump": 2,
    "max_same_size_run": 2,
    "camera_positions": ["eye_level", "low_angle", "high_angle", "over_shoulder", "side"],
    "movements": ["static", "pan", "tilt", "dolly", "handheld"],
}

_VECTORS = {
    "calm": [0.30, 0.55, 0.45],
    "tense": [0.75, 0.18, 0.15],
    "joyful": [0.90, 0.78, 0.20],
    "sorrow": [0.15, 0.25, 0.60],
    "awe": [0.55, 0.30, 0.80],
}


@pytest.fixture()
def script(make_script_segment):
    return make_script_segment()


@pytest.fixture()
def render_cfg():
    """渲染配置：真实 storyboard.render 段缩小尺寸（64x48）以控制单测耗时。"""
    return {
        "fps": _FPS,
        "width": 64,
        "height": 48,
        "codec": "libx264",
        "encode_threads": 1,
        "price_per_shot_usd": 0.06,
    }


def _cards(shotlist, script, render_cfg, **overrides):
    return board_render.storyboard_cards(
        shotlist,
        script,
        render_cfg=render_cfg,
        grammar_rules=_GRAMMAR,
        emotion_vectors=_VECTORS,
        **overrides,
    )


def _render(shotlist, script, render_cfg, **overrides):
    return board_render.render_animatic(
        shotlist,
        script,
        render_cfg=render_cfg,
        grammar_rules=_GRAMMAR,
        emotion_vectors=_VECTORS,
        **overrides,
    )


def _subject_pixels(card: np.ndarray) -> int:
    """主体像素数口径：亮度低于卡内峰值 65% 的像素（主体框 + 索引条暗块）。"""
    luma = card.mean(axis=2)
    return int((luma < 0.65 * luma.max()).sum())


class Test分镜卡帧:
    def test_每镜一张卡且尺寸符合配置(self, make_shotlist, script, render_cfg):
        cards = _cards(make_shotlist(), script, render_cfg)
        assert cards.frames.shape == (9, render_cfg["height"], render_cfg["width"], 3)
        assert len(cards.frame_hashes) == 9
        assert len(cards.emotions) == 9

    def test_景别档位决定主体占比(self, script, render_cfg):
        """景别越远（档位序号越大）主体占比越小——程序化构图表达景别。"""

        def _card(shot_size: str) -> np.ndarray:
            return board_render.render_shot_card(
                {
                    "shot_id": "shot-x",
                    "scene_id": "scene-1",
                    "covers": ["s1-l1"],
                    "shot_size": shot_size,
                    "camera": "eye_level",
                    "side": "A",
                    "movement": "static",
                    "est_duration_ms": 1000,
                    "alternatives": 1,
                },
                index=0,
                emotion="tense",
                render_cfg=render_cfg,
                grammar_rules=_GRAMMAR,
                emotion_vectors=_VECTORS,
            )

        areas = [_subject_pixels(_card(size)) for size in _GRAMMAR["shot_sizes"]]
        assert areas == sorted(areas, reverse=True)
        assert areas[0] > areas[-1]  # 特写主体显著大于全景

    def test_机位与运动改变构图(self, script, render_cfg):
        base = {
            "shot_id": "shot-x",
            "scene_id": "scene-1",
            "covers": ["s1-l1"],
            "shot_size": "medium",
            "camera": "eye_level",
            "side": "A",
            "movement": "static",
            "est_duration_ms": 1000,
            "alternatives": 1,
        }

        def _card(**overrides) -> np.ndarray:
            return board_render.render_shot_card(
                {**base, **overrides},
                index=0,
                emotion="tense",
                render_cfg=render_cfg,
                grammar_rules=_GRAMMAR,
                emotion_vectors=_VECTORS,
            )

        assert not np.array_equal(_card(), _card(camera="side"))
        assert not np.array_equal(_card(), _card(side="B"))
        assert not np.array_equal(_card(), _card(movement="dolly"))

    def test_索引条可解码(self, make_shotlist, script, render_cfg):
        """顶部索引条编码镜头序号（构图可机检，非像素比对依赖）。"""
        cards = _cards(make_shotlist(), script, render_cfg)
        for index, frame in enumerate(cards.frames):
            assert board_render.decode_index_code(frame) == index

    def test_情绪色板注入可测(self, script, render_cfg):
        """澄清 Q2：情绪基调注入分镜卡像素（对齐代理读同一批帧做余弦）。"""

        def _mean(emotion: str | None) -> np.ndarray:
            card = board_render.render_shot_card(
                {
                    "shot_id": "shot-x",
                    "scene_id": "scene-1",
                    "covers": ["s1-l1"],
                    "shot_size": "medium",
                    "camera": "eye_level",
                    "side": "A",
                    "movement": "static",
                    "est_duration_ms": 1000,
                    "alternatives": 1,
                },
                index=0,
                emotion=emotion,
                render_cfg=render_cfg,
                grammar_rules=_GRAMMAR,
                emotion_vectors=_VECTORS,
            )
            return card.reshape(-1, 3).mean(axis=0)

        tense = _mean("tense")
        calm = _mean("calm")
        assert tense[0] > tense[2]  # 紧张：暗红（R 通道主导）
        assert calm[1] > calm[0] and calm[2] > calm[0]  # 平静：青绿（G/B 主导）

    def test_未标注情绪用中性色板(self, script, render_cfg):
        card = board_render.render_shot_card(
            {
                "shot_id": "shot-x",
                "scene_id": "scene-1",
                "covers": ["s1-l1"],
                "shot_size": "medium",
                "camera": "eye_level",
                "side": "A",
                "movement": "static",
                "est_duration_ms": 1000,
                "alternatives": 1,
            },
            index=0,
            emotion=None,
            render_cfg=render_cfg,
            grammar_rules=_GRAMMAR,
            emotion_vectors=_VECTORS,
        )
        mean = card.reshape(-1, 3).mean(axis=0)
        assert mean.max() - mean.min() < 24  # 中性灰：通道差异小

    def test_未登记情绪拒绝(self, script, render_cfg):
        with pytest.raises(ValidationError, match="melancholic"):
            board_render.emotion_palette("melancholic", _VECTORS)

    def test_镜头情绪取首个带标注的承接行(self, make_shotlist, script, render_cfg):
        """承接多行时取顺序上首个带情绪标注的行；全部未标注 → 不适用。"""
        cards = _cards(make_shotlist(), script, render_cfg)
        assert cards.emotions[0] == "tense"  # shot-01 承接 s1-l1（tense）
        assert cards.emotions[2] is None  # shot-03 承接 s1-l3（未标注情绪）


class Test拼接与元数据:
    def test_帧数等于时长网格(self, make_shotlist, script, render_cfg):
        """逐镜帧数 = est_duration_ms × fps // 1000（时长估算的帧网格口径）。"""
        shotlist = make_shotlist()
        cards = _cards(shotlist, script, render_cfg)
        frames = board_render.compose_frames(shotlist, cards, render_cfg)
        expected = sum(shot.est_duration_ms * _FPS // 1000 for shot in shotlist.shots)
        assert len(frames) == expected == 82

    def test_元数据键齐全且与_ShotList_一致(self, make_shotlist, script, render_cfg):
        shotlist = make_shotlist()
        _, meta = _render(shotlist, script, render_cfg)
        for key in (
            "duration_ms",
            "shot_count",
            "shot_sizes",
            "shot_durations_ms",
            "shot_frame_counts",
            "frame_hashes",
            "frames_hash",
            "has_temp_audio",
            "fps",
            "emotions",
        ):
            assert key in meta, f"元数据缺键 {key}"
        assert meta["shot_count"] == 9
        assert meta["shot_sizes"] == [shot.shot_size for shot in shotlist.shots]
        assert meta["shot_durations_ms"] == [shot.est_duration_ms for shot in shotlist.shots]
        assert meta["duration_ms"] == 82 * 1000 // _FPS
        assert meta["fps"] == _FPS

    def test_帧哈希形态与镜头数一致(self, make_shotlist, script, render_cfg):
        _, meta = _render(make_shotlist(), script, render_cfg)
        assert len(meta["frame_hashes"]) == 9
        assert all(len(h) == 64 and h == h.lower() for h in meta["frame_hashes"])
        assert len(meta["frames_hash"]) == 64

    def test_mp4_可探测且规格符合配置(self, make_shotlist, script, render_cfg, tmp_path):
        mp4_bytes, meta = _render(make_shotlist(), script, render_cfg)
        path = tmp_path / "animatic.mp4"
        path.write_bytes(mp4_bytes)
        probed = probe_clip(path)
        assert probed["fps"] == pytest.approx(float(render_cfg["fps"]))
        assert probed["width"] == render_cfg["width"]
        assert probed["height"] == render_cfg["height"]
        assert probed["duration_seconds"] == pytest.approx(meta["duration_ms"] / 1000.0, abs=0.26)


class Test确定性:
    def test_同_ShotList_两次渲染逐字节一致(self, make_shotlist, script, render_cfg):
        """SC-002：单线程编码档 + 定点构图 → mp4 字节与元数据完全一致。"""
        shotlist = make_shotlist()
        first_bytes, first_meta = _render(shotlist, script, render_cfg)
        second_bytes, second_meta = _render(shotlist, script, render_cfg)
        assert first_bytes == second_bytes
        assert first_meta == second_meta

    def test_同_ShotList_两次分镜卡逐字节一致(self, make_shotlist, script, render_cfg):
        shotlist = make_shotlist()
        first = _cards(shotlist, script, render_cfg)
        second = _cards(shotlist, script, render_cfg)
        assert np.array_equal(first.frames, second.frames)
        assert first.frame_hashes == second.frame_hashes
        assert first.frames_hash == second.frames_hash

    def test_编码参数为单线程确定性档(self):
        params = board_render.ENCODE_FFMPEG_PARAMS
        assert "-threads" in params
        assert params[params.index("-threads") + 1] == "1"


class Test同一帧函数服务评估器:
    """C11：分镜卡帧口径即 C7 的评估输入（同一函数产出，禁止两套帧）。"""

    def test_元数据帧哈希与重算分镜卡一致(self, make_shotlist, script, render_cfg):
        shotlist = make_shotlist()
        _, meta = _render(shotlist, script, render_cfg)
        rederived = _cards(shotlist, script, render_cfg)
        assert meta["frame_hashes"] == list(rederived.frame_hashes)
        assert meta["frames_hash"] == rederived.frames_hash

    def test_编码帧与评估帧同源(self, make_shotlist, script, render_cfg):
        """mp4 内帧数 = 拼接帧数；评估侧的帧像素即渲染件来源（帧哈希逐镜对齐）。"""
        import blake3

        shotlist = make_shotlist()
        mp4_bytes, meta = _render(shotlist, script, render_cfg)
        decoded = iio.imread(io.BytesIO(mp4_bytes), extension=".mp4")
        assert len(decoded) == meta["duration_ms"] * _FPS // 1000
        cards = _cards(shotlist, script, render_cfg)
        for index, frame in enumerate(cards.frames):
            assert meta["frame_hashes"][index] == blake3.blake3(frame.tobytes()).hexdigest()


class Test临时音轨:
    def test_无音轨标记(self, make_shotlist, script, render_cfg):
        _, meta = _render(make_shotlist(), script, render_cfg)
        assert meta["has_temp_audio"] is False
        assert "temp_audio_mix_hash" not in meta

    def test_带临时音轨标记与混音哈希(self, make_shotlist, script, render_cfg):
        _, meta_a = _render(make_shotlist(), script, render_cfg, with_temp_audio=True)
        _, meta_b = _render(make_shotlist(), script, render_cfg, with_temp_audio=True)
        assert meta_a["has_temp_audio"] is True
        assert len(meta_a["temp_audio_mix_hash"]) == 64
        assert meta_a["temp_audio_mix_hash"] == meta_b["temp_audio_mix_hash"]


class Test素材校验:
    def test_帧网格为空拒绝(self, make_script_segment, script, render_cfg):
        """估算时长不足一帧 → 执行前拒绝（不静默丢弃镜头）。"""
        shots = [
            {
                "shot_id": "shot-01",
                "scene_id": "scene-1",
                "covers": ["s1-l1"],
                "shot_size": "medium",
                "camera": "eye_level",
                "side": "A",
                "movement": "static",
                "est_duration_ms": 100,  # 100ms @ 8fps = 0 帧
                "alternatives": 1,
            }
        ]
        with pytest.raises(ValidationError, match="shot-01"):
            _render(ShotList(shots=shots), script, render_cfg)

    def test_逐镜情绪与_shot_emotion_口径一致(self, make_shotlist, script, render_cfg):
        """cards 的逐镜情绪即 shot_emotion 口径（渲染件与评估输入同源同一函数）。"""
        shotlist = make_shotlist()
        cards = _cards(shotlist, script, render_cfg)
        assert cards.emotions == tuple(
            board_render.shot_emotion(shot, script) for shot in shotlist.shots
        )
