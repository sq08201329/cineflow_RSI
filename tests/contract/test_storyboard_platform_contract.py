"""分镜预演渲染适配器契约套件（功能 008 / T815，先于实现编写）。

C10~C13 同构执行：双实现（模拟/真实骨架）——estimate ≥ actual、mp4 可探测
（ffprobe 口径：时长/帧率/尺寸符合渲染配置）、RenderedAnimatic 元数据键齐全
（景别序列/逐镜帧哈希/临时音轨标记）、分镜卡帧数与镜头数一致、同 ShotList
逐字节一致（SC-002）、错误分型正确（RenderError 族）。模拟实现必须全过；
真实实现无凭证按用例 skip（不报错不假装，003/004/006/007 同款）。

补充（C11 单一帧来源）：模拟渲染器元数据的逐镜帧哈希必须等于 board_render
同一函数（storyboard_cards）的重算结果——渲染件与评估输入同源，禁止两套帧。
"""

import copy
import os
from pathlib import Path

import pytest
import yaml

from agents.storyboard.board_render import storyboard_cards
from agents.storyboard.config import StoryboardConfig
from agents.storyboard.platform.base import (
    RateLimitedError,
    RenderError,
    UnavailableError,
)
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.visual.frames import probe_clip

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_METADATA_KEYS = {
    "duration_ms",
    "shot_count",
    "shot_sizes",
    "shot_durations_ms",
    "shot_frame_counts",
    "frame_hashes",
    "frames_hash",
    "emotions",
    "has_temp_audio",
    "fps",
}


