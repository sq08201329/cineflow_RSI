"""EditDecisionList 与四层合法性校验单测（功能 007 / T707，先于实现编写）。

C1 落点：规范化 JSON 键序稳定（回放匹配键）；validate_edl 四层校验——
① 引用存在（镜头/音轨）② 0 ≤ in < out ≤ 镜头时长 ③ 分区归属正确 + 场景顺序不降
④ 转场规则库（类型 ∈ allowed、叠化 ≤ dissolve_max_ms、同区跳切检测，配置驱动）。
五类非法变体各拒绝（conftest make_edl 工厂）。
"""

import json

import pytest

from agents.editing.edl import EditDecisionList, validate_edl
from core.tree.errors import ValidationError

# 转场规则库（与 configs/movie.yaml editing.transition_rules 一致的字面量；
# 执行前校验与门禁共用同一规则库——配置单一事实源）
_RULES = {
    "allowed": ["cut", "dissolve", "fade"],
    "dissolve_max_ms": 2000,
    "forbid_jump_cut_within_scene": True,
}


@pytest.fixture()
def library(make_shot_library):
    return make_shot_library()


@pytest.fixture()
def structure(make_scene_structure, library):
    return make_scene_structure(library=library)


def _validate(edl, library, structure, rules=_RULES):
    validate_edl(edl, shot_library=library, scenes=structure, transition_rules=rules)


class Test规范化:
    def test_键序稳定_同义构造同_JSON(self, make_edl):
        """规范化 JSON = 回放匹配键：构造参数键序不同 → 规范化字节相同。"""
        edl_a = make_edl()
        edl_b = EditDecisionList.from_dict(
            json.loads(make_edl().canonical_json())  # 经 JSON 往返重建（键序已被打散）
        )
        assert edl_a.canonical_json() == edl_b.canonical_json()
        assert edl_a.edl_hash() == edl_b.edl_hash()

    def test_规范化输出键排序(self, make_edl):
        parsed = json.loads(make_edl().canonical_json())
        assert list(parsed) == sorted(parsed)  # 顶层键排序
        for clip in parsed["clips"]:
            assert list(clip) == sorted(clip)  # clip 键排序

    def test_edl_hash_形态(self, make_edl):
        digest = make_edl().edl_hash()
        assert len(digest) == 64 and digest == digest.lower()

    def test_无音轨规范化含空 audio(self, make_edl):
        parsed = json.loads(make_edl(audio=[]).canonical_json())
        assert parsed["audio"] == []

    def test_不同_EDL_哈希不同(self, make_edl):
        assert make_edl().edl_hash() != make_edl("scene_disorder").edl_hash()


class Test构造形状校验:
    @pytest.mark.parametrize("in_ms", [-1, 1.5, True])
    def test_非法入点(self, make_edl, in_ms):
        with pytest.raises(ValidationError, match="in_ms"):
            make_edl(clips=[{"shot_id": "shot-1", "in_ms": in_ms, "out_ms": 1000,
                             "transition": {"type": "cut", "duration_ms": 0}}])

    def test_空_clips_拒绝(self, make_edl):
        with pytest.raises(ValidationError, match="clips"):
            make_edl(clips=[])

    def test_非法增益(self, make_edl):
        with pytest.raises(ValidationError, match="gain"):
            make_edl(audio=[{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.0}])

    def test_非法转场时长(self, make_edl):
        with pytest.raises(ValidationError, match="duration_ms"):
            make_edl(clips=[{"shot_id": "shot-1", "in_ms": 0, "out_ms": 1000,
                             "transition": {"type": "cut", "duration_ms": -1}}])


class Test合法EDL通过:
    def test_默认合法变体(self, make_edl, library, structure):
        _validate(make_edl(), library, structure)  # 不抛即通过

    def test_无音轨合法(self, make_edl, library, structure):
        _validate(make_edl(audio=[]), library, structure)


class Test第一层引用存在:
    def test_引用不存在镜头(self, make_edl, library, structure):
        with pytest.raises(ValidationError, match="shot-999"):
            _validate(make_edl("unknown_ref"), library, structure)

    def test_引用不存在音轨(self, make_edl, library, structure):
        edl = make_edl(audio=[{"track_ref": "bgm-999", "at_ms": 0, "gain": 0.5}])
        with pytest.raises(ValidationError, match="bgm-999"):
            _validate(edl, library, structure)


class Test第二层出入点越界:
    def test_出点越界(self, make_edl, library, structure):
        with pytest.raises(ValidationError, match="越界|out_ms"):
            _validate(make_edl("out_of_bounds"), library, structure)

    def test_入点不小于出点(self, make_edl, library, structure):
        edl = make_edl(clips=[{"shot_id": "shot-1", "in_ms": 2000, "out_ms": 2000,
                               "transition": {"type": "cut", "duration_ms": 0}}])
        with pytest.raises(ValidationError, match="in_ms"):
            _validate(edl, library, structure)


class Test第三层场景分区:
    def test_跨分区选镜(self, make_edl, library, structure):
        """shot-orphan 在库但未分区——分区归属校验拒绝。"""
        with pytest.raises(ValidationError, match="跨分区|分区"):
            _validate(make_edl("cross_partition"), library, structure)

    def test_场景乱序(self, make_edl, library, structure):
        with pytest.raises(ValidationError, match="乱序|顺序"):
            _validate(make_edl("scene_disorder"), library, structure)


class Test第四层转场规则库:
    def test_非法转场类型(self, make_edl, library, structure):
        with pytest.raises(ValidationError, match="wipe"):
            _validate(make_edl("illegal_transition"), library, structure)

    def test_叠化超上限(self, make_edl, library, structure):
        clips = [
            {"shot_id": "shot-1", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "dissolve", "duration_ms": 2500}},  # > 2000 上限
            {"shot_id": "shot-3", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ]
        with pytest.raises(ValidationError, match="dissolve_max_ms|叠化"):
            _validate(make_edl(clips=clips), library, structure)

    def test_同区跳切拒绝(self, make_edl, library, structure):
        """同区连续镜头用 cut 衔接 = 跳切（forbid_jump_cut_within_scene 开启时拒绝）。"""
        clips = [
            {"shot_id": "shot-1", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "cut", "duration_ms": 0}},  # 同区衔接 shot-2 用 cut
            {"shot_id": "shot-2", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ]
        with pytest.raises(ValidationError, match="跳切"):
            _validate(make_edl(clips=clips), library, structure)

    def test_跳切检测配置关闭则放行(self, make_edl, library, structure):
        """规则库配置驱动：关闭 forbid_jump_cut_within_scene 后同序列合法。"""
        clips = [
            {"shot_id": "shot-1", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-2", "in_ms": 0, "out_ms": 2000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ]
        _validate(
            make_edl(clips=clips),
            library,
            structure,
            rules={**_RULES, "forbid_jump_cut_within_scene": False},
        )
