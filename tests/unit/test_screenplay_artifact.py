"""剧本工件 ScriptArtifact 单测（功能 009 / T905，先于实现编写）。

C1/C4~C9 的结构化标记完整性是"评估器确定性"的前提（research 决策 3）：三段产出
（outline/scenes/script）共用同一结构化标记——beats 节拍清单 / scenes 场景头
（location 段与字段机检一致）/ characters 角色表含 aliases / lines 行（归属场景、
关键行标注、情绪）；**缺任一标记即拒绝**（缺标记 = 评估器无从确定性解析，不允许静默
降级）。工件内容寻址（规范化 JSON 的 BLAKE3）即运营表唯一键分量与回放匹配键。
"""

import blake3
import pytest

from agents.screenplay.artifact import (
    LINE_KINDS,
    SCHEMA_VERSION,
    SIDES,
    ScriptArtifact,
    heading_parts,
)
from core.tree.errors import ValidationError


class Test三段构造与解析:
    @pytest.mark.parametrize("stage", ["outline", "scenes", "script"])
    def test_三段均可构造(self, make_script_artifact, stage):
        artifact = make_script_artifact(stage=stage)
        assert artifact.stage == stage
        assert artifact.schema_version == SCHEMA_VERSION

    def test_to_dict_from_dict_往返一致(self, script_artifact):
        payload = script_artifact.to_dict()
        restored = ScriptArtifact.from_dict(payload)
        assert restored == script_artifact
        assert restored.canonical_json() == script_artifact.canonical_json()

    def test_字段集与数据模型一致(self, script_artifact):
        """schema 快照：顶层字段名锁定（下游 008 导出与总结函数按此消费）。"""
        assert set(script_artifact.to_dict()) == {
            "schema_version",
            "stage",
            "text",
            "beats",
            "scenes",
            "characters",
            "lines",
        }
        assert set(script_artifact.beats[0].to_dict()) == {
            "beat_id",
            "act",
            "required",
            "description",
        }
        assert set(script_artifact.scenes[0].to_dict()) == {
            "scene_id",
            "heading",
            "location",
            "time_marker",
            "characters",
            "axis_base",
        }
        assert set(script_artifact.characters[0].to_dict()) == {"name", "aliases"}
        assert set(script_artifact.lines[0].to_dict()) == {
            "line_id",
            "scene_id",
            "kind",
            "text",
            "character",
            "key",
            "emotion",
        }

    def test_行类型与侧别枚举与_008_同域(self):
        """枚举值锁定（导出 008 ScriptSegment 的口径，快照断言在 T911 双向复核）。"""
        assert LINE_KINDS == ("dialogue", "action")
        assert SIDES == ("A", "B")

    def test_非_dict_输入拒绝(self):
        with pytest.raises(ValidationError, match="dict"):
            ScriptArtifact.from_dict(["not-a-mapping"])

    def test_非法阶段拒绝(self, make_script_artifact):
        with pytest.raises(ValidationError, match="stage"):
            make_script_artifact(stage="treatment")

    def test_文本为空拒绝(self, make_script_artifact):
        with pytest.raises(ValidationError, match="text"):
            make_script_artifact(text="")

    def test_schema_version_为空拒绝(self, make_script_artifact):
        with pytest.raises(ValidationError, match="schema_version"):
            make_script_artifact(schema_version="")


