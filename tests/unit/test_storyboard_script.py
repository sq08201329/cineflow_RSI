"""ScriptSegment 剧本段落模型单测（功能 008 / T805，先于实现编写）。

剧本段落 = 分镜闭环的输入单元（场景列表 + 行 + 关键行标注 + 情绪基调 + 轴向基准）：
- 构造即校验：行 id 全局唯一、场景有序（scene_id 不重复）、关键行标注为布尔、
  情绪为字符串或缺失、轴向基准 ∈ {A, B} 或缺失、行类型 ∈ {dialogue, action}；
- validate_script 配置相关校验：情绪取值必须在情绪向量表内（原则五：配置化口径，
  不允许静默放过未登记情绪）；空剧本/空场景拒绝并注明"剧本不足"（执行前预检）。
"""

import pytest

from agents.storyboard.script import ScriptSegment, validate_script
from core.tree.errors import ValidationError

VECTORS = {
    "calm": [0.30, 0.55, 0.45],
    "tense": [0.75, 0.18, 0.15],
    "joyful": [0.90, 0.78, 0.20],
    "sorrow": [0.15, 0.25, 0.60],
    "awe": [0.55, 0.30, 0.80],
}


class Test剧本结构:
    def test_场景与行结构(self, make_script_segment):
        script = make_script_segment()
        assert script.scene_ids() == ("scene-1", "scene-2", "scene-3")
        assert len(script.line_ids()) == 9
        assert script.line_ids()[0] == "s1-l1"
        assert script.line_ids()[-1] == "s3-l3"
        assert script.line("s2-l1").text == "我不该回来的。"
        assert script.line("s2-l1").kind == "dialogue"

    def test_轴向基准字段(self, make_script_segment):
        """轴向基准（180° 线）逐场景标注：scene-3 切换为 B 侧（场景切换重置轴线）。"""
        script = make_script_segment()
        assert [s.axis_base for s in script.scenes] == ["A", "A", "B"]

    def test_关键行清单(self, make_script_segment):
        """必覆盖清单 = key=True 行（澄清 Q1：普通台词合并/拆分不违规）。"""
        script = make_script_segment()
        assert script.key_line_ids() == ("s1-l2", "s2-l1", "s3-l3")
        assert script.line("s1-l2").key is True
        assert script.line("s1-l1").key is False

    def test_情绪查询(self, make_script_segment):
        script = make_script_segment()
        assert script.emotion_of("s1-l1") == "tense"
        assert script.emotion_of("s1-l3") is None  # 未标注情绪（"不适用"路径）
        assert script.scene_of("s3-l1") == "scene-3"

    def test_查询不存在行即报错(self, make_script_segment):
        with pytest.raises(ValidationError, match="s1-l9"):
            make_script_segment().line("s1-l9")

    def test_字典往返一致(self, make_script_segment):
        script = make_script_segment()
        rebuilt = ScriptSegment.from_dict(script.to_dict())
        assert rebuilt.to_dict() == script.to_dict()
        assert rebuilt.key_line_ids() == script.key_line_ids()


class Test构造形状校验:
    def test_行_id_跨场景重复拒绝(self, make_script_segment):
        with pytest.raises(ValidationError, match="line_id"):
            make_script_segment("duplicate_line")

    def test_场景_id_重复拒绝(self, make_script_segment):
        """场景有序：scene_id 不重复且顺序即剧本顺序（coverage 门禁逐场景对照）。"""
        with pytest.raises(ValidationError, match="scene"):
            make_script_segment("duplicate_scene")

    def test_关键行标注必须是布尔(self, make_script_segment):
        with pytest.raises(ValidationError, match="key"):
            make_script_segment(
                scenes=[
                    {
                        "scene_id": "scene-1",
                        "lines": [
                            {
                                "line_id": "s1-l1",
                                "kind": "dialogue",
                                "text": "你好。",
                                "key": "yes",
                            }
                        ],
                    }
                ]
            )

    def test_行类型非法拒绝(self, make_script_segment):
        with pytest.raises(ValidationError, match="kind"):
            make_script_segment(
                scenes=[
                    {
                        "scene_id": "scene-1",
                        "lines": [{"line_id": "s1-l1", "kind": "voiceover", "text": "你好。"}],
                    }
                ]
            )

    @pytest.mark.parametrize("axis_base", ["C", 1, "a"])
    def test_轴向基准取值非法拒绝(self, make_script_segment, axis_base):
        with pytest.raises(ValidationError, match="axis_base"):
            make_script_segment(
                scenes=[
                    {
                        "scene_id": "scene-1",
                        "axis_base": axis_base,
                        "lines": [{"line_id": "s1-l1", "kind": "dialogue", "text": "你好。"}],
                    }
                ]
            )

    def test_空行文本拒绝(self, make_script_segment):
        with pytest.raises(ValidationError, match="text"):
            make_script_segment(
                scenes=[
                    {
                        "scene_id": "scene-1",
                        "lines": [{"line_id": "s1-l1", "kind": "dialogue", "text": ""}],
                    }
                ]
            )

    def test_场景缺行列表拒绝(self, make_script_segment):
        with pytest.raises(ValidationError, match="lines"):
            make_script_segment(scenes=[{"scene_id": "scene-1"}])


class Test可选校验函数:
    def test_情绪取值必须在向量表内(self, make_script_segment):
        """情绪取值配置化：未登记情绪即报错（不允许静默无对照打分）。"""
        validate_script(make_script_segment(), emotion_vectors=VECTORS)
        with pytest.raises(ValidationError, match="melancholic"):
            validate_script(make_script_segment("bad_emotion"), emotion_vectors=VECTORS)

    def test_空剧本拒绝并注明剧本不足(self, make_script_segment):
        """剧本不足预检（C2 场景 5）：空场景列表 → 执行前拒绝并注明。"""
        with pytest.raises(ValidationError, match="剧本不足"):
            validate_script(make_script_segment("empty_scenes"), emotion_vectors=VECTORS)

    def test_空行剧本拒绝并注明剧本不足(self, make_script_segment):
        with pytest.raises(ValidationError, match="剧本不足"):
            validate_script(
                make_script_segment(scenes=[{"scene_id": "scene-1", "lines": []}]),
                emotion_vectors=VECTORS,
            )

    def test_缺情绪向量表不校验取值(self, make_script_segment):
        """不传向量表时只做形状校验（情绪取值校验是配置相关口径）。"""
        validate_script(make_script_segment("bad_emotion"))
