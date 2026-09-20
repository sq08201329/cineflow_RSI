"""剪辑渲染适配器契约套件（功能 007 / T715，先于实现编写）。

C10~C13 同构执行：双实现（模拟/真实骨架）——estimate ≥ actual、mp4 可探测
（ffprobe 口径：时长/帧率/尺寸符合渲染配置）、RenderedFilm 元数据键齐全
（总时长/镜头时长序列/转场序列/音轨标记）、同 EDL 逐字节一致（SC-002）、
错误分型正确（RenderError 族）。模拟实现必须全过；真实实现无凭证按用例
skip（不报错不假装，003/004/006 同款）。
"""

import os

import pytest

from agents.editing.platform.base import (
    RateLimitedError,
    RenderError,
    UnavailableError,
)
from agents.editing.platform.simulated import SimulatedEditRenderer
from agents.visual.frames import probe_clip

_METADATA_KEYS = {"duration_ms", "shot_durations_ms", "transitions", "has_audio"}


def _render_cfg(editing_config) -> dict:
    """渲染配置：真实 editing.render 段缩小尺寸（64x48）以控制契约套件耗时。"""
    cfg = dict(editing_config.render)
    cfg.update(width=64, height=48)
    return cfg


def _make_real_adapter():
    from agents.editing.platform.http_real import HttpRealEditRender

    if not os.environ.get("EDIT_RENDER_BASE_URL"):
        pytest.skip("真实渲染服务无凭证（EDIT_RENDER_BASE_URL 未配置），跳过其契约用例")
    return HttpRealEditRender.from_env()


@pytest.fixture(params=["simulated", "http_real"])
def adapter(request, editing_config):
    if request.param == "simulated":
        return SimulatedEditRenderer(_render_cfg(editing_config))
    return _make_real_adapter()


@pytest.fixture()
def library(make_shot_library):
    return make_shot_library()


@pytest.fixture()
def edl(make_edl):
    return make_edl()


@pytest.mark.usefixtures("adapter")
class Test花费契约:
    def test_预估花费为正(self, adapter, edl, library):
        assert adapter.estimate(edl, library) > 0

    def test_实际扣费不超预估(self, adapter, edl, library):
        """estimate ≥ actual 纪律（C11；预估按 EDL 名义时长，叠化重叠只减不增）。"""
        estimated = adapter.estimate(edl, library)
        film = adapter.render(edl, library)
        assert 0 < film.actual_cost_usd <= estimated + 1e-9


class Test工件与元数据:
    def test_mp4_可探测且规格符合配置(self, adapter, edl, library, editing_config, tmp_path):
        """C13：ffprobe 口径——帧率/尺寸符合渲染配置，时长与元数据一致。"""
        cfg = _render_cfg(editing_config)
        film = adapter.render(edl, library)
        path = tmp_path / "film.mp4"
        path.write_bytes(film.mp4_bytes)
        meta = probe_clip(path)
        assert meta["fps"] == pytest.approx(float(cfg["fps"]))
        assert meta["width"] == cfg["width"]
        assert meta["height"] == cfg["height"]
        assert meta["duration_seconds"] == pytest.approx(
            film.metadata["duration_ms"] / 1000.0,
            abs=0.26,  # 单帧时长容差
        )

    def test_元数据键齐全且与_EDL_一致(self, adapter, edl, library):
        """C10：总时长/镜头时长序列/转场序列/音轨标记四键齐全。"""
        film = adapter.render(edl, library)
        assert _METADATA_KEYS <= set(film.metadata)
        assert film.metadata["shot_durations_ms"] == [c.out_ms - c.in_ms for c in edl.clips]
        assert film.metadata["transitions"] == [c.transition.to_dict() for c in edl.clips]
        assert film.metadata["duration_ms"] == edl.total_duration_ms()

    def test_同_EDL_两次渲染逐字节一致(self, adapter, edl, library):
        """SC-002：编码单线程确定性档——同 EDL 两次 render 字节完全一致。"""
        first = adapter.render(edl, library)
        second = adapter.render(edl, library)
        assert first.mp4_bytes == second.mp4_bytes
        assert first.metadata == second.metadata

    def test_带音轨标记与无音轨标记(self, adapter, make_edl, library):
        """C13 场景 3：带音轨 EDL → has_audio=true；无音轨 → false。"""
        assert adapter.render(make_edl(), library).metadata["has_audio"] is True
        assert adapter.render(make_edl(audio=[]), library).metadata["has_audio"] is False

    def test_叠化转场序列记录类型与时长(self, adapter, edl, library):
        """C13 场景 2：叠化转场在元数据转场序列中记录类型与时长。"""
        transitions = adapter.render(edl, library).metadata["transitions"]
        dissolves = [t for t in transitions if t["type"] == "dissolve"]
        assert dissolves and all(t["duration_ms"] > 0 for t in dissolves)


class Test错误分型:
    """错误类型层级（C10）：全部归一 RenderError 族，不泄漏实现侧异常。"""

    def test_错误层级(self):
        assert issubclass(RateLimitedError, RenderError)
        assert issubclass(UnavailableError, RenderError)

    def test_真实适配器无凭证构造即报未配置(self, monkeypatch):
        """C12/C13 场景 4：无凭证 → UnavailableError（不假装接入）。"""
        from agents.editing.platform.http_real import HttpRealEditRender

        monkeypatch.delenv("EDIT_RENDER_BASE_URL", raising=False)
        monkeypatch.delenv("EDIT_RENDER_API_KEY", raising=False)
        with pytest.raises(UnavailableError, match="EDIT_RENDER"):
            HttpRealEditRender.from_env()