class Test结构化标记完整性:
    """缺 beats/scenes/characters/lines 即拒绝（确定性解析的前提，不允许静默降级）。"""

    @pytest.mark.parametrize("key", ["beats", "scenes", "characters", "lines"])
    def test_缺标记即拒绝(self, script_artifact, key):
        payload = script_artifact.to_dict()
        del payload[key]
        with pytest.raises(ValidationError, match=key):
            ScriptArtifact.from_dict(payload)

    @pytest.mark.parametrize("key", ["beats", "scenes", "characters", "lines"])
    def test_标记为空即拒绝(self, script_artifact, key):
        payload = script_artifact.to_dict()
        payload[key] = []
        with pytest.raises(ValidationError, match=key):
            ScriptArtifact.from_dict(payload)

    def test_节拍_id_重复拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["beats"].append(dict(payload["beats"][0]))
        with pytest.raises(ValidationError, match="beat_id"):
            ScriptArtifact.from_dict(payload)

    def test_节拍_required_非布尔拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["beats"][0]["required"] = "yes"
        with pytest.raises(ValidationError, match="required"):
            ScriptArtifact.from_dict(payload)

    def test_场景_id_重复拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["scenes"].append(dict(payload["scenes"][0]))
        with pytest.raises(ValidationError, match="scene_id"):
            ScriptArtifact.from_dict(payload)

    def test_行_id_跨场景重复拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["lines"][3]["line_id"] = payload["lines"][0]["line_id"]
        with pytest.raises(ValidationError, match="line_id"):
            ScriptArtifact.from_dict(payload)

    def test_行归属场景不存在拒绝(self, script_artifact):
        """行必须归属既有场景（否则导出 008 时会静默丢行——宁拒绝不丢数据）。"""
        payload = script_artifact.to_dict()
        payload["lines"][0]["scene_id"] = "scene-9"
        with pytest.raises(ValidationError, match="scene-9"):
            ScriptArtifact.from_dict(payload)

    def test_行类型越界拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["lines"][0]["kind"] = "lyric"
        with pytest.raises(ValidationError, match="kind"):
            ScriptArtifact.from_dict(payload)

    @pytest.mark.parametrize(
        "heading",
        ["病房 - 夜", "走廊 - 夜 - 日内", "内景|病房|夜", ""],
        ids=["段数不足", "首段非内景外景", "分隔符错", "空"],
    )
    def test_场景头格式非法拒绝(self, script_artifact, heading):
        payload = script_artifact.to_dict()
        payload["scenes"][0]["heading"] = heading
        with pytest.raises(ValidationError, match="heading"):
            ScriptArtifact.from_dict(payload)

    @pytest.mark.parametrize("value", [-1, 3.5, "30", True])
    def test_时间戳非非负整数拒绝(self, script_artifact, value):
        payload = script_artifact.to_dict()
        payload["scenes"][0]["time_marker"] = value
        with pytest.raises(ValidationError, match="time_marker"):
            ScriptArtifact.from_dict(payload)

    def test_轴向基准越界拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["scenes"][0]["axis_base"] = "C"
        with pytest.raises(ValidationError, match="axis_base"):
            ScriptArtifact.from_dict(payload)

    def test_角色名重复拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["characters"].append({"name": "林静", "aliases": []})
        with pytest.raises(ValidationError, match="林静"):
            ScriptArtifact.from_dict(payload)

    def test_别名跨角色复用拒绝(self, script_artifact):
        """同一写法不得多义（别名表是实体一致性的规范化依据）。"""
        payload = script_artifact.to_dict()
        payload["characters"][1]["aliases"] = ["阿静"]
        with pytest.raises(ValidationError, match="阿静"):
            ScriptArtifact.from_dict(payload)

    def test_关键行标注非布尔拒绝(self, script_artifact):
        payload = script_artifact.to_dict()
        payload["lines"][0]["key"] = 1
        with pytest.raises(ValidationError, match="key"):
            ScriptArtifact.from_dict(payload)

    def test_缺关键行标注默认非关键(self, script_artifact):
        payload = script_artifact.to_dict()
        del payload["lines"][0]["key"]
        restored = ScriptArtifact.from_dict(payload)
        assert restored.lines[0].key is False


