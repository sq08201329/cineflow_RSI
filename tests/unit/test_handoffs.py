"""功能 015 US2（T1514）：`agents/pilot/handoffs.py` 四段交接测试（契约 C5~C9）。

四段交接是**纯映射函数**：上游导出 → 下游输入，字段集双向锁定（含枚举值）。
每段配"字段映射声明"（kept/dropped）与数量守恒断言——上游导出字段集 == 下游输入
字段集 ∪ 显式声明丢弃字段（丢字段必须写进声明，不许静默丢）。
"""

import pytest

from agents.editing.shots import ShotLibrary
from agents.pilot.handoffs import (
    HandoffError,
    PromoMaterials,
    av_to_edit_inputs,
    edit_inputs_parity,
    reel_to_promo_materials,
    script_parity,
    script_to_segment,
    shotlist_parity,
    shotlist_to_gen_params,
)
from agents.storyboard.script import ScriptSegment

FORM = "shortdrama"  # 形态值只作参数透传（交接函数不分支）


class _ClipSpecCfg:
    """视觉形态配置桩：只暴露 clip_spec（交接函数只读配置，不碰加载器）。"""

    def __init__(self, **clip_spec):
        self.clip_spec = {
            "width": 540,
            "height": 960,
            "fps": 8,
            "duration_seconds": 1.5,
            "codec": "h264",
        }
        self.clip_spec.update(clip_spec)


def _clips(*shot_ids) -> list[dict]:
    """视觉阶段产物（JSON 明细形态）：每镜一条片段引用。"""
    return [
        {
            "shot_id": shot_id,
            "scene_id": "scene-1" if shot_id.endswith("1") else "scene-2",
            "artifact_hash": (f"{index + 1:02d}" * 32)[:64],
            "duration_ms": 1500,
            "gen_params": {"shot_id": shot_id, "width": 540, "height": 960},
            "score": 0.8,
        }
        for index, shot_id in enumerate(shot_ids)
    ]


class TestC5剧本到分镜:
    def test_导出复用_009_且校验通过(self, pilot_material_script):
        segment = script_to_segment(pilot_material_script)
        assert isinstance(segment, ScriptSegment)
        assert segment.scene_ids() == ("scene-1", "scene-2")
        assert segment.key_line_ids() == ("s1-l1", "s2-l1")
        # 情绪/种类逐条保真（枚举值一致）
        assert segment.line("s1-l1").kind == "dialogue"
        assert segment.line("s2-l2").emotion == "sorrow"

    def test_字段集双向锁定(self, pilot_material_script):
        parity = script_parity(pilot_material_script)
        assert parity.consistent()
        # 上游行字段集：场景归属与角色是上游独有面（下游用结构承载），须显式声明
        assert parity.dropped == {"scene_id", "character"}
        assert {"line_id", "kind", "text", "key", "emotion"} <= parity.downstream

    def test_非工件输入被拒(self):
        with pytest.raises(HandoffError):
            script_to_segment({"not": "artifact"})

    def test_类型不符即拒(self, pilot_material_script):
        segment = script_to_segment(pilot_material_script)
        with pytest.raises(HandoffError):
            script_to_segment(segment)  # 已导出的段落不得再次交接（防重复映射）


class TestC6分镜到视觉:
    def test_镜头数等于参数数且尺寸来自配置(self, pilot_material_shotlist):
        cfg = _ClipSpecCfg()
        params = shotlist_to_gen_params(pilot_material_shotlist, config=cfg)
        assert len(params) == len(pilot_material_shotlist.shots) == 2
        for shot, param in zip(pilot_material_shotlist.shots, params, strict=True):
            assert param["shot_id"] == shot.shot_id
            # 接线坑守卫：竖屏规格必须由 clip_spec 派生（缺省会回落 320x240 被门禁判 0）
            assert (param["width"], param["height"]) == (540, 960)
            assert param["fps"] == 8
            assert param["duration_s"] == 1.5

    def test_枚举值逐镜保真(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_ClipSpecCfg())
        shots = pilot_material_shotlist.shots
        assert [p["shot_size"] for p in params] == [s.shot_size for s in shots]
        assert [p["camera"] for p in params] == [s.camera for s in shots]
        assert [p["movement"] for p in params] == [s.movement for s in shots]
        assert [p["side"] for p in params] == [s.side for s in shots]

    def test_字段集双向锁定与风格派生(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_ClipSpecCfg())
        parity = shotlist_parity(pilot_material_shotlist, params)
        assert parity.consistent()
        assert parity.dropped == {"covers", "alternatives"}  # 承接清单在视觉侧无面
        assert parity.derived == {"style", "seed_tier", "duration_s", "width", "height", "fps"}
        assert all(p["style"] for p in params)  # 风格由景别/运动派生（非空）

    def test_数量不守恒即拒(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_ClipSpecCfg())
        with pytest.raises(HandoffError):
            shotlist_parity(pilot_material_shotlist, params[:-1])

    def test_缺_clip_spec_即拒(self, pilot_material_shotlist):
        class _NoSpec:
            pass

        with pytest.raises(HandoffError):
            shotlist_to_gen_params(pilot_material_shotlist, config=_NoSpec())


