"""EDL 结构化文本摘要单测（功能 007 / T713，先于实现编写）。

澄清 Q1 落点：judge 输入 = EDL 摘要——确定性结构化文本（逐镜：序号/时长/转场/
分区标注/音轨标记）；同 EDL 重算逐字节一致；摘要函数哈希稳定（judge 版本号
三段之一，决策 5：提示词 + 锚点集 + 摘要函数）。
"""

import blake3
import pytest

from agents.editing import summary
from agents.editing.summary import summarize_edl, summary_function_hash


@pytest.fixture()
def library(make_shot_library):
    return make_shot_library()


@pytest.fixture()
def structure(make_scene_structure, library):
    return make_scene_structure(library=library)


class Test摘要内容:
    def test_逐镜结构齐全(self, make_edl, library, structure):
        """逐镜：序号/时长/转场/分区；音轨标记随行。"""
        text = summarize_edl(make_edl(), library, structure)
        lines = text.splitlines()
        # 逐镜行：5 个 clip 各一行，序号从 1 递增
        shot_lines = [ln for ln in lines if ln.startswith("[")]
        assert len(shot_lines) == 5
        assert shot_lines[0].startswith("[1]")
        assert shot_lines[4].startswith("[5]")
        # 时长/转场/分区标注
        assert "时长 3000ms" in shot_lines[0]  # shot-1 [500, 3500)
        assert "dissolve(750ms)" in shot_lines[0]
        assert "scene-a" in shot_lines[0]
        assert "cut" in shot_lines[1]
        assert "scene-c" in shot_lines[4]
        # 音轨标记
        assert "bgm-01" in text
        assert "0ms" in text

    def test_无音轨标记(self, make_edl, library, structure):
        text = summarize_edl(make_edl(audio=[]), library, structure)
        assert "无音轨" in text

    def test_总时长行(self, make_edl, library, structure):
        edl = make_edl()
        text = summarize_edl(edl, library, structure)
        assert f"总时长 {edl.total_duration_ms()}ms" in text


class Test确定性:
    def test_同_EDL_重算逐字节一致(self, make_edl, library, structure):
        edl = make_edl()
        assert summarize_edl(edl, library, structure) == summarize_edl(edl, library, structure)

    def test_不同_EDL_摘要不同(self, make_edl, library, structure):
        assert summarize_edl(make_edl(), library, structure) != summarize_edl(
            make_edl("scene_disorder"), library, structure
        )

    def test_锚点_EDL_可摘要(self, editing_config, library, structure):
        """judge 锚点集经同一摘要函数产出（决策 5）：库外镜头以裸 shot_id 标注。"""
        anchor = editing_config.anchor_edls[0]
        text = summarize_edl(anchor, library, structure)
        assert "anchor-a1" in text
        assert "anchor-music" not in text  # 第一个锚点无音轨


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
