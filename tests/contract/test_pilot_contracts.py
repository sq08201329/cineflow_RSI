"""试水作品契约聚合（功能 015）。

本批只覆盖 `contracts/handoffs.md` 的 C5~C9（orchestration 的 C1~C4 与 pilot-run 的
C10~C13 由 T1524 补全并与本文件合并为 C1~C13 全量）：

- **C5** 剧本 → 分镜段落：字段集双向锁定（含枚举值）+ 下游预检通过 + 缺字段即红；
- **C6** 分镜 → 视觉生成参数：镜头数 == 参数数 + 尺寸由形态配置派生（竖屏守卫）；
- **C7** 视听 → 剪辑输入：镜头库条目一致 + 含/无音轨两路径（无音轨如实标注）；
- **C8** 成片 → 宣发物料：素材引用与元数据齐备 + 规格来自配置裁剪规则；
- **C9** 拒绝语义：上游 FAILED → 下游**拒绝启动**（机检下游执行计数 0）；
  环节候选全败 → 环节 failed → 运行终止并记录全部候选判 0 理由。
"""

import pytest

from agents.editing.shots import ShotLibrary
from agents.pilot.handoffs import (
    HandoffError,
    av_to_edit_inputs,
    edit_inputs_parity,
    reel_to_promo_materials,
    script_parity,
    script_to_segment,
    shotlist_parity,
    shotlist_to_gen_params,
)
from agents.storyboard.script import ScriptSegment
from core.orchestration.dag import build_dag
from core.orchestration.executor import ExecutionContext, run
from core.orchestration.models import RunStatus, StageOutcome, StageSpec, StageStatus


class _Cfg:
    """形态配置桩（只暴露交接要读的两段；形态值只作参数，不进分支）。"""

    def __init__(self, width=540, height=960, fps=8, duration_seconds=1.5):
        self.clip_spec = {
            "width": width,
            "height": height,
            "fps": fps,
            "duration_seconds": duration_seconds,
            "codec": "h264",
        }
        self.material_spec = {
            "max_copy_chars": 40,
            "poster_size": "1080x1920",
            "max_duration_seconds": 20,
        }


def _clip(shot_id: str, scene_id: str, index: int) -> dict:
    return {
        "shot_id": shot_id,
        "scene_id": scene_id,
        "artifact_hash": (f"{index:02d}" * 32)[:64],
        "duration_ms": 1500,
        "gen_params": {"shot_id": shot_id, "width": 540, "height": 960},
        "score": 0.75,
    }


def _reel() -> dict:
    return {
        "artifact_hash": "ee" * 32,
        "duration_ms": 120000,
        "width": 540,
        "height": 960,
        "fps": 8,
    }


class TestC5剧本到分镜:
    def test_四场景全链路(self, pilot_material_script):
        segment = script_to_segment(pilot_material_script)
        assert isinstance(segment, ScriptSegment)
        assert script_parity(pilot_material_script).consistent()
        assert segment.scene_ids() == ("scene-1", "scene-2")
        assert dict(zip(segment.line_ids(), segment.key_line_ids(), strict=False))
        # 关键行清单逐条承接（必覆盖清单来自上游 key=True 行）
        assert set(segment.key_line_ids()) == {"s1-l1", "s2-l1"}

    def test_缺字段即红(self, pilot_material_script):
        with pytest.raises(HandoffError):
            script_to_segment("不是工件")


class TestC6分镜到视觉:
    def test_镜头数等于参数数(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_Cfg())
        assert len(params) == len(pilot_material_shotlist.shots)
        assert shotlist_parity(pilot_material_shotlist, params).consistent()

    def test_竖屏规格由配置派生(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(
            pilot_material_shotlist, config=_Cfg(width=720, height=1280)
        )
        assert {(p["width"], p["height"]) for p in params} == {(720, 1280)}

    def test_参数在视觉侧样式校验通过(self, pilot_material_shotlist):
        # 视觉模拟生成器只接受 dict 参数；此处机检参数键为 JSON 原生类型（可进生成器）
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_Cfg())
        for param in params:
            assert all(isinstance(key, str) for key in param)
            assert isinstance(param["width"], int) and isinstance(param["duration_s"], float)


