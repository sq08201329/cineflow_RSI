"""真实适配器「协议实现」的本地 stub 端到端集成测试（B 路径协议面）。

形态：`tests/stubs_http.py` 起**真实监听** `127.0.0.1:0` 的 stub 平台（随机端口，
测试结束关闭），三个媒体环节的真实适配器对着它跑全链——**不需要真实凭证、
不发任何外部网络**。

覆盖（三环节各一套，逐条固化 `core/platform_http.py` 的协议）：

1. **协议全链**：视觉 `submit → poll → fetch_artifact → job_actual_cost → cancel`；
   分镜/剪辑 `estimate → render`（提交 → 轮询 → 取件 → 成本）；
2. **幂等**：幂等键原样透传；同键重复提交返回**平台给的**同一 external_id、
   服务端只建一个任务（适配器不自行编造 external_id）；
3. **成本纪律**：预估/实际均以响应为准（本地零估算）、`actual ≤ estimated`、
   `actual > estimated` **显式报错**（不静默接受）；
4. **错误映射**：429→限流、5xx→不可用、超时→不可用（注明超时值）、
   401/404→参数类、409→工件未就绪（视觉）、平台终态 failed→RenderError、
   响应不可解析 / 缺工件元数据头→可诊断的 unavailable（含截断片段）；
5. **契约镜像（C10~C13 关键断言）**：工件字节与 stub 返回**逐字节一致**、
   ffprobe 规格符合渲染配置、元数据键齐全且与 ShotList/EDL 一致、
   同输入两次渲染逐字节一致（并命中同一平台任务）。

本文件**不打 integration 标记**（不依赖 Docker/PG），CI 里单独一步执行
（见 .github/workflows/ci.yml 的 integration 作业）。
"""

import copy
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from agents.editing.platform.base import RateLimitedError as EditRateLimited
from agents.editing.platform.base import RenderError as EditRenderError
from agents.editing.platform.base import UnavailableError as EditUnavailable
from agents.editing.platform.http_real import HttpRealEditRender
from agents.editing.platform.http_real import render_params as edit_render_params
from agents.storyboard.board_render import render_animatic
from agents.storyboard.config import StoryboardConfig
from agents.storyboard.platform.base import RateLimitedError as BoardRateLimited
from agents.storyboard.platform.base import RenderError as BoardRenderError
from agents.storyboard.platform.base import UnavailableError as BoardUnavailable
from agents.storyboard.platform.http_real import HttpRealStoryboardRender
from agents.storyboard.platform.http_real import render_params as board_render_params
from agents.visual.frames import probe_clip
from agents.visual.platform.base import ArtifactNotReadyError, GenJobStatus, InvalidParamsError
from agents.visual.platform.base import RateLimitedError as VisualRateLimited
from agents.visual.platform.base import UnavailableError as VisualUnavailable
from agents.visual.platform.http_real import HttpRealVideoGen
from agents.visual.platform.simulated import encode_mp4, render_frames
from core.platform_http import idempotency_key, params_fingerprint
from tests.stubs_http import StubPlatformServer

REPO_ROOT = Path(__file__).resolve().parents[2]
_MOVIE_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

GEN_PARAMS = {"style": "史诗", "shots": 2, "seed_tier": 1}


@pytest.fixture()
def stub_factory():
    """stub 平台工厂：各自随机端口启动，测试结束统一关闭（无残留线程/端口）。"""
    servers: list[StubPlatformServer] = []

    def _make(**kwargs) -> StubPlatformServer:
        server = StubPlatformServer(**kwargs).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()


