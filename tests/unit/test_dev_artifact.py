"""立项组合工件 TopicSlate 单测（功能 017 / T1717，先于实现编写）。

契约 C4/C5/C6：schema 版本化（模块常量 + 实例字段，字段/枚举变更即升版）、
`canonical_json()` 确定性与 `slate_hash()` 内容寻址（BLAKE3，运营表幂等键分量 + 回放核对）、
`to_dict↔from_dict` 往返等价、**缺必填结构标记即拒绝**（缺标记 = 门禁与下游无从确定性解析，
不允许静默降级）；"本轮进入生产"标记如实保留（悬空与越界**不由工件代判**，
交 `rule.slate_combination` 判定）；
模拟数据源标注随产物本体落盘（SC-009：不得让模拟信号看起来像真实商业数据）。
"""

import json

import blake3
import pytest

from agents.dev.artifact import (
    SCHEMA_VERSION,
    SIMULATED_NOTE,
    SlateEntry,
    TopicSlate,
    simulated_signal_sources,
)
from core.tree.errors import ValidationError


class Testschema与内容寻址:
    def test_schema版本为模块常量且入实例字段(self, topic_slate):
        assert SCHEMA_VERSION == "1.0.0"
        assert TopicSlate.SCHEMA_VERSION == SCHEMA_VERSION
        assert topic_slate.schema_version == SCHEMA_VERSION

    def test_字段集与数据模型一致(self, topic_slate):
        """schema 快照：顶层与条目字段名锁定（下游 G2 交接按此消费，变更须同时升版）。"""
        assert set(topic_slate.to_dict()) == {
            "schema_version",
            "entries",
            "production_marks",
            "signal_sources",
        }
        assert set(topic_slate.entries[0].to_dict()) == {
            "direction_id",
            "rationale",
            "eval_components",
            "genre",
            "constraints",
            "characters",
            "in_production",
        }

    def test_canonical_json与哈希逐位可复现(self, topic_slate):
        assert topic_slate.canonical_json() == topic_slate.canonical_json()
        assert topic_slate.canonical_json() == json.dumps(
            topic_slate.to_dict(), sort_keys=True, ensure_ascii=False
        )
        digest = topic_slate.slate_hash()
        assert digest == blake3.blake3(topic_slate.canonical_json().encode()).hexdigest()
        assert len(digest) == 64 and digest == digest.lower()

    def test_内容变化即哈希变化(self, make_topic_slate):
        base = make_topic_slate()
        assert make_topic_slate("dangling_mark").slate_hash() != base.slate_hash()
        assert make_topic_slate("duplicate_direction").slate_hash() != base.slate_hash()

    def test_往返等价(self, topic_slate):
        restored = TopicSlate.from_dict(topic_slate.to_dict())
        assert restored == topic_slate
        assert restored.canonical_json() == topic_slate.canonical_json()
        assert restored.slate_hash() == topic_slate.slate_hash()
        assert isinstance(restored.entries[0], SlateEntry)


class Test构造期拒绝:
    def test_空组合即拒绝(self, make_topic_slate):
        """条目列表非空是必填结构标记（C4）：空组合在工件层即拒绝，越界不由代码兜底。"""
        with pytest.raises(ValidationError, match="entries"):
            TopicSlate.from_dict(make_topic_slate("empty_slate"))

    def test_非_mapping_输入拒绝(self):
        with pytest.raises(ValidationError, match="dict"):
            TopicSlate.from_dict(["not-a-mapping"])

    @pytest.mark.parametrize("field", ["direction_id", "rationale"])
    @pytest.mark.parametrize("value", ["", None])
    def test_缺方向标识或缺论证要点即拒绝(self, make_topic_slate, field, value):
        payload = make_topic_slate().to_dict()
        payload["entries"][0][field] = value
        with pytest.raises(ValidationError):
            TopicSlate.from_dict(payload)

    def test_缺标记来源即拒绝(self, make_topic_slate):
        """signal_sources 为必填标记（SC-009 标注不可省）：缺失即拒绝，不静默降级。"""
        for missing in (None, [], {}):
            payload = make_topic_slate().to_dict()
            payload["signal_sources"] = missing
            with pytest.raises(ValidationError):
                TopicSlate.from_dict(payload)

    def test_条目形状非法即拒绝(self, make_topic_slate):
        for field, value in (
            ("eval_components", []),
            ("constraints", "单场景为主"),
            ("characters", [""]),
            ("in_production", "yes"),
        ):
            payload = make_topic_slate().to_dict()
            payload["entries"][0][field] = value
            with pytest.raises(ValidationError):
                TopicSlate.from_dict(payload)

    def test_要点为空可构造并由门禁判定(self, make_topic_slate):
        """ "字段在但取值为空"与"字段缺失"不同：前者可构造、由门禁点名（C5），
        故门禁仍能对缺陷工件判 0 —— 构造期只拒绝**缺结构标记**，不代判要点齐备性。"""
        slate = make_topic_slate("missing_essentials")
        deficient = slate.entry("dir-awakening")
        assert deficient.genre == ""
        assert deficient.constraints == ()
        assert deficient.characters == ()