def _config() -> StoryboardConfig:
    """渲染配置：真实 storyboard 段缩小尺寸（64x48）以控制契约套件耗时。"""
    config = copy.deepcopy(_REAL_CONFIG)
    config["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(config)


def _make_real_adapter():
    from agents.storyboard.platform.http_real import HttpRealStoryboardRender

    if not os.environ.get("STORYBOARD_RENDER_BASE_URL"):
        pytest.skip("真实预演渲染服务无凭证（STORYBOARD_RENDER_BASE_URL 未配置），跳过其契约用例")
    return HttpRealStoryboardRender.from_env()


@pytest.fixture(params=["simulated", "http_real"])
def adapter(request):
    if request.param == "simulated":
        return SimulatedStoryboardRenderer()
    return _make_real_adapter()


@pytest.fixture()
def config():
    return _config()


@pytest.fixture()
def script(make_script_segment):
    return make_script_segment()


@pytest.fixture()
def shotlist(make_shotlist):
    return make_shotlist()


class Test花费契约:
    def test_预估花费为正(self, adapter, shotlist, config):
        assert adapter.estimate(shotlist, config) > 0

    def test_实际扣费不超预估(self, adapter, shotlist, script, config):
        """estimate ≥ actual 纪律（C11；按镜头数计价，实际不超过预估）。"""
        estimated = adapter.estimate(shotlist, config)
        animatic = adapter.render(shotlist, script, config)
        assert 0 < animatic.actual_cost_usd <= estimated + 1e-9


class Test工件与元数据:
    def test_mp4_可探测且规格符合配置(self, adapter, shotlist, script, config, tmp_path):
        """C13：ffprobe 口径——帧率/尺寸符合渲染配置，时长与元数据一致。"""
        animatic = adapter.render(shotlist, script, config)
        path = tmp_path / "animatic.mp4"
        path.write_bytes(animatic.mp4_bytes)
        meta = probe_clip(path)
        assert meta["fps"] == pytest.approx(float(config.render["fps"]))
        assert meta["width"] == config.render["width"]
        assert meta["height"] == config.render["height"]
        assert meta["duration_seconds"] == pytest.approx(
            animatic.metadata["duration_ms"] / 1000.0,
            abs=0.26,  # 单帧时长容差
        )

    def test_元数据键齐全且与_ShotList_一致(self, adapter, shotlist, script, config):
        """C10/C11：时长/镜头数/景别序列/逐镜帧哈希/临时音轨标记齐全。"""
        animatic = adapter.render(shotlist, script, config)
        metadata = animatic.metadata
        assert _METADATA_KEYS <= set(metadata)
        assert metadata["shot_count"] == len(shotlist.shots)
        assert metadata["shot_sizes"] == [shot.shot_size for shot in shotlist.shots]
        assert metadata["shot_durations_ms"] == [shot.est_duration_ms for shot in shotlist.shots]
        assert metadata["duration_ms"] > 0
        assert metadata["fps"] == config.render["fps"]

    def test_逐镜帧哈希与镜头数一致(self, adapter, shotlist, script, config):
        """C13 场景 3：分镜卡帧数与镜头数一致；帧哈希形态 = 64 位小写 hex。"""
        metadata = adapter.render(shotlist, script, config).metadata
        assert len(metadata["frame_hashes"]) == len(shotlist.shots)
        assert all(len(h) == 64 and h == h.lower() for h in metadata["frame_hashes"])
        assert len(metadata["frames_hash"]) == 64
        assert sum(metadata["shot_frame_counts"]) == (
            metadata["duration_ms"] * config.render["fps"] // 1000
        )

    def test_同_ShotList_两次渲染逐字节一致(self, adapter, shotlist, script, config):
        """SC-002：编码单线程确定性档——同 ShotList 两次 render 字节完全一致。"""
        first = adapter.render(shotlist, script, config)
        second = adapter.render(shotlist, script, config)
        assert first.mp4_bytes == second.mp4_bytes
        assert first.metadata == second.metadata

    def test_带临时音轨标记与无音轨标记(self, config, shotlist, script):
        """C13 场景 2：带临时音轨 → has_temp_audio=true；默认无音轨 → false。"""
        with_audio = SimulatedStoryboardRenderer(with_temp_audio=True).render(
            shotlist, script, config
        )
        without_audio = SimulatedStoryboardRenderer().render(shotlist, script, config)
        assert with_audio.metadata["has_temp_audio"] is True
        assert len(with_audio.metadata["temp_audio_mix_hash"]) == 64
        assert without_audio.metadata["has_temp_audio"] is False
        assert "temp_audio_mix_hash" not in without_audio.metadata


class Test模拟渲染器帧来源:
    """C11：分镜卡帧口径即 C7 的评估输入（同一函数产出，禁止两套帧）。"""

    def test_元数据帧哈希等于同一函数重算(self, shotlist, script, config):
        animatic = SimulatedStoryboardRenderer().render(shotlist, script, config)
        rederived = storyboard_cards(
            shotlist,
            script,
            render_cfg=config.render,
            grammar_rules=config.shot_grammar,
            emotion_vectors=config.emotion_vectors,
        )
        assert animatic.metadata["frame_hashes"] == list(rederived.frame_hashes)
        assert animatic.metadata["frames_hash"] == rederived.frames_hash
        assert animatic.metadata["emotions"] == list(rederived.emotions)


class Test错误分型:
    """错误类型层级（C10）：全部归一 RenderError 族，不泄漏实现侧异常。"""

    def test_错误层级(self):
        assert issubclass(RateLimitedError, RenderError)
        assert issubclass(UnavailableError, RenderError)

    def test_注入失败归_RenderError(self, config, shotlist, script):
        """模拟渲染器失败注入（执行器失败路径测试用）归一 RenderError。"""
        adapter = SimulatedStoryboardRenderer(fail_on_shots=("shot-05",))
        with pytest.raises(RenderError, match="shot-05"):
            adapter.render(shotlist, script, config)

    def test_真实适配器无凭证构造即报未配置(self, monkeypatch):
        """C12/C13 场景 4：无凭证 → UnavailableError（不假装接入）。"""
        from agents.storyboard.platform.http_real import HttpRealStoryboardRender

        monkeypatch.delenv("STORYBOARD_RENDER_BASE_URL", raising=False)
        monkeypatch.delenv("STORYBOARD_RENDER_API_KEY", raising=False)
        with pytest.raises(UnavailableError, match="STORYBOARD_RENDER"):
            HttpRealStoryboardRender.from_env()