class TestC7视听到剪辑:
    def test_镜头库条目数与片段数一致(self):
        edits = av_to_edit_inputs(_clips("shot-1", "shot-2"), None, config=_ClipSpecCfg())
        assert isinstance(edits.shot_library, ShotLibrary)
        assert len(edits.shot_library.shots) == 2
        assert [s.shot_id for s in edits.shot_library.shots] == ["shot-1", "shot-2"]
        assert [s.artifact_hash for s in edits.shot_library.shots] == [
            clip["artifact_hash"] for clip in _clips("shot-1", "shot-2")
        ]
        assert edits.shot_library.audio_tracks == ()
        assert edits.has_audio is False  # 无音轨如实标注（不伪造）

    def test_含音轨路径(self):
        audio = {
            "tracks": [
                {"gen_type": "tts", "artifact_hash": "aa" * 32, "at_ms": 0, "duration_ms": 1200},
                {"gen_type": "music", "artifact_hash": "bb" * 32, "at_ms": 0, "duration_ms": 1500},
            ]
        }
        edits = av_to_edit_inputs(_clips("shot-1"), audio, config=_ClipSpecCfg())
        assert edits.has_audio is True
        assert edits.shot_library.audio_tracks == ("tts-aaaaaaaaaaaa", "music-bbbbbbbbbbbb")

    def test_分区结构与镜头归属一致(self):
        edits = av_to_edit_inputs(_clips("shot-1", "shot-2"), None, config=_ClipSpecCfg())
        structure = edits.scene_structure
        assert [scene.scene_id for scene in structure.scenes] == ["scene-1", "scene-2"]
        assert structure.scenes[0].shot_ids == ("shot-1",)
        # 归属一致（构造即校验）：镜头 scene_id == 所属分区
        assert structure.shot_library.get("shot-2").scene_id == "scene-2"

    def test_字段集双向锁定(self):
        clips = _clips("shot-1", "shot-2")
        edits = av_to_edit_inputs(clips, None, config=_ClipSpecCfg())
        parity = edit_inputs_parity(clips, edits)
        assert parity.consistent()
        assert parity.dropped == {"gen_params", "score"}  # 生成参数/评分不进剪辑输入面
        assert parity.derived == {"metadata"}

    def test_空片段列表拒绝(self):
        with pytest.raises(HandoffError):
            av_to_edit_inputs([], None, config=_ClipSpecCfg())

    def test_明细可序列化为_json(self):
        import json

        edits = av_to_edit_inputs(_clips("shot-1"), None, config=_ClipSpecCfg())
        payload = json.loads(json.dumps(edits.to_dict(), ensure_ascii=False))
        assert payload["has_audio"] is False
        assert payload["shot_library"]["shots"][0]["shot_id"] == "shot-1"


class TestC8成片到宣发:
    def _reel(self) -> dict:
        return {
            "artifact_hash": "cc" * 32,
            "duration_ms": 120000,
            "width": 540,
            "height": 960,
            "fps": 8,
        }

    def test_物料齐备且规格来自配置(self):
        class _PromoCfg:
            material_spec = {
                "max_copy_chars": 40,
                "poster_size": "1080x1920",
                "max_duration_seconds": 20,
            }

        materials = reel_to_promo_materials(self._reel(), config=_PromoCfg())
        assert isinstance(materials, PromoMaterials)
        kinds = [material["kind"] for material in materials.materials]
        assert kinds == ["clip", "poster", "copy"]
        assert materials.reel_hash == "cc" * 32
        for material in materials.materials:
            assert material["source_hash"] == "cc" * 32  # 素材引用齐备
            assert material["spec"]  # 规格非空

    def test_裁剪规则按配置生效(self):
        class _PromoCfg:
            material_spec = {
                "max_copy_chars": 40,
                "poster_size": "1080x1920",
                "max_duration_seconds": 20,
            }

        materials = reel_to_promo_materials(self._reel(), config=_PromoCfg())
        by_kind = {material["kind"]: material for material in materials.materials}
        assert by_kind["clip"]["spec"]["duration_ms"] == 20_000  # 成片片段按配置裁剪
        assert by_kind["poster"]["spec"]["poster_size"] == "1080x1920"
        assert by_kind["copy"]["spec"]["max_copy_chars"] == 40

    def test_明细可序列化且含形态无关字段(self):
        import json

        class _PromoCfg:
            material_spec = {
                "max_copy_chars": 40,
                "poster_size": "1080x1920",
                "max_duration_seconds": 20,
            }

        materials = reel_to_promo_materials(self._reel(), config=_PromoCfg())
        payload = json.loads(json.dumps(materials.to_dict(), ensure_ascii=False))
        assert payload["reel_ref"] and payload["materials"]

    def test_成片缺失拒绝(self):
        class _PromoCfg:
            material_spec = {
                "max_copy_chars": 40,
                "poster_size": "1080x1920",
                "max_duration_seconds": 20,
            }

        with pytest.raises(HandoffError):
            reel_to_promo_materials({}, config=_PromoCfg())


class Test静态断言:
    def test_交接模块无形态分支(self):
        from pathlib import Path

        import agents.pilot.handoffs as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for banned in ("shortdrama", '"movie"', "form ==", "form==", "form is "):
            assert banned not in source, banned
