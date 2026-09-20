"""TimingSheet 时序元数据测试（功能 006 / T605，先于实现编写）。

澄清 Q1 落点：声音 Agent 的同步输入只有时序元数据（台词起止 + 音效事件时间），
不碰视觉工件；上游输入错误（重叠/越界/start≥end）必须执行前拒绝（C1 场景 5）。
"""

import pytest

from agents.sound.timing import Effect, TimingSheet, Utterance
from core.tree.errors import ValidationError


class Test合法构造:
    def test_字典输入归一化为强类型(self, make_timing_sheet):
        sheet = make_timing_sheet()
        assert all(isinstance(u, Utterance) for u in sheet.utterances)
        assert all(isinstance(e, Effect) for e in sheet.effects)
        assert sheet.utterances[0].text == "你终于来了。"
        assert sheet.utterances[0].start_ms == 0
        assert sheet.utterances[0].end_ms == 1000
        assert sheet.effects[0].kind == "door_slam"
        assert sheet.effects[0].at_ms == 1100

    def test_强类型输入直接构造(self):
        sheet = TimingSheet(
            utterances=[Utterance(text="台词", start_ms=0, end_ms=500)],
            effects=[Effect(kind="rain", at_ms=250)],
        )
        assert len(sheet.utterances) == 1
        assert len(sheet.effects) == 1

    def test_空清单合法(self):
        sheet = TimingSheet(utterances=[], effects=[])
        assert sheet.utterances == []
        assert sheet.effects == []

    def test_相邻贴边不算重叠(self, make_timing_sheet):
        """区间语义 [start_ms, end_ms)：前条 end == 后条 start 为合法贴边。"""
        sheet = make_timing_sheet(
            utterances=[
                {"text": "甲", "start_ms": 0, "end_ms": 1000},
                {"text": "乙", "start_ms": 1000, "end_ms": 2000},
            ]
        )
        assert len(sheet.utterances) == 2

    def test_乱序输入按开始时间排序(self, make_timing_sheet):
        sheet = make_timing_sheet(
            utterances=[
                {"text": "乙", "start_ms": 1200, "end_ms": 2000},
                {"text": "甲", "start_ms": 0, "end_ms": 1000},
            ]
        )
        assert [u.text for u in sheet.utterances] == ["甲", "乙"]


class Test重叠拒绝:
    def test_台词区间重叠被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="重叠"):
            make_timing_sheet(variant="overlap")

    def test_完全包含也算重叠(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="重叠"):
            make_timing_sheet(
                utterances=[
                    {"text": "甲", "start_ms": 0, "end_ms": 3000},
                    {"text": "乙", "start_ms": 1000, "end_ms": 2000},
                ]
            )


class Test越界拒绝:
    def test_音效事件负时间戳被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="非负"):
            make_timing_sheet(variant="out_of_bounds")

    def test_台词负时间戳被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="非负"):
            make_timing_sheet(
                utterances=[{"text": "甲", "start_ms": -1, "end_ms": 1000}]
            )


class Test起止关系:
    def test_start_等于_end_被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="start_ms.*<.*end_ms"):
            make_timing_sheet(
                utterances=[{"text": "甲", "start_ms": 1000, "end_ms": 1000}]
            )

    def test_start_大于_end_被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="start_ms.*<.*end_ms"):
            make_timing_sheet(
                utterances=[{"text": "甲", "start_ms": 2000, "end_ms": 1000}]
            )


class Test字段合法性:
    def test_空台词文本被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="text"):
            make_timing_sheet(
                utterances=[{"text": "", "start_ms": 0, "end_ms": 1000}]
            )

    def test_空音效类型被拒(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="kind"):
            make_timing_sheet(effects=[{"kind": "", "at_ms": 100}])

    def test_时间戳必须为整数(self, make_timing_sheet):
        with pytest.raises(ValidationError, match="整数"):
            make_timing_sheet(
                utterances=[{"text": "甲", "start_ms": 0.5, "end_ms": 1000}]
            )
