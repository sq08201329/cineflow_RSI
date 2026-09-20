"""ShotEntry / ShotLibrary / SceneStructure 单测（功能 007 / T705，先于实现编写）。

C1 第③层的结构底座：ShotEntry 字段校验；ShotLibrary 镜头/音轨检索；
SceneStructure 分区校验（shot_ids 无重复、引用存在、归属与 ShotEntry.scene_id 一致、
分区有序）——场景分区约束（澄清 Q2 决议）的执行前校验依据。
"""

import pytest

from agents.editing.shots import Scene, SceneStructure, ShotEntry, ShotLibrary
from core.tree.errors import ValidationError


def _entry(**overrides):
    fields = {
        "shot_id": "shot-1",
        "artifact_hash": "ab" * 32,
        "duration_ms": 4000,
        "scene_id": "scene-a",
    }
    fields.update(overrides)
    return ShotEntry(**fields)


class TestShotEntry校验:
    def test_合法构造(self):
        entry = _entry(metadata={"source": "t"})
        assert entry.shot_id == "shot-1"
        assert entry.duration_ms == 4000
        assert entry.scene_id == "scene-a"
        assert entry.metadata == {"source": "t"}

    @pytest.mark.parametrize("shot_id", ["", None, 123])
    def test_非法_shot_id(self, shot_id):
        with pytest.raises(ValidationError, match="shot_id"):
            _entry(shot_id=shot_id)

    @pytest.mark.parametrize(
        "artifact_hash",
        ["ab", "AB" * 32, "zz" * 32, None],  # 过短 / 大写 / 非 hex / 非字符串
    )
    def test_非法_artifact_hash(self, artifact_hash):
        with pytest.raises(ValidationError, match="artifact_hash"):
            _entry(artifact_hash=artifact_hash)

    @pytest.mark.parametrize("duration_ms", [0, -1, 1.5, True, "4000"])
    def test_非法_duration_ms(self, duration_ms):
        """镜头时长必须为正整数毫秒（bool 不接受）。"""
        with pytest.raises(ValidationError, match="duration_ms"):
            _entry(duration_ms=duration_ms)

    @pytest.mark.parametrize("scene_id", ["", None])
    def test_非法_scene_id(self, scene_id):
        with pytest.raises(ValidationError, match="scene_id"):
            _entry(scene_id=scene_id)


class TestShotLibrary:
    def test_合法库检索(self, make_shot_library):
        library = make_shot_library()
        assert len(library.shots) == 7  # 6 分区镜头 + shot-orphan
        assert library.get("shot-1").scene_id == "scene-a"
        assert library.has_shot("shot-6")
        assert not library.has_shot("shot-999")
        assert library.has_audio("bgm-01")
        assert not library.has_audio("bgm-999")

    def test_未知镜头_get_拒绝(self, make_shot_library):
        with pytest.raises(ValidationError, match="shot-999"):
            make_shot_library().get("shot-999")

    def test_重复_shot_id_拒绝(self, make_shot_library):
        library = make_shot_library()
        with pytest.raises(ValidationError, match="重复"):
            ShotLibrary(shots=[library.get("shot-1"), library.get("shot-1")])

    def test_空镜头库拒绝(self):
        with pytest.raises(ValidationError, match="镜头库"):
            ShotLibrary(shots=[])

    def test_重复音轨拒绝(self, make_shot_library):
        with pytest.raises(ValidationError, match="音轨"):
            make_shot_library(audio_tracks=("bgm-01", "bgm-01"))


class TestSceneStructure:
    def test_合法结构分区有序(self, make_scene_structure):
        structure = make_scene_structure()
        assert [s.scene_id for s in structure.scenes] == ["scene-a", "scene-b", "scene-c"]
        # 分区序号 = scenes 列表序（EDL 场景顺序不降校验的依据）
        assert structure.scene_index_of("shot-1") == 0
        assert structure.scene_index_of("shot-4") == 1
        assert structure.scene_index_of("shot-6") == 2

    def test_未分区镜头_scene_index_of_拒绝(self, make_scene_structure):
        """shot-orphan 在库但不归属任何分区——跨分区违规的检测口径。"""
        with pytest.raises(ValidationError, match="shot-orphan"):
            make_scene_structure().scene_index_of("shot-orphan")

    def test_跨分区重复拒绝(self, make_scene_structure):
        with pytest.raises(ValidationError, match="重复"):
            make_scene_structure("duplicate")

    def test_引用不存在镜头拒绝(self, make_scene_structure):
        with pytest.raises(ValidationError, match="shot-999"):
            make_scene_structure("unknown")

    def test_归属不一致拒绝(self, make_scene_structure):
        """shot-3 的 ShotEntry.scene_id=scene-b 却被分进 scene-a——分区归属必须一致。"""
        with pytest.raises(ValidationError, match="归属"):
            make_scene_structure("mismatched")

    def test_分区接受_dict_形态(self, make_shot_library):
        structure = SceneStructure(
            scenes=[
                {"scene_id": "scene-a", "shot_ids": ["shot-1", "shot-2"]},
                {"scene_id": "scene-b", "shot_ids": ["shot-3", "shot-4"]},
                {"scene_id": "scene-c", "shot_ids": ["shot-5", "shot-6"]},
            ],
            shot_library=make_shot_library(),
        )
        assert isinstance(structure.scenes[0], Scene)
        assert structure.scene_index_of("shot-2") == 0

    def test_重复_scene_id_拒绝(self, make_shot_library):
        with pytest.raises(ValidationError, match="scene_id"):
            SceneStructure(
                scenes=[
                    Scene(scene_id="scene-a", shot_ids=("shot-1", "shot-2")),
                    Scene(scene_id="scene-a", shot_ids=("shot-3",)),
                ],
                shot_library=make_shot_library(),
            )
