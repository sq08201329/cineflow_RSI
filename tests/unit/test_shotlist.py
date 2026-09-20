"""ShotList 与执行前三层合法性校验单测（功能 008 / T807，先于实现编写）。

C1 落点：规范化 JSON 键序稳定（回放匹配键 + 运营表唯一键分量）；schema_version
字段（FR-012 下游衔接契约）；validate_shotlist 三层校验——① 承接的剧本行存在
② 场景承接（每场景 ≥1 镜 + key 行逐条被 covers 承接）③ 景别/机位/运动档位在
规则库枚举内（配置驱动，与 rule.shot_grammar 门禁共用同一规则库）。
四类非法变体各拒绝（conftest make_shotlist 工厂）；alternatives 备选数存在性与
取值域（≥1 整数）断言。
"""

import json

import pytest

from agents.storyboard.shotlist import ShotList, validate_shotlist
from core.tree.errors import ValidationError

# 规则库（与 configs/movie.yaml storyboard.shot_grammar 一致的字面量；
# 执行前校验与 rule.shot_grammar 门禁共用同一规则库——配置单一事实源）
_GRAMMAR = {
    "shot_sizes": ["extreme_close_up", "close_up", "medium", "full", "wide"],
    "max_size_jump": 2,
    "max_same_size_run": 2,
    "camera_positions": ["eye_level", "low_angle", "high_angle", "over_shoulder", "side"],
    "movements": ["static", "pan", "tilt", "dolly", "handheld"],
}


@pytest.fixture()
def script(make_script_segment):
    return make_script_segment()


def _validate(shotlist, script, rules=_GRAMMAR):
    validate_shotlist(shotlist, script, rules)


class Test规范化:
    def test_键序稳定_同义构造同_JSON(self, make_shotlist):
        """规范化 JSON = 回放匹配键：经 JSON 往返重建后字节仍相同。"""
        shotlist_a = make_shotlist()
        shotlist_b = ShotList.from_dict(json.loads(shotlist_a.canonical_json()))
        assert shotlist_a.canonical_json() == shotlist_b.canonical_json()
        assert shotlist_a.shotlist_hash() == shotlist_b.shotlist_hash()

    def test_规范化输出键排序(self, make_shotlist):
        parsed = json.loads(make_shotlist().canonical_json())
        assert list(parsed) == sorted(parsed)  # 顶层键排序
        for shot in parsed["shots"]:
            assert list(shot) == sorted(shot)  # 逐镜键排序

    def test_schema_版本字段在规范化输出内(self, make_shotlist):
        """FR-012：ShotList schema 稳定可供视觉线（004）与剪辑线（007）消费。"""
        parsed = json.loads(make_shotlist().canonical_json())
        assert parsed["schema_version"] == ShotList.SCHEMA_VERSION
        assert ShotList.SCHEMA_VERSION == "1.0.0"

    def test_shotlist_hash_形态(self, make_shotlist):
        digest = make_shotlist().shotlist_hash()
        assert len(digest) == 64 and digest == digest.lower()

    def test_不同_ShotList_哈希不同(self, make_shotlist):
        assert make_shotlist().shotlist_hash() != make_shotlist("size_out_of_range").shotlist_hash()

    def test_字典往返一致(self, make_shotlist):
        shotlist = make_shotlist()
        assert ShotList.from_dict(shotlist.to_dict()).to_dict() == shotlist.to_dict()


class Test镜头与清单查询:
    def test_镜头序列与场景归属(self, make_shotlist):
        shotlist = make_shotlist()
        assert shotlist.shot_ids() == tuple(f"shot-{i:02d}" for i in range(1, 10))
        assert shotlist.shots_of_scene("scene-2") == ("shot-04", "shot-05", "shot-06")
        assert shotlist.total_est_duration_ms() == 10250

    def test_承接行全集(self, make_script_segment, make_shotlist):
        script = make_script_segment()
        assert make_shotlist().covered_line_ids() == script.line_ids()

    def test_按_id_取镜头(self, make_shotlist):
        assert make_shotlist().shot("shot-02").shot_size == "medium"
        with pytest.raises(ValidationError, match="shot-99"):
            make_shotlist().shot("shot-99")


