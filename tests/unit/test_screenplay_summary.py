"""大纲结构化摘要单测（功能 009 / T909，先于实现编写）。

C10 落点：judge 输入 = **大纲结构化摘要**（summary.py 确定性产出）——总览行 + 节拍清单
+ 按场景分段（场景头/时序/出场角色/行数）+ 逐行（类型/归属角色/文本/情绪/关键行标记）
+ 角色表（含别名）。摘要仅接受 outline 阶段工件（judge 仅作用于大纲阶段，research
决策 2；scenes/script 阶段"不适用"由合成层跳过，不伪造 0 分）。

同输入重算逐字节一致；摘要函数哈希 = 实现文件 BLAKE3 前 8 位（judge 版本号三段之一，
原则一：摘要函数变更即打分行为变更）。
"""

import blake3
import pytest

from agents.screenplay import summary
from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.summary import summarize_outline, summary_function_hash
from core.tree.errors import ValidationError


@pytest.fixture()
def outline(script_artifact):
    return script_artifact


class Test摘要内容:
    def test_总览行(self, outline):
        assert summarize_outline(outline).splitlines()[0] == (
            "大纲摘要：节拍 8 个（关键 8） | 场景 3 个 | 行 9 条（对白 6 / 动作 3） | 角色 3 个"
        )

    def test_节拍清单逐条(self, outline):
        text = summarize_outline(outline)
        beats = [line for line in text.splitlines() if line.startswith("[节拍]")]
        assert len(beats) == 8
        assert beats[0] == "[节拍] opening_image（act1，必需）节拍 opening_image 的结构要求（夹具）"
        assert beats[-1].startswith("[节拍] resolution（act3，必需）")

    def test_节拍可选与必需如实标注(self, make_script_artifact):
        """可选节拍标注"可选"（不臆造存在性要求）。"""
        payload = make_script_artifact().to_dict()
        payload["beats"].append(
            {"beat_id": "theme_stated", "act": "act1", "required": False, "description": "主题陈述"}
        )
        text = summarize_outline(ScriptArtifact.from_dict(payload))
        assert "[节拍] theme_stated（act1，可选）主题陈述" in text

    def test_按场景分段(self, outline):
        headers = [
            line for line in summarize_outline(outline).splitlines() if line.startswith("[场景")
        ]
        assert headers == [
            "[场景 scene-1] 内景 - 病房 - 夜 | 时序 0 分钟 | 出场 林静,陈默,周医生 | 行 3 条",
            "[场景 scene-2] 内景 - 走廊 - 夜 | 时序 30 分钟 | 出场 林静,周医生 | 行 3 条",
            "[场景 scene-3] 外景 - 天台 - 清晨 | 时序 75 分钟 | 出场 陈默,林静 | 行 3 条",
        ]

    def test_逐行结构齐全(self, outline):
        lines = [line for line in summarize_outline(outline).splitlines() if line.startswith("[行")]
        assert len(lines) == 9
        assert lines[0] == "[行 s1-l1] 对白 林静：你醒了。 | 情绪 tense"
        assert lines[1] == (
            "[行 s1-l2] 动作 陈默：陈默伸手去够床头的病历本，输液管绷紧。 | 情绪 tense | 关键"
        )

    def test_未标注如实标注(self, outline):
        """归属角色缺失与情绪缺失一律"未标注"（不臆造说话人与情绪）。"""
        text = summarize_outline(outline)
        line_s2_l2 = next(line for line in text.splitlines() if line.startswith("[行 s2-l2]"))
        line_s2_l3 = next(line for line in text.splitlines() if line.startswith("[行 s2-l3]"))
        assert line_s2_l2 == (
            "[行 s2-l2] 动作 未标注：走廊尽头的灯一盏一盏亮起来。 | 情绪 tense | 关键"
        )
        assert line_s2_l3 == "[行 s2-l3] 对白 周医生：三个月。他让我们都不要说。 | 情绪 未标注"

    def test_角色表行(self, outline):
        roles = [
            line for line in summarize_outline(outline).splitlines() if line.startswith("[角色]")
        ]
        assert roles == ["[角色] 林静（别名 阿静） | 陈默（别名 默哥） | 周医生（无别名）"]

    def test_大纲正文逐行前置标记(self, outline):
        """正文是判"戏剧张力"的实质内容（结构标记之外不得省略）。"""
        body = [
            line for line in summarize_outline(outline).splitlines() if line.startswith("[正文]")
        ]
        assert len(body) == 1
        assert body[0] == (
            "[正文] （outline 阶段文本）病房的夜与天台的清晨之间，"
            "陈默藏了三个月的手术通知，林静必须决定要不要拆穿。"
        )

    def test_锚点大纲可摘要(self, screenplay_config):
        """judge 锚点大纲经同一摘要函数产出（成对比较的对照面口径一致）。"""
        text = summarize_outline(screenplay_config.anchor_outlines[0])
        assert "anchor-a1" in text
        assert "anchor-a2" in text
        assert "[场景 anchor-a1] 内景 - 手术室 - 夜" in text
        assert "[行 a1-l2] 动作 陈默" in text


class Test确定性:
    def test_同输入重算逐字节一致(self, outline):
        assert summarize_outline(outline) == summarize_outline(outline)

    def test_不同工件摘要不同(self, make_script_artifact):
        valid = summarize_outline(make_script_artifact())
        assert summarize_outline(make_script_artifact("ghost_character")) != valid
        assert summarize_outline(make_script_artifact("timeline_conflict")) != valid

    def test_文本变更即摘要变更(self, make_script_artifact):
        assert summarize_outline(make_script_artifact(text="另一版大纲")) != summarize_outline(
            make_script_artifact()
        )


class Test阶段适用范围:
    """judge 仅作用于大纲阶段：摘要函数只接受 outline 工件（不适用不伪造）。"""

    @pytest.mark.parametrize("stage", ["scenes", "script"])
    def test_非大纲阶段拒绝(self, make_script_artifact, stage):
        with pytest.raises(ValidationError, match="outline"):
            summarize_outline(make_script_artifact(stage=stage))

    def test_非工件输入拒绝(self):
        with pytest.raises(ValidationError, match="ScriptArtifact"):
            summarize_outline({"stage": "outline"})


class Test摘要函数哈希:
    def test_哈希形态与稳定(self):
        digest = summary_function_hash()
        assert len(digest) == 8
        assert digest == digest.lower()
        assert digest == summary_function_hash()  # 重算稳定

    def test_哈希源自实现文件(self):
        """摘要函数哈希 = 实现文件 BLAKE3 前 8 位（变更即 judge 版本变更，原则一）。"""
        from pathlib import Path

        expected = blake3.blake3(Path(summary.__file__).read_bytes()).hexdigest()[:8]
        assert summary_function_hash() == expected