class Test内容寻址:
    def test_哈希形态(self, script_artifact):
        digest = script_artifact.artifact_hash()
        assert len(digest) == 64
        assert digest == digest.lower()
        assert int(digest, 16) >= 0  # 十六进制

    def test_哈希源自规范化_json(self, script_artifact):
        expected = blake3.blake3(script_artifact.canonical_json().encode()).hexdigest()
        assert script_artifact.artifact_hash() == expected

    def test_重算稳定(self, script_artifact):
        assert script_artifact.artifact_hash() == script_artifact.artifact_hash()

    def test_规范化_json_键序稳定(self, make_script_artifact):
        """嵌套字典键序乱排不影响规范化 JSON（回放精确匹配键的依据）。"""
        artifact = make_script_artifact()
        payload = artifact.to_dict()
        shuffled = {key: payload[key] for key in list(payload)[::-1]}
        shuffled["beats"] = [
            {key: beat[key] for key in list(beat)[::-1]} for beat in payload["beats"]
        ]
        assert ScriptArtifact.from_dict(shuffled).canonical_json() == artifact.canonical_json()

    @pytest.mark.parametrize(
        "overrides",
        [{"stage": "script"}, {"text": "另一份大纲文本"}],
        ids=["阶段不同", "文本不同"],
    )
    def test_内容不同哈希不同(self, make_script_artifact, overrides):
        base = make_script_artifact()
        assert make_script_artifact(**overrides).artifact_hash() != base.artifact_hash()

    def test_行数不同哈希不同(self, make_script_artifact):
        base = make_script_artifact()
        longer = make_script_artifact(lines_per_scene=4)
        assert longer.artifact_hash() != base.artifact_hash()


class Test访问器:
    def test_场景与行索引(self, script_artifact):
        assert script_artifact.scene_ids() == ("scene-1", "scene-2", "scene-3")
        assert script_artifact.line_ids() == tuple(
            f"s{scene}-l{line}" for scene in (1, 2, 3) for line in (1, 2, 3)
        )
        assert [line.line_id for line in script_artifact.lines_of_scene("scene-2")] == [
            "s2-l1",
            "s2-l2",
            "s2-l3",
        ]
        assert script_artifact.scene("scene-3").location == "天台"

    def test_节拍清单与必需子集(self, script_artifact):
        assert len(script_artifact.beat_ids()) == 8  # 默认只含 required 节拍
        assert "theme_stated" not in script_artifact.beat_ids()  # 可选节拍可缺
        assert script_artifact.required_beat_ids() == script_artifact.beat_ids()

    def test_角色表与登记名集合(self, script_artifact):
        assert script_artifact.character_names() == ("林静", "陈默", "周医生")
        assert script_artifact.registered_names() == frozenset(
            {"林静", "阿静", "陈默", "默哥", "周医生"}
        )
        assert script_artifact.aliases_of("林静") == ("阿静",)

    def test_行统计(self, script_artifact):
        assert script_artifact.total_lines() == 9
        assert script_artifact.count_kind("dialogue") == 6
        assert script_artifact.count_kind("action") == 3
        assert script_artifact.key_line_ids() == ("s1-l2", "s2-l2", "s3-l3")

    def test_指称清单按出现顺序去重(self, script_artifact):
        """角色指称 = 行归属 + 场景出场清单（proxy.entity_consistency 的扫描面）。"""
        references = script_artifact.character_references()
        assert references[0] == "林静"
        assert set(references) == {"林静", "陈默", "周医生"}
        assert len(references) == len(set(references))

    def test_页数换算(self, script_artifact):
        assert script_artifact.page_count(45) == pytest.approx(0.2)
        assert script_artifact.page_count(9) == pytest.approx(1.0)
        assert script_artifact.page_count(3) == pytest.approx(3.0)

    def test_分页工件行数(self, make_script_artifact):
        artifact = make_script_artifact(pages=3, lines_per_page=45)
        assert artifact.total_lines() == 135
        assert artifact.page_count(45) == pytest.approx(3.0)


class Test场景头解析:
    def test_三段解析(self):
        assert heading_parts("内景 - 病房 - 夜") == ("内景", "病房", "夜")
        assert heading_parts("外景 - 天台 - 清晨") == ("外景", "天台", "清晨")

    def test_地点段即机检地点(self, script_artifact):
        for scene in script_artifact.scenes:
            assert heading_parts(scene.heading)[1] == scene.location

    def test_非法形态拒绝(self):
        with pytest.raises(ValidationError, match="heading"):
            heading_parts("病房 - 夜")

    def test_地点不一致工件可构造但地点段与字段不同(self, make_script_artifact):
        """地点不一致是门禁缺陷（rule.scene_character 判 0），不是构造期拒绝。"""
        artifact = make_script_artifact("location_mismatch")
        scene = artifact.scene("scene-2")
        assert scene.location == "走廊"
        assert heading_parts(scene.heading)[1] == "病房"