class Test构造形状校验:
    def test_空_shots_拒绝(self, make_shotlist):
        with pytest.raises(ValidationError, match="shots"):
            make_shotlist(shots=[])

    def test_镜头_id_重复拒绝(self, make_shotlist):
        shots = make_shotlist().to_dict()["shots"]
        shots[1] = {**shots[1], "shot_id": "shot-01"}
        with pytest.raises(ValidationError, match="shot_id"):
            ShotList(shots=shots)

    @pytest.mark.parametrize("est_duration_ms", [0, -125, 1000.5, True])
    def test_估算时长非法拒绝(self, make_shotlist, est_duration_ms):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "est_duration_ms": est_duration_ms}
        with pytest.raises(ValidationError, match="est_duration_ms"):
            ShotList(shots=shots)

    def test_covers_不得为空(self, make_shotlist):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "covers": []}
        with pytest.raises(ValidationError, match="covers"):
            ShotList(shots=shots)

    @pytest.mark.parametrize("side", ["C", "a", None, 1])
    def test_侧别取值非法拒绝(self, make_shotlist, side):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "side": side}
        with pytest.raises(ValidationError, match="side"):
            ShotList(shots=shots)


class Test备选数纪律:
    """alternatives（备选数，下游视觉线参数）：存在且为 ≥1 整数。"""

    def test_缺备选数字段即报错(self, make_shotlist):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {k: v for k, v in shots[0].items() if k != "alternatives"}
        with pytest.raises(ValidationError, match="alternatives"):
            ShotList(shots=shots)

    @pytest.mark.parametrize("alternatives", [0, -1, 1.5, "2", True])
    def test_备选数取值域非法拒绝(self, make_shotlist, alternatives):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "alternatives": alternatives}
        with pytest.raises(ValidationError, match="alternatives"):
            ShotList(shots=shots)

    def test_合法备选数入库(self, make_shotlist):
        assert make_shotlist().shot("shot-01").alternatives == 2


class Test合法ShotList通过:
    def test_默认合法变体(self, make_shotlist, script):
        _validate(make_shotlist(), script)  # 不抛即通过

    def test_同场景多镜承接同场景多行(self, make_shotlist, script):
        """普通台词合并/拆分不违规（澄清 Q1）：一镜承接两行仍合法。"""
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "covers": ["s1-l1", "s1-l3"]}
        shots[2] = {**shots[2], "covers": ["s1-l3"]}
        _validate(ShotList(shots=shots), script)


class Test第一层引用行存在:
    def test_承接不存在的剧本行(self, make_shotlist, script):
        with pytest.raises(ValidationError, match="s1-l9"):
            _validate(make_shotlist("unknown_line"), script)


class Test第二层场景承接:
    def test_场景无镜头(self, make_shotlist, script):
        with pytest.raises(ValidationError, match="scene-2"):
            _validate(make_shotlist("scene_uncovered"), script)

    def test_关键行未承接(self, make_shotlist, script):
        """必覆盖清单逐条承接（澄清 Q1）：scene-2 仍有镜头，但 key 行 s2-l1 无承接。"""
        with pytest.raises(ValidationError, match="s2-l1"):
            _validate(make_shotlist("key_line_uncovered"), script)

    def test_镜头引用不存在场景(self, make_shotlist, script):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "scene_id": "scene-9"}
        with pytest.raises(ValidationError, match="scene-9"):
            _validate(ShotList(shots=shots), script)


class Test第三层档位枚举:
    def test_景别档位越界(self, make_shotlist, script):
        with pytest.raises(ValidationError, match="extreme_wide"):
            _validate(make_shotlist("size_out_of_range"), script)

    def test_机位档位越界(self, make_shotlist, script):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "camera": "drone"}
        with pytest.raises(ValidationError, match="drone"):
            _validate(ShotList(shots=shots), script)

    def test_运动档位越界(self, make_shotlist, script):
        shots = make_shotlist().to_dict()["shots"]
        shots[0] = {**shots[0], "movement": "zoom"}
        with pytest.raises(ValidationError, match="zoom"):
            _validate(ShotList(shots=shots), script)

    def test_规则库缺失即报错(self, make_shotlist, script):
        """规则库配置缺失不允许静默放过门禁（C3 / 原则五）。"""
        with pytest.raises(ValidationError, match="shot_sizes"):
            _validate(make_shotlist(), script, rules={})
