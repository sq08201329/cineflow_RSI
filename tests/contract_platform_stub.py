"""契约套件的"真实分支"可跑化基建：本地 stub 三个媒体平台（自算渲染侧）。

背景：`tests/contract/` 的三个适配器契约套件（视觉生成 / 分镜预演 / 剪辑渲染）
对**双实现**跑同一套断言；真实实现分支此前因无凭证一律按用例 skip。本模块把
"真实分支"在**无真实凭证、零外部网络**下变得可跑：起三个本地 stub 平台
（`127.0.0.1:0`），把三组凭证环境变量指向它们即可。

关键点：stub 的"渲染侧"**按规范化参数自行渲染**（不是返回固定假字节）——
分镜用 `board_render.render_animatic`、剪辑用 `SimulatedEditRenderer`、
视觉用 `encode_mp4(render_frames(...))`：契约套件传什么输入，就产出对应的真实
mp4 与元数据，因此 C10~C13 的断言（ffprobe 规格、元数据键、逐字节确定性）
在真实适配器上真被验证，而不是被 stub 的固定值糊过去。

**诚实边界**：这只证明"适配器的协议实现与既有契约一致"，**不证明**"某家真实
厂商的 API 已被对接"或"B 路径已验证"（真实凭证/真实计费/真实厂商 API 均未跑过，
见 docs/二期升级路径-真实生成与投放.md）。

启用方式（默认不启用，既有 skip 语义不变）：

```bash
CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q
```
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from tests.stubs_http import StubPlatformServer

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_STUB_ENV = "CINEFLOW_CONTRACT_STUB"
STUB_API_KEY = "contract-stub-key"

# 三组凭证 → stub 服务的映射（前缀即各自适配器的 from_env 前缀）
CREDENTIAL_PREFIXES = ("VISUAL_GEN", "STORYBOARD_RENDER", "EDIT_RENDER")

# 契约套件把渲染尺寸缩到 64x48 以控耗时（tests/contract/test_editing_platform_contract.py
# 的 _render_cfg 同口径）：stub 渲染侧按同一档配置，ffprobe 断言才对得上
CONTRACT_RENDER_WIDTH = 64
CONTRACT_RENDER_HEIGHT = 48


def _movie_config() -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _contract_editing_render_cfg() -> dict:
    cfg = dict(_movie_config()["editing"]["render"])
    cfg.update(width=CONTRACT_RENDER_WIDTH, height=CONTRACT_RENDER_HEIGHT)
    return cfg


def _visual_artifact_provider(distribution: dict, fps: int):
    """视觉生成侧：既有确定性生成器（程序化帧 + 单线程编码档）；fps 取 clip_spec（同模拟实现）。"""
    from agents.visual.platform.simulated import encode_mp4, render_frames

    def _provider(params: dict) -> tuple[bytes, dict]:
        return encode_mp4(render_frames(params, distribution), fps=fps), {"fps": fps}

    return _provider


def _visual_cost_model(distribution: dict):
    def _cost(params: dict) -> float:
        return float(distribution["estimated_cost_usd"])

    return _cost


def _storyboard_provider(params: dict) -> tuple[bytes, dict]:
    """分镜渲染侧：从规范化参数重建 ShotList/剧本，再走既有 render_animatic（同一帧来源）。"""
    from agents.storyboard.board_render import render_animatic
    from agents.storyboard.script import ScriptSegment
    from agents.storyboard.shotlist import ShotList

    return render_animatic(
        ShotList.from_dict(params["shotlist"]),
        ScriptSegment.from_dict(params["script"]),
        render_cfg=params["render"],
        grammar_rules=params["shot_grammar"],
        emotion_vectors=params["emotion_vectors"],
    )


def _storyboard_cost_model(params: dict) -> float:
    """分镜计价：镜头数 × 渲染价目（与模拟实现同口径，恒 estimated == actual）。"""
    return len(params["shotlist"]["shots"]) * float(params["render"]["price_per_shot_usd"])


def _editing_provider_factory(render_cfg: dict):
    from agents.editing.edl import EditDecisionList
    from agents.editing.platform.simulated import SimulatedEditRenderer
    from agents.editing.shots import ShotEntry, ShotLibrary

    def _provider(params: dict) -> tuple[bytes, dict]:
        library = ShotLibrary(
            shots=[
                ShotEntry(
                    shot_id=shot["shot_id"],
                    artifact_hash=shot["artifact_hash"],
                    duration_ms=shot["duration_ms"],
                    scene_id=shot["scene_id"],
                )
                for shot in params["shots"]
            ],
            audio_tracks=params["audio_tracks"],
        )
        film = SimulatedEditRenderer(render_cfg).render(
            EditDecisionList.from_dict(params["edl"]), library
        )
        return film.mp4_bytes, film.metadata

    return _provider


def _editing_cost_model(render_cfg: dict):
    def _cost(params: dict) -> float:
        nominal_ms = sum(clip["out_ms"] - clip["in_ms"] for clip in params["edl"]["clips"])
        return nominal_ms / 1000.0 * float(render_cfg["price_per_second_usd"])

    return _cost


def start_contract_stub() -> list[StubPlatformServer]:
    """起三个 stub 平台（视觉生成 / 分镜预演 / 剪辑渲染），返回服务端列表。

    调用方负责在结束时逐个 `stop()`；凭证环境变量的注入由调用方完成
    （`credential_env()` 给出映射）。
    """
    config = _movie_config()
    storyboard_render = dict(config["storyboard"]["render"])
    storyboard_render.update(width=CONTRACT_RENDER_WIDTH, height=CONTRACT_RENDER_HEIGHT)
    editing_render = _contract_editing_render_cfg()
    visual_dist = dict(config["visual"]["simulated_gen"])
    clip_spec = dict(config["visual"]["clip_spec"])

    servers = [
        StubPlatformServer(
            api_key=STUB_API_KEY,
            artifact_provider=_visual_artifact_provider(visual_dist, int(clip_spec["fps"])),
            estimated_cost_usd=_visual_cost_model(visual_dist),
        ).start(),
        StubPlatformServer(
            api_key=STUB_API_KEY,
            artifact_provider=_storyboard_provider,
            # 渲染平台：一次查询即终态（省去契约套件的轮询等待）
            status_sequence=("succeeded",),
            estimated_cost_usd=_storyboard_cost_model,
        ).start(),
        StubPlatformServer(
            api_key=STUB_API_KEY,
            artifact_provider=_editing_provider_factory(editing_render),
            status_sequence=("succeeded",),
            estimated_cost_usd=_editing_cost_model(editing_render),
        ).start(),
    ]
    return servers


def credential_env(servers: list[StubPlatformServer]) -> dict[str, str]:
    """三组凭证环境变量 → stub 服务（键序与 CREDENTIAL_PREFIXES 一致）。"""
    if len(servers) != len(CREDENTIAL_PREFIXES):
        raise ValueError(
            f"stub 服务数必须为 {len(CREDENTIAL_PREFIXES)}（{CREDENTIAL_PREFIXES}），"
            f"实际 {len(servers)}"
        )
    env: dict[str, str] = {}
    for prefix, server in zip(CREDENTIAL_PREFIXES, servers, strict=True):
        env[f"{prefix}_BASE_URL"] = server.base_url
        env[f"{prefix}_API_KEY"] = server.api_key
    return env


def stub_enabled(environ: dict[str, str] | None = None) -> bool:
    """契约套件真实分支是否启用（默认关：既有"无凭证即 skip"语义不变）。"""
    env = os.environ if environ is None else environ
    return env.get(CONTRACT_STUB_ENV, "") in {"1", "true", "yes"}
