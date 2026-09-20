"""剧本 → 分镜 script_segment 导出对接单测（功能 009 / T911，先于实现编写）。

C17 落点：`export_segment(artifact) -> ScriptSegment`（008 侧输入 schema）——导出函数
确定性、**双向 schema 快照断言**（本侧导出字段/枚举 ↔ 008 侧输入字段/枚举，任一漂移
即红）、导出结果能通过 008 `validate_script`（真实剧本产出可替换分镜夹具）。
导出不丢数据：行数、关键行清单、情绪逐条保真（缺陷工件同样不因缺陷丢行）。
"""

from dataclasses import fields

import pytest

from agents.screenplay import artifact as artifact_module
from agents.screenplay.artifact import SCHEMA_VERSION, ScriptArtifact
from agents.screenplay.export_segment import export_segment
from agents.storyboard.script import (
    LINE_KINDS as STORYBOARD_LINE_KINDS,
)
from agents.storyboard.script import (
    SIDES as STORYBOARD_SIDES,
)
from agents.storyboard.script import (
    ScriptLine,
    ScriptScene,
    ScriptSegment,
    validate_script,
)
from core.tree.errors import ValidationError


@pytest.fixture()
def script_stage(make_script_artifact):
    return make_script_artifact(stage="script")


class Test导出结构:
    def test_导出_008_类型(self, script_stage):
        segment = export_segment(script_stage)
        assert isinstance(segment, ScriptSegment)
        assert all(isinstance(scene, ScriptScene) for scene in segment.scenes)
        assert all(isinstance(line, ScriptLine) for scene in segment.scenes for line in scene.lines)

    def test_场景与行序保真(self, script_stage):
        segment = export_segment(script_stage)
        assert segment.scene_ids() == script_stage.scene_ids()
        assert segment.line_ids() == script_stage.line_ids()
        assert [line.line_id for line in segment.scenes[1].lines] == ["s2-l1", "s2-l2", "s2-l3"]

    def test_关键行清单保真(self, script_stage):
        """关键行导出后即 008 必覆盖清单（须逐条被镜头承接）。"""
        segment = export_segment(script_stage)
        assert segment.key_line_ids() == ("s1-l2", "s2-l2", "s3-l3")

    def test_情绪与轴向基准直通(self, script_stage):
        segment = export_segment(script_stage)
        assert segment.emotion_of("s1-l1") == "tense"
        assert segment.emotion_of("s2-l3") is None  # 无标注如实保持缺失（不臆造）
        assert [scene.axis_base for scene in segment.scenes] == ["A", "A", "B"]

    def test_行字段逐条映射(self, script_stage):
        line = export_segment(script_stage).line("s1-l2")
        assert line.kind == "action"
        assert line.key is True
        assert line.text == "陈默伸手去够床头的病历本，输液管绷紧。"
        assert line.emotion == "tense"

    def test_三段工件均可导出(self, make_script_artifact):
        for stage in ("outline", "scenes", "script"):
            segment = export_segment(make_script_artifact(stage=stage))
            assert segment.line_ids() == (
                "s1-l1",
                "s1-l2",
                "s1-l3",
                "s2-l1",
                "s2-l2",
                "s2-l3",
                "s3-l1",
                "s3-l2",
                "s3-l3",
            )

    @pytest.mark.parametrize(
        "variant",
        [
            "missing_beat",
            "page_out_of_range",
            "ghost_character",
            "location_mismatch",
            "ratio_imbalance",
            "entity_variant",
            "timeline_conflict",
        ],
    )
    def test_缺陷工件导出不丢数据(self, make_script_artifact, variant):
        """导出只做 schema 映射：缺陷由评估器判分，导出不因缺陷静默丢行/丢场景。"""
        artifact = make_script_artifact(variant)
        segment = export_segment(artifact)
        assert len(segment.line_ids()) == artifact.total_lines()
        assert len(segment.scene_ids()) == len(artifact.scene_ids())
        assert segment.key_line_ids() == artifact.key_line_ids()

    def test_确定性(self, script_stage):
        first, second = export_segment(script_stage), export_segment(script_stage)
        assert first.to_dict() == second.to_dict()

    def test_非工件输入拒绝(self):
        with pytest.raises(ValidationError, match="ScriptArtifact"):
            export_segment({"scenes": []})


class Test双向schema快照:
    """本侧导出字段/枚举 ↔ 008 侧输入字段/枚举双向锁定（任一漂移即红）。"""

    def test_顶层字段名锁定(self, script_stage):
        assert set(export_segment(script_stage).to_dict()) == {"scenes"}
        assert {field.name for field in fields(ScriptSegment)} == {"scenes"}

    def test_场景字段名锁定(self, script_stage):
        scene = export_segment(script_stage).scenes[0]
        assert set(scene.to_dict()) == {"scene_id", "axis_base", "lines"}
        assert {field.name for field in fields(ScriptScene)} == {"scene_id", "axis_base", "lines"}

    def test_行字段名锁定(self, script_stage):
        line = export_segment(script_stage).scenes[0].lines[0]
        assert set(line.to_dict()) == {"line_id", "kind", "text", "key", "emotion"}
        assert {field.name for field in fields(ScriptLine)} == {
            "line_id",
            "kind",
            "text",
            "key",
            "emotion",
        }

    def test_行类型枚举值与_008_一致(self):
        assert artifact_module.LINE_KINDS == STORYBOARD_LINE_KINDS == ("dialogue", "action")

    def test_轴向基准枚举值与_008_一致(self):
        assert artifact_module.SIDES == STORYBOARD_SIDES == ("A", "B")

    def test_导出枚举取值落在_008_取值域内(self, script_stage):
        segment = export_segment(script_stage)
        kinds = {line.kind for scene in segment.scenes for line in scene.lines}
        sides = {scene.axis_base for scene in segment.scenes if scene.axis_base is not None}
        assert kinds <= set(STORYBOARD_LINE_KINDS)
        assert sides <= set(STORYBOARD_SIDES)

    def test_导出可被_008_反解往返(self, script_stage):
        """双向：008 侧 from_dict 反解本侧导出 → 结构等价（字段名与语义一致）。"""
        segment = export_segment(script_stage)
        restored = ScriptSegment.from_dict(segment.to_dict())
        assert restored.to_dict() == segment.to_dict()
        assert restored.key_line_ids() == segment.key_line_ids()

    def test_工件_schema_版本存在(self, script_stage):
        assert script_stage.schema_version == SCHEMA_VERSION


class Test通过008校验:
    def test_导出结果通过_validate_script(self, script_stage):
        validate_script(export_segment(script_stage))

    def test_带情绪向量表同样通过(self, script_stage, storyboard_config):
        """情绪取值与 008 情绪向量表同域（导出即通过带向量表的预检）。"""
        validate_script(
            export_segment(script_stage), emotion_vectors=storyboard_config.emotion_vectors
        )

    def test_空场景导出后由_008_预检拒绝(self, make_script_artifact):
        """空场景是构造期合法的中间态，导出如实保留 → 008 侧以"剧本不足"执行前拒绝。"""
        payload = make_script_artifact(stage="script").to_dict()
        payload["lines"] = [line for line in payload["lines"] if line["scene_id"] != "scene-2"]
        artifact = ScriptArtifact.from_dict(payload)
        segment = export_segment(artifact)
        empty = next(scene for scene in segment.scenes if scene.scene_id == "scene-2")
        assert empty.lines == ()
        assert segment.line_ids() == artifact.line_ids()  # 其余行不丢
        with pytest.raises(ValidationError, match="剧本不足"):
            validate_script(segment)
