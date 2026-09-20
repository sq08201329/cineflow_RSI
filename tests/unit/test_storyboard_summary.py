"""ShotList 结构化文本摘要单测（功能 008 / T813，先于实现编写）。

C8 落点：judge 输入 = ShotList 摘要（+ 剧本段落文本）——确定性结构化文本
（总览行 + 按场景分段 + 逐镜：序号/景别/机位含侧别/运动/时长/备选数/承接行/情绪）；
同 ShotList × 剧本重算逐字节一致；摘要函数哈希稳定（judge 版本号三段之一，
research 决策 6：提示词 + 锚点集 + 摘要函数）。
"""

import blake3
import pytest

from agents.storyboard import summary
from agents.storyboard.summary import summarize_shotlist, summary_function_hash


@pytest.fixture()
def script(make_script_segment):
    return make_script_segment()


@pytest.fixture()
def shotlist(make_shotlist):
    return make_shotlist()


class Test摘要内容:
    def test_总览行(self, shotlist, script):
        text = summarize_shotlist(shotlist, script)
        assert text.splitlines()[0] == (
            "分镜摘要：镜头数 9 | 场景数 3 | 预估时长 10250ms | 关键行 3 条"
        )

    def test_按场景分段(self, shotlist, script):
        text = summarize_shotlist(shotlist, script)
        headers = [ln for ln in text.splitlines() if ln.startswith("[场景")]
        assert headers == [
            "[场景 scene-1] 轴线 A | 剧本行 3 条 | 镜头 3 个",
            "[场景 scene-2] 轴线 A | 剧本行 3 条 | 镜头 3 个",
            "[场景 scene-3] 轴线 B | 剧本行 3 条 | 镜头 3 个",
        ]

    def test_逐镜结构齐全(self, shotlist, script):
        text = summarize_shotlist(shotlist, script)
        lines = text.splitlines()
        shot_lines = [ln for ln in lines if ln.startswith("[") and ln[1:2].isdigit()]
        assert len(shot_lines) == 9
        assert shot_lines[0].startswith("[1] 镜头 shot-01")
        assert shot_lines[-1].startswith("[9] 镜头 shot-09")
        assert shot_lines[0] == (
            "[1] 镜头 shot-01 | 场景 scene-1 | 景别 close_up | 机位 eye_level(侧 A) | "
            "运动 static | 时长 1000ms | 备选 2 | 承接 s1-l1 | 情绪 tense"
        )

    def test_未标注情绪如实标注(self, shotlist, script):
        """情绪缺失如实标注（不臆造）：shot-03 承接的 s1-l3 未标注情绪。"""
        text = summarize_shotlist(shotlist, script)
        shot_03 = [ln for ln in text.splitlines() if ln.startswith("[3]")][0]
        assert shot_03.endswith("情绪 未标注")

    def test_关键行承接行(self, shotlist, script):
        text = summarize_shotlist(shotlist, script)
        assert "关键行承接：s1-l2←shot-02, s2-l1←shot-04, s3-l3←shot-09" in text

    def test_必覆盖清单为空如实降级(self, make_shotlist, make_script_segment):
        """必覆盖清单为空（剧本未标注关键行）→ 摘要如实注明，不伪造关键行要求。"""
        script = make_script_segment(
            scenes=[
                {
                    "scene_id": "scene-1",
                    "axis_base": "A",
                    "lines": [{"line_id": "s1-l1", "kind": "dialogue", "text": "你好。"}],
                }
            ]
        )
        shots = [
            {
                "shot_id": "shot-01",
                "scene_id": "scene-1",
                "covers": ["s1-l1"],
                "shot_size": "medium",
                "camera": "eye_level",
                "side": "A",
                "movement": "static",
                "est_duration_ms": 1000,
                "alternatives": 1,
            }
        ]
        text = summarize_shotlist(make_shotlist(shots=shots), script)
        assert "关键行承接：无（必覆盖清单为空）" in text

    def test_剧本情绪行(self, shotlist, script):
        text = summarize_shotlist(shotlist, script)
        emotion_line = [ln for ln in text.splitlines() if ln.startswith("剧本情绪：")][0]
        assert "s1-l1=tense" in emotion_line
        assert "s1-l3=未标注" in emotion_line
        assert "s3-l3=sorrow" in emotion_line


class Test确定性:
    def test_同输入重算逐字节一致(self, shotlist, script):
        assert summarize_shotlist(shotlist, script) == summarize_shotlist(shotlist, script)

    def test_不同_ShotList_摘要不同(self, make_shotlist, script):
        assert summarize_shotlist(make_shotlist(), script) != summarize_shotlist(
            make_shotlist("size_out_of_range"), script
        )

    def test_无剧本摘要同样确定(self, shotlist):
        """锚点 ShotList 不经剧本校验：无剧本时只输出镜头结构（如实不标注来源）。"""
        first = summarize_shotlist(shotlist)
        assert first == summarize_shotlist(shotlist)
        assert "[场景 scene-1]" in first
        assert "情绪" not in first
        assert "关键行承接" not in first

    def test_锚点_ShotList_可摘要(self, storyboard_config):
        """judge 锚点集经同一摘要函数产出（成对比较的对照面）。"""
        anchor = storyboard_config.anchor_shotlists[0]
        text = summarize_shotlist(anchor)
        assert "shot-anchor-a1" in text
        assert "shot-anchor-a2" in text
        assert "景别 close_up" in text
        assert "备选 3" in text


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