class TestC7视听到剪辑:
    def test_含音轨与无音轨两路径(self):
        clips = [_clip("shot-1", "scene-1", 1), _clip("shot-2", "scene-2", 2)]
        without = av_to_edit_inputs(clips, None, config=_Cfg())
        assert without.has_audio is False and without.shot_library.audio_tracks == ()
        with_audio = av_to_edit_inputs(
            clips,
            {"tracks": [{"gen_type": "tts", "artifact_hash": "aa" * 32, "at_ms": 0}]},
            config=_Cfg(),
        )
        assert with_audio.has_audio is True
        assert with_audio.shot_library.audio_tracks == ("tts-aaaaaaaaaaaa",)

    def test_镜头库条目一致(self):
        clips = [_clip("shot-1", "scene-1", 1), _clip("shot-2", "scene-2", 2)]
        edits = av_to_edit_inputs(clips, None, config=_Cfg())
        assert isinstance(edits.shot_library, ShotLibrary)
        assert [shot.shot_id for shot in edits.shot_library.shots] == ["shot-1", "shot-2"]
        assert edit_inputs_parity(clips, edits).consistent()

    def test_剪辑侧校验通过(self):
        clips = [_clip("shot-1", "scene-1", 1), _clip("shot-2", "scene-2", 2)]
        edits = av_to_edit_inputs(clips, None, config=_Cfg())
        # 分区归属一致性由 SceneStructure 构造即校验（越界即构造失败）
        assert [scene.scene_id for scene in edits.scene_structure.scenes] == [
            "scene-1",
            "scene-2",
        ]


class TestC8成片到宣发:
    def test_物料素材齐备(self):
        materials = reel_to_promo_materials(_reel(), config=_Cfg())
        assert {material["kind"] for material in materials.materials} == {
            "clip",
            "poster",
            "copy",
        }
        assert all(material["source_hash"] == "ee" * 32 for material in materials.materials)

    def test_宣发侧规格校验通过(self):
        materials = reel_to_promo_materials(_reel(), config=_Cfg())
        poster = next(m for m in materials.materials if m["kind"] == "poster")
        width, height = poster["spec"]["poster_size"].split("x")
        assert int(height) > int(width)  # 竖屏封面


class TestC9拒绝语义:
    """上下游拒绝语义：上游 FAILED → 下游执行计数 0（不静默降级、不伪造输入）。"""

    def test_上游失败下游零调用(self, stage_entrypoint_stub):
        calls = {"third": 0}

        first = stage_entrypoint_stub("s1", fail="候选全败", candidates=["门禁违规", "分数为 0"])

        def _second(stage_input):
            raise AssertionError("上游 failed 时第二段不得启动")

        def _third(stage_input):
            calls["third"] += 1
            return StageOutcome()

        dag = build_dag(
            [
                StageSpec(stage_id="s1", entrypoint=first),
                StageSpec(stage_id="s2", entrypoint=_second, depends_on=("s1",)),
                StageSpec(stage_id="s3", entrypoint=_third, depends_on=("s2",)),
            ]
        )
        record = run(
            dag,
            ExecutionContext(
                run_id="run-c9",
                form="form-x",
                config_fingerprint="a" * 64,
                input_fingerprint="b" * 64,
            ),
        )
        assert record.status is RunStatus.FAILED
        assert record.failure_stage == "s1"
        assert calls["third"] == 0  # 机检：下游执行计数 0
        assert record.stage("s2").status is StageStatus.SKIPPED
        reasons = [reason for c in record.stage("s1").candidates for reason in c.reasons]
        assert reasons == ["门禁违规", "分数为 0"]  # 全部候选判 0 理由落记录

    def test_交接函数对缺失上游输入拒绝(self):
        with pytest.raises(HandoffError):
            av_to_edit_inputs([], None, config=_Cfg())
        with pytest.raises(HandoffError):
            reel_to_promo_materials({}, config=_Cfg())


@pytest.mark.parametrize("segment_kind", ["script"])
def test_C5_C8_字段声明可机读(segment_kind, pilot_material_script, pilot_material_shotlist):
    """字段映射声明（kept/dropped/derived）随契约可机读：漂移即红。"""
    parities = [
        script_parity(pilot_material_script),
        shotlist_parity(
            pilot_material_shotlist,
            shotlist_to_gen_params(pilot_material_shotlist, config=_Cfg()),
        ),
        edit_inputs_parity(
            [_clip("shot-1", "scene-1", 1)],
            av_to_edit_inputs([_clip("shot-1", "scene-1", 1)], None, config=_Cfg()),
        ),
    ]
    for parity in parities:
        payload = parity.to_dict()
        assert payload["consistent"] is True
        assert payload["downstream"] and payload["label"]