class Test本轮进入生产标记:
    def test_标记原样保留不代判(self, make_topic_slate):
        """悬空标记如实落盘（不由工件兜底）：指向合法性由 rule.slate_combination 判 0 并点名。"""
        slate = make_topic_slate("dangling_mark")
        assert slate.produce_ids() == ("dir-not-in-slate",)
        assert slate.marked_entries() == ()

    def test_条目标记位与组合级指向一致(self):
        """两处呈现永不漂移：构造期按组合级指向归一化条目级 in_production 位。"""
        slate = TopicSlate.from_dict(
            {
                "entries": [
                    {
                        "direction_id": "dir-a",
                        "rationale": "论点 A",
                        "eval_components": {},
                        "genre": "悬疑",
                        "constraints": ["单场景"],
                        "characters": ["林静"],
                        "in_production": True,
                    },
                    {
                        "direction_id": "dir-b",
                        "rationale": "论点 B",
                        "eval_components": {},
                        "genre": "都市",
                        "constraints": [],
                        "characters": [],
                    },
                ],
                "production_marks": ["dir-b"],
                "signal_sources": list(simulated_signal_sources({"fixture": True})),
            }
        )
        assert [entry.in_production for entry in slate.entries] == [False, True]
        assert slate.produce_ids() == ("dir-b",)
        assert [entry.direction_id for entry in slate.marked_entries()] == ["dir-b"]

    def test_标记缺省时由条目标记位派生(self, topic_slate):
        payload = topic_slate.to_dict()
        payload.pop("production_marks")
        slate = TopicSlate.from_dict(payload)
        assert slate.produce_ids() == topic_slate.produce_ids()
        assert slate.slate_hash() == topic_slate.slate_hash()

    def test_标记列表缺省为空(self, topic_slate):
        """0 标记是合法状态（"组合内无达标条目"如实产出）——区间下界冲突由门禁按配置判定。"""
        payload = topic_slate.to_dict()
        payload["production_marks"] = []
        payload["entries"] = [{**entry, "in_production": False} for entry in payload["entries"]]
        assert TopicSlate.from_dict(payload).produce_ids() == ()

    def test_方向标识与条目查询(self, topic_slate):
        assert topic_slate.direction_ids() == (
            "dir-night-ward",
            "dir-city-heist",
            "dir-awakening",
            "dir-return",
        )
        assert topic_slate.entry("dir-return").genre == "家庭剧情"
        with pytest.raises(ValidationError, match="dir-ghost"):
            topic_slate.entry("dir-ghost")


class Test模拟数据源标注:
    def test_标注随产物落盘且可机读(self, topic_slate):
        """SC-009：数据来源与"非真实商业数据"随产物本体落盘（不只落报告侧）。"""
        sources = topic_slate.to_dict()["signal_sources"]
        assert len(sources) == 2
        for source in sources:
            assert source["source"]
            assert source["simulated"] is True
            assert "非真实商业数据" in source["note"]
        assert SIMULATED_NOTE in {source["note"] for source in sources}

    def test_标注缺失或声称真实即拒绝(self, topic_slate):
        payload = topic_slate.to_dict()
        payload["signal_sources"] = [
            {"source": "box_office_db", "simulated": False, "note": "真实"}
        ]
        with pytest.raises(ValidationError):
            TopicSlate.from_dict(payload)
        payload["signal_sources"] = [{"source": "simulated.x", "simulated": True, "note": "热度"}]
        with pytest.raises(ValidationError):
            TopicSlate.from_dict(payload)
        payload["signal_sources"] = [{"simulated": True, "note": SIMULATED_NOTE}]
        with pytest.raises(ValidationError):
            TopicSlate.from_dict(payload)

    def test_标注携带参数摘要(self):
        """模拟源参数即行为口径（原则一）：参数变更 ⇒ 标注摘要变更（改参数即新版本）。"""
        first = simulated_signal_sources({"baseline_usd_million": 40.0, "sensitivity": 1.0})
        second = simulated_signal_sources({"baseline_usd_million": 40.0, "sensitivity": 1.0})
        third = simulated_signal_sources({"baseline_usd_million": 41.0, "sensitivity": 1.0})
        assert first == second
        assert [dict(source) for source in first] != [dict(source) for source in third]
        assert len({source["source"] for source in first}) == 2
        assert all(len(source["params_digest"]) == 12 for source in first)