@pytest.fixture(scope="module")
def storyboard_config():
    """渲染配置：真实 storyboard 段缩小尺寸（64x48）以控制编码耗时。"""
    config = copy.deepcopy(_MOVIE_CONFIG)
    config["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(config)


def _probe(tmp_path, raw: bytes, *, fps, width, height) -> None:
    path = tmp_path / "artifact.mp4"
    path.write_bytes(raw)
    meta = probe_clip(path)
    assert meta["fps"] == pytest.approx(float(fps))
    assert meta["width"] == width
    assert meta["height"] == height


# ---------------------------------------------------------------------------
# 视觉生成（HttpRealVideoGen）：submit → poll → 取件 → 成本 → 取消
# ---------------------------------------------------------------------------


class Test视觉生成协议:
    def test_全链_提交到取消(self, stub_factory, visual_config, tmp_path):
        """工件/成本/状态以平台为准；幂等键与鉴权头原样透传。"""
        artifact = encode_mp4(render_frames(GEN_PARAMS, visual_config.simulated_gen), fps=8)
        server = stub_factory(
            artifact_provider=lambda params: (artifact, {"fps": 8}), estimated_cost_usd=0.4
        )
        adapter = HttpRealVideoGen(server.base_url, server.api_key)

        job = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:full")
        assert job.estimated_cost_usd == 0.4
        assert job.status is GenJobStatus.SUBMITTED

        record = server.records_of("/jobs")[0]
        assert record["headers"]["authorization"] == "Bearer stub-key"
        body = json.loads(record["body"].decode("utf-8"))
        assert body["idempotency_key"] == "stub:visual:full"
        assert body["params"] == GEN_PARAMS  # params 原样透传

        # 单次状态查询（不阻塞）：逐次推进至 completed（契约套件同款断言口径）
        assert adapter.get_status(job.external_id) in (
            GenJobStatus.SUBMITTED,
            GenJobStatus.GENERATING,
        )
        for _ in range(5):
            if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                break
        assert adapter.get_status(job.external_id) is GenJobStatus.COMPLETED

        fetched = adapter.fetch_artifact(job.external_id)
        assert fetched == artifact  # 工件字节与 stub 返回逐字节一致
        _probe(tmp_path, fetched, fps=8, width=320, height=240)
        assert adapter.job_actual_cost(job.external_id) == 0.4  # ≤ 预估 0.4

        adapter.cancel(job.external_id)  # 已完成任务取消为无操作（幂等）
        assert adapter.get_status(job.external_id) is GenJobStatus.COMPLETED

    def test_取消未完成任务_进入终态且二次取消幂等(self, stub_factory):
        server = stub_factory()
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        job = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:cancel")
        adapter.cancel(job.external_id)
        assert adapter.get_status(job.external_id) is GenJobStatus.FAILED
        adapter.cancel(job.external_id)  # 二次取消无副作用（平台侧幂等）
        assert adapter.get_status(job.external_id) is GenJobStatus.FAILED

    def test_同幂等键重复提交_平台只建一个任务(self, stub_factory):
        server = stub_factory(estimated_cost_usd=0.5)
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        first = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:idem")
        second = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:idem")
        other = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:other")

        assert second.external_id == first.external_id
        assert second.job_id == first.job_id
        assert other.external_id != first.external_id
        assert server.job_count == 2  # 同键未新建任务（去重语义在平台侧）

    def test_参数指纹_平台回传为准_缺省回落规范化指纹(self, stub_factory):
        server = stub_factory()
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        job = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:hash")
        assert job.params_hash == server.job(job.external_id)["params_hash"]

        plain = stub_factory(params_hash_mode="absent")
        fallback = HttpRealVideoGen(plain.base_url, plain.api_key).submit(
            GEN_PARAMS, idempotency_key="stub:visual:hash2"
        )
        assert fallback.params_hash == params_fingerprint(GEN_PARAMS)

    def test_未完成取件_映射工件未就绪(self, stub_factory):
        server = stub_factory(status_sequence=("running", "succeeded"))
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        job = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:early")
        with pytest.raises(ArtifactNotReadyError, match="not ready"):
            adapter.fetch_artifact(job.external_id)

    def test_429_映射限流(self, stub_factory):
        server = stub_factory()
        server.enqueue_status(429)
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        with pytest.raises(VisualRateLimited, match="限流"):
            adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:429")

    def test_5xx_映射不可用(self, stub_factory):
        server = stub_factory()
        server.enqueue_status(503)
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        with pytest.raises(VisualUnavailable, match="HTTP 503"):
            adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:5xx")

    def test_超时_映射不可用并注明超时值(self, stub_factory):
        server = stub_factory()
        server.enqueue_latency(1.5)
        adapter = HttpRealVideoGen(server.base_url, server.api_key, request_timeout_s=0.2)
        with pytest.raises(VisualUnavailable, match="超时 0.2s"):
            adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:timeout")

    def test_响应不可解析_给出可诊断错误(self, stub_factory):
        server = stub_factory()
        server.enqueue_raw(b"<html>gateway error</html>")
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        with pytest.raises(VisualUnavailable) as excinfo:
            adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:garbage")
        assert "响应不可解析" in str(excinfo.value)
        assert "gateway error" in str(excinfo.value)  # 含响应片段（可诊断）

    def test_未知任务与鉴权失败_归参数类错误(self, stub_factory):
        server = stub_factory()
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        with pytest.raises(InvalidParamsError, match="HTTP 404"):
            adapter.get_status("ghost")
        with pytest.raises(InvalidParamsError, match="HTTP 404"):
            adapter.fetch_artifact("ghost")
        with pytest.raises(InvalidParamsError, match="HTTP 404"):
            adapter.cancel("ghost")
        with pytest.raises(InvalidParamsError, match="HTTP 401"):
            HttpRealVideoGen(server.base_url, "wrong-key").submit(
                GEN_PARAMS, idempotency_key="stub:visual:401"
            )

    def test_actual_超过预估_显式报错(self, stub_factory):
        server = stub_factory(estimated_cost_usd=0.4, overcharge_usd=0.25)
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        job = adapter.submit(GEN_PARAMS, idempotency_key="stub:visual:overcharge")
        for _ in range(5):
            if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                break
        adapter.fetch_artifact(job.external_id)
        with pytest.raises(VisualUnavailable, match="超过预估"):
            adapter.job_actual_cost(job.external_id)

    def test_同参数跨任务_工件逐字节一致(self, stub_factory, visual_config):
        """契约镜像：同 gen_params 不同幂等键 → 工件逐字节相同（决策 2 口径）。"""
        artifact = encode_mp4(render_frames(GEN_PARAMS, visual_config.simulated_gen), fps=8)
        server = stub_factory(artifact_provider=lambda params: (artifact, {}))
        adapter = HttpRealVideoGen(server.base_url, server.api_key)
        jobs = [
            adapter.submit(GEN_PARAMS, idempotency_key=f"stub:visual:det-{index}")
            for index in range(2)
        ]
        for job in jobs:
            for _ in range(5):
                if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                    break
        assert (
            adapter.fetch_artifact(jobs[0].external_id)
            == adapter.fetch_artifact(jobs[1].external_id)
            == artifact
        )


# ---------------------------------------------------------------------------
# 分镜预演渲染（HttpRealStoryboardRender）：estimate → render
# ---------------------------------------------------------------------------


class Test分镜渲染协议:
    @pytest.fixture()
    def inputs(self, make_shotlist, make_script_segment):
        return make_shotlist(), make_script_segment()

    @pytest.fixture()
    def adapter_factory(self, stub_factory, inputs, storyboard_config):
        """stub 平台的"渲染侧"：用既有 render_animatic 产出真实可探测 mp4 + 契约元数据。"""
        shotlist, script = inputs

        def _default_provider(params):
            return render_animatic(
                shotlist,
                script,
                render_cfg=storyboard_config.render,
                grammar_rules=storyboard_config.shot_grammar,
                emotion_vectors=storyboard_config.emotion_vectors,
            )

        def _make(
            *,
            artifact_provider=_default_provider,
            server_kwargs=None,
            adapter_kwargs=None,
        ):
            server = stub_factory(
                artifact_provider=artifact_provider,
                estimated_cost_usd=len(shotlist.shots)
                * float(storyboard_config.render["price_per_shot_usd"]),
                **(server_kwargs or {}),
            )
            adapter = HttpRealStoryboardRender(
                server.base_url,
                server.api_key,
                **{"poll_interval_s": 0.01, "poll_deadline_s": 10.0, **(adapter_kwargs or {})},
            )
            return adapter, server

        return _make

    def test_全链_估价到渲染(self, adapter_factory, inputs, storyboard_config, tmp_path):
        shotlist, script = inputs
        adapter, server = adapter_factory()

        estimated = adapter.estimate(shotlist, storyboard_config)
        assert estimated > 0  # 估价来自平台（本地零估算）
        animatic = adapter.render(shotlist, script, storyboard_config)

        # 工件：stub 返回的原样字节 + ffprobe 规格符合渲染配置（契约镜像 C13）
        _probe(
            tmp_path,
            animatic.mp4_bytes,
            fps=storyboard_config.render["fps"],
            width=storyboard_config.render["width"],
            height=storyboard_config.render["height"],
        )
        assert animatic.mp4_bytes == server.artifact_bytes(server.job_ids()[0])

        # 元数据：平台返回的原样透传 + 与 ShotList 一致（契约镜像 C10/C11）
        metadata = animatic.metadata
        assert metadata["shot_count"] == len(shotlist.shots)
        assert metadata["shot_sizes"] == [shot.shot_size for shot in shotlist.shots]
        assert metadata["shot_durations_ms"] == [shot.est_duration_ms for shot in shotlist.shots]
        assert len(metadata["frame_hashes"]) == len(shotlist.shots)

        # 成本：estimated ≥ actual > 0；幂等键由规范化参数派生后原样透传、规格随调用注入
        assert 0 < animatic.actual_cost_usd <= estimated + 1e-9
        body = json.loads(server.records_of("/jobs")[0]["body"].decode("utf-8"))
        assert body["idempotency_key"] == idempotency_key(
            "storyboard-render", board_render_params(shotlist, storyboard_config, script=script)
        )
        assert body["params"]["render"] == dict(storyboard_config.render)

    def test_同输入两次渲染_同一平台任务且逐字节一致(
        self, adapter_factory, inputs, storyboard_config
    ):
        adapter, server = adapter_factory()
        first = adapter.render(*inputs, storyboard_config)
        second = adapter.render(*inputs, storyboard_config)

        assert server.job_count == 1  # 同输入 → 同幂等键 → 平台侧同一任务（0 重复扣费）
        assert first.mp4_bytes == second.mp4_bytes
        assert first.metadata == second.metadata

    def test_平台终态失败_映射_RenderError带详情(self, adapter_factory, inputs, storyboard_config):
        adapter, _ = adapter_factory(
            server_kwargs={"status_sequence": ("running", "failed"), "error_message": "GPU 掉线"}
        )
        with pytest.raises(BoardRenderError, match="GPU 掉线"):
            adapter.render(*inputs, storyboard_config)

    def test_轮询超时_映射不可用并注明(self, adapter_factory, inputs, storyboard_config):
        adapter, _ = adapter_factory(
            server_kwargs={"status_sequence": ("pending", "running")},
            adapter_kwargs={"poll_interval_s": 0.05, "poll_deadline_s": 0.3},
        )
        with pytest.raises(BoardUnavailable, match="等待平台渲染完成超时"):
            adapter.render(*inputs, storyboard_config)

    def test_429_500_超时_错误映射(self, adapter_factory, inputs, storyboard_config):
        adapter, server = adapter_factory()
        shotlist, script = inputs
        server.enqueue_status(429)
        with pytest.raises(BoardRateLimited, match="限流"):
            adapter.render(shotlist, script, storyboard_config)
        server.enqueue_status(500)
        with pytest.raises(BoardUnavailable, match="HTTP 500"):
            adapter.estimate(shotlist, storyboard_config)
        server.enqueue_latency(1.5)
        slow = HttpRealStoryboardRender(
            server.base_url, server.api_key, request_timeout_s=0.2, poll_interval_s=0.01
        )
        with pytest.raises(BoardUnavailable, match="超时 0.2s"):
            slow.estimate(shotlist, storyboard_config)

    def test_元数据缺必含键_拒绝(self, adapter_factory, inputs, storyboard_config):
        adapter, _ = adapter_factory(artifact_provider=lambda params: (b"mp4", {}))
        with pytest.raises(BoardUnavailable, match="缺必含键"):
            adapter.render(*inputs, storyboard_config)

    def test_缺工件元数据响应头_拒绝(self, adapter_factory, inputs, storyboard_config):
        adapter, _ = adapter_factory(server_kwargs={"omit_artifact_meta": True})
        with pytest.raises(BoardUnavailable, match="X-Artifact-Meta"):
            adapter.render(*inputs, storyboard_config)

    def test_actual_超过预估_显式报错(self, adapter_factory, inputs, storyboard_config):
        adapter, _ = adapter_factory(server_kwargs={"overcharge_usd": 0.25})
        with pytest.raises(BoardRenderError, match="超过预估"):
            adapter.render(*inputs, storyboard_config)


# ---------------------------------------------------------------------------
# 剪辑渲染（HttpRealEditRender）：estimate → render
# ---------------------------------------------------------------------------


class Test剪辑渲染协议:
    @pytest.fixture()
    def render_cfg(self, editing_config):
        """渲染配置：真实 editing.render 段缩小尺寸（64x48）以控制编码耗时。"""
        cfg = dict(editing_config.render)
        cfg.update(width=64, height=48)
        return cfg

    @pytest.fixture()
    def library(self, make_shot_library):
        return make_shot_library()

    @pytest.fixture()
    def edls(self, make_edl):
        """两版 EDL：带音轨（默认）与无音轨（如实标注路径）。"""
        return {"audio": make_edl(), "silent": make_edl(audio=[])}

    @pytest.fixture()
    def adapter_factory(self, stub_factory, render_cfg, library, edls):
        """stub 平台的"渲染侧"：用既有模拟渲染器产出（真实 mp4 + 契约元数据）。"""
        from agents.editing.platform.simulated import SimulatedEditRenderer

        artifacts = {}
        for key, edl in edls.items():
            film = SimulatedEditRenderer(render_cfg).render(edl, library)
            artifacts[key] = (film.mp4_bytes, film.metadata, film.actual_cost_usd)

        def _default_provider(params):
            key = "audio" if params["edl"]["audio"] else "silent"
            artifact, meta, _ = artifacts[key]
            return artifact, meta

        def _make(*, artifact_provider=_default_provider, server_kwargs=None, adapter_kwargs=None):
            server = stub_factory(
                artifact_provider=artifact_provider,
                # 平台估价 ≥ 任一实际扣费（estimated ≥ actual 纪律的平台侧口径）
                estimated_cost_usd=max(entry[2] for entry in artifacts.values()),
                **(server_kwargs or {}),
            )
            adapter = HttpRealEditRender(
                server.base_url,
                server.api_key,
                **{"poll_interval_s": 0.01, "poll_deadline_s": 10.0, **(adapter_kwargs or {})},
            )
            return adapter, server

        return _make

    def test_全链_估价到渲染(self, adapter_factory, render_cfg, library, edls, tmp_path):
        edl = edls["audio"]
        adapter, server = adapter_factory()

        estimated = adapter.estimate(edl, library)
        assert estimated > 0  # 估价来自平台（本地零估算）
        film = adapter.render(edl, library)

        # 工件：stub 返回的原样字节 + ffprobe 规格符合渲染配置（契约镜像 C13）
        _probe(tmp_path, film.mp4_bytes, fps=render_cfg["fps"], width=64, height=48)
        assert film.mp4_bytes == server.artifact_bytes(server.job_ids()[0])

        # 元数据四键与 EDL 一致（契约镜像 C10）+ 成本 estimated ≥ actual > 0
        assert film.metadata["shot_durations_ms"] == [c.out_ms - c.in_ms for c in edl.clips]
        assert film.metadata["transitions"] == [c.transition.to_dict() for c in edl.clips]
        assert film.metadata["duration_ms"] == edl.total_duration_ms()
        assert film.metadata["has_audio"] is True
        assert 0 < film.actual_cost_usd <= estimated + 1e-9

        # 幂等键由规范化参数派生（含 EDL 与素材引用哈希）
        body = json.loads(server.records_of("/jobs")[0]["body"].decode("utf-8"))
        assert body["idempotency_key"] == idempotency_key(
            "edit-render", edit_render_params(edl, library)
        )
        assert body["params"]["shots"][0]["artifact_hash"] == library.shots[0].artifact_hash

    def test_同输入两次渲染_同一平台任务且逐字节一致(self, adapter_factory, library, edls):
        adapter, server = adapter_factory()
        first = adapter.render(edls["audio"], library)
        second = adapter.render(edls["audio"], library)
        assert server.job_count == 1  # 同输入 → 同幂等键 → 平台侧同一任务（0 重复扣费）
        assert first.mp4_bytes == second.mp4_bytes
        assert first.metadata == second.metadata

    def test_无音轨_如实标注且不同输入不同任务(self, adapter_factory, library, edls):
        adapter, server = adapter_factory()
        assert adapter.render(edls["audio"], library).metadata["has_audio"] is True
        assert adapter.render(edls["silent"], library).metadata["has_audio"] is False
        assert server.job_count == 2  # 不同 EDL → 不同幂等键 → 两个任务

    def test_平台终态失败_映射_RenderError带详情(self, adapter_factory, library, edls):
        adapter, _ = adapter_factory(
            server_kwargs={"status_sequence": ("running", "failed"), "error_message": "转码过载"}
        )
        with pytest.raises(EditRenderError, match="转码过载"):
            adapter.render(edls["audio"], library)

    def test_轮询超时_映射不可用并注明(self, adapter_factory, library, edls):
        adapter, _ = adapter_factory(
            server_kwargs={"status_sequence": ("pending", "running")},
            adapter_kwargs={"poll_interval_s": 0.05, "poll_deadline_s": 0.3},
        )
        with pytest.raises(EditUnavailable, match="等待平台渲染完成超时"):
            adapter.render(edls["audio"], library)

    def test_429_500_超时_错误映射(self, adapter_factory, library, edls):
        adapter, server = adapter_factory()
        edl = edls["audio"]
        server.enqueue_status(429)
        with pytest.raises(EditRateLimited, match="限流"):
            adapter.estimate(edl, library)
        server.enqueue_status(502)
        with pytest.raises(EditUnavailable, match="HTTP 502"):
            adapter.estimate(edl, library)
        server.enqueue_latency(1.5)
        slow = HttpRealEditRender(server.base_url, server.api_key, request_timeout_s=0.2)
        with pytest.raises(EditUnavailable, match="超时 0.2s"):
            slow.estimate(edl, library)

    def test_元数据缺必含键_拒绝(self, adapter_factory, library, edls):
        adapter, _ = adapter_factory(artifact_provider=lambda params: (b"mp4", {}))
        with pytest.raises(EditUnavailable, match="缺必含键"):
            adapter.render(edls["audio"], library)

    def test_actual_超过预估_显式报错(self, adapter_factory, library, edls):
        adapter, _ = adapter_factory(server_kwargs={"overcharge_usd": 0.3})
        with pytest.raises(EditRenderError, match="超过预估"):
            adapter.render(edls["audio"], library)


# ---------------------------------------------------------------------------
# 凭证与探测形态（协议实现的诚实边界）
# ---------------------------------------------------------------------------


class Test凭证与探测:
    def test_无凭证构造即不可用(self, monkeypatch):
        """三环节一致：无凭证不假装可用（宪章原则六），报错指明缺哪个环境变量。"""
        for prefix in ("VISUAL_GEN", "STORYBOARD_RENDER", "EDIT_RENDER"):
            monkeypatch.delenv(f"{prefix}_BASE_URL", raising=False)
            monkeypatch.delenv(f"{prefix}_API_KEY", raising=False)
        with pytest.raises(VisualUnavailable, match="VISUAL_GEN"):
            HttpRealVideoGen.from_env()
        with pytest.raises(BoardUnavailable, match="STORYBOARD_RENDER"):
            HttpRealStoryboardRender.from_env()
        with pytest.raises(EditUnavailable, match="EDIT_RENDER"):
            HttpRealEditRender.from_env()

    def test_健康端点只读可探测(self, stub_factory):
        """`/health` 是本协议登记的只读探测端点（ops/check_credentials.py 的 PROBE_PATHS）。"""
        server = stub_factory()
        request = urllib.request.Request(
            f"{server.base_url}/health",
            headers={"Authorization": f"Bearer {server.api_key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert json.loads(response.read())["status"] == "ok"

    def test_stub_平台拒绝未鉴权与未登记路径(self, stub_factory):
        """反向机检：stub 只服务协议登记的端点，且一律要求 Bearer 鉴权。"""
        server = stub_factory()
        with pytest.raises(urllib.error.HTTPError) as unauthenticated:
            urllib.request.urlopen(f"{server.base_url}/jobs", timeout=5)
        assert unauthenticated.value.code == 401

        request = urllib.request.Request(
            f"{server.base_url}/unknown", headers={"Authorization": f"Bearer {server.api_key}"}
        )
        with pytest.raises(urllib.error.HTTPError) as unknown:
            urllib.request.urlopen(request, timeout=5)
        assert unknown.value.code == 404
