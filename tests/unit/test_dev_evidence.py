"""开发 Agent 升级判据材料单测（功能 017 / T1737，先于实现编写；C16/C17/C18）。

开发 Agent **无 judge 层、无人类锚点**（澄清第 1 条），且 010 按设计把它排除在周校准之外
（`specs/010-weekly-calibration/spec.md:157`）——故判据材料必然含两类**来源缺失**：

- 相关性项：010 排除 ⇒ 无信度数据；
- 漂移项：无 judge 层 ⇒ 无 012 漂移材料。

契约口径：阈值**全量声明**（缺任一项即报错，不许静默"无判据"）+ 逐项"**实测值 / 无法评价
（来源缺失）+ 缺失原因**"（无空白、无省略）；系统结论**恒不为"达标"**（取值域
`below | insufficient`）；系统字段不可改写（`system_digest` 机检）；人工推翻必须留痕且系统
字段逐字节不变；材料为 append-only 不可变快照（同周期已存在即拒绝），落
`calibration/upgrade-events/dev/{period}.json`；继续观察条件 = 待补齐阈值项的量化清单。
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agents.dev.upgrade_evidence import (
    CONCLUSIONS,
    MISSING_SOURCES,
    THRESHOLD_KEYS,
    UpgradeEvidence,
    UpgradeEvidenceError,
    build_upgrade_evidence,
    continuation_conditions,
    gate_violation_rate_of,
    load_evidence,
    override_conclusion,
    proxy_stats_of,
)
from core.degraded.evidence import MEASURED, MISSING_SOURCE
from core.tree.models import NodeStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_PERIOD = "2026-W38"


@pytest.fixture()
def cfg(dev_config):
    return dev_config


def _nodes(make_node, *, scores=None, violation_indexes=()):
    """本周期已评估节点（分量键与 dev 四评估器同构；按索引注入门禁违规）。

    默认 12 个样本 = `dev.upgrade_criteria.min_samples`（形态配置口径）：样本不足会如实
    判 `below`，故"仅来源缺失"的用例必须凑够形态声明的最小样本量。
    """
    if scores is None:
        scores = [round(0.60 + 0.01 * index, 6) for index in range(12)]
    nodes = []
    for index, score in enumerate(scores):
        violated = index in violation_indexes
        # 门禁判 0 短路：代理不跑也不落分量键（真实编排口径——不得按 0 分入代理样本）
        breakdown = {
            "rule.slate_structure@1.0.0": {"score": 0.0 if violated else 1.0},
            "rule.slate_combination@1.0.0": {"score": 0.0 if violated else 1.0},
        }
        if not violated:
            breakdown["proxy.genre_regression@1.0.0"] = {"score": score}
            breakdown["proxy.buzz_heat@1.0.0"] = {"score": score}
        nodes.append(make_node(eval_breakdown=breakdown, score=0.0 if violated else score))
    return nodes


def _build(cfg, *, nodes=(), period=_PERIOD, data_dir, **kwargs):
    return build_upgrade_evidence(period, cfg, nodes=nodes, data_dir=data_dir, **kwargs)


def _system_view(payload: dict) -> str:
    """系统写入部分（排除 overrides）的规范化文本——"逐字节不变"机检口径。"""
    return json.dumps(
        {key: value for key, value in payload.items() if key != "overrides"},
        ensure_ascii=False,
        sort_keys=True,
    )


class Test材料内容:
    def test_材料落盘与字段齐全(self, cfg, make_node, tmp_path):
        """C16 场景 1：字段齐全 + 阈值快照齐全 + 每项均有取值形态。"""
        evidence = _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        assert isinstance(evidence, UpgradeEvidence)
        assert evidence.agent_id == "dev"
        path = tmp_path / "dev" / f"{_PERIOD}.json"
        assert path.is_file()  # 按 agent 分目录（009 同周期材料互不覆盖）
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["period"] == _PERIOD and payload["agent_id"] == "dev"
        assert payload["threshold_snapshot"] == evidence.threshold_snapshot
        assert payload["system_digest"] == evidence.system_digest
        assert payload["overrides"] == []
        assert set(payload["threshold_snapshot"]) >= set(THRESHOLD_KEYS)

    def test_阈值项无空白无省略(self, cfg, make_node, tmp_path):
        """每项必有取值形态：实测值或"无法评价（来源缺失）"+ 原因；阈值项全覆盖。"""
        evidence = _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        items = evidence.items
        assert items
        for item in items:
            assert item["key"]
            assert item["status"] in (MEASURED, MISSING_SOURCE)
            if item["status"] == MISSING_SOURCE:
                assert item["value"] is None
                assert str(item["missing_reason"]).strip()
            else:
                assert item["value"] is not None
                assert item["missing_reason"] == ""
        declared = {item["threshold_key"] for item in items if item["threshold_key"]}
        assert declared == set(THRESHOLD_KEYS)  # 全量声明：无省略阈值项
        assert all(item["threshold_key"] is None or item["key"] in evidence.raw for item in items)

    def test_两类来源缺失逐项登记(self, cfg, make_node, tmp_path):
        """C18：无 judge ⇒ 无 012 漂移材料；010 排除 ⇒ 无信度数据（逐项 + 原因）。"""
        evidence = _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        missing = {item["key"]: item for item in evidence.items if item["status"] == MISSING_SOURCE}
        assert (
            set(missing)
            == {entry["key"] for entry in MISSING_SOURCES}
            == {
                "reliability",
                "drift",
            }
        )
        assert "010-weekly-calibration/spec.md:157" in missing["reliability"]["missing_reason"]
        assert "信度" in missing["reliability"]["missing_reason"]
        assert "judge" in missing["drift"]["missing_reason"]
        assert "012" in missing["drift"]["missing_reason"]
        registry = {entry["key"]: entry for entry in evidence.raw["missing_source_registry"]}
        assert set(registry) == {"reliability", "drift"}
        assert all(entry["source"] and entry["reason"] for entry in registry.values())

    def test_可评价项如实取数(self, cfg, make_node, tmp_path):
        """本环节可评价的三项：门禁违规率 / 代理分量分布 / 本周期已评估样本量。"""
        nodes = _nodes(make_node, scores=(0.6, 0.7, 0.8), violation_indexes=(1,))
        evidence = _build(cfg, nodes=nodes, data_dir=tmp_path)
        measured = {item["key"]: item for item in evidence.items if item["status"] == MEASURED}
        assert evidence.raw["gate_violation_rate"] == pytest.approx(gate_violation_rate_of(nodes))
        assert evidence.raw["gate_violation_rate"] == pytest.approx(1 / 3)
        assert evidence.raw["evaluated_nodes"] == 3
        stats = proxy_stats_of(nodes)
        assert evidence.raw["proxy_components"] == stats["components"]
        assert evidence.raw["proxy_count"] == stats["count"] == 2  # 违规节点代理未跑，不入样本
        assert measured["proxy_distribution"]["value"] == pytest.approx(stats["mean"])
        assert measured["gate_violation_rate"]["value"] == pytest.approx(1 / 3)
        assert measured["evaluated_nodes"]["value"] == 3

    def test_模拟源标注可机读(self, cfg, make_node, tmp_path):
        """SC-009 第三处落点：判据材料同样带模拟数据源来源标记载荷。"""
        evidence = _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        sources = evidence.raw["signal_sources"]
        assert len(sources) == 2
        for source in sources:
            assert source["simulated"] is True
            assert "非真实商业数据" in source["note"]
            assert source["source"].startswith("simulated.")
            assert len(source["params_digest"]) == 12
        assert "非真实商业数据" in json.dumps(evidence.to_dict(), ensure_ascii=False)

    def test_材料不可变_重复生成即拒绝(self, cfg, make_node, tmp_path):
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        with pytest.raises(UpgradeEvidenceError, match="已存在"):
            _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        assert load_evidence(_PERIOD, data_dir=tmp_path)["period"] == _PERIOD

    def test_不同周期各产一条(self, cfg, make_node, tmp_path):
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        other = _build(cfg, nodes=_nodes(make_node), period="2026-W39", data_dir=tmp_path)
        assert other.period == "2026-W39"
        assert sorted(path.name for path in (tmp_path / "dev").glob("*.json")) == [
            "2026-W38.json",
            "2026-W39.json",
        ]

    def test_周期为空即报错(self, cfg, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="period"):
            _build(cfg, period="", data_dir=tmp_path)


class Test系统结论恒不达标:
    """C17：结论取值域不含"达标"；来源缺失即"证据不足"，未过阈即可评价地判"不达标"。"""

    def test_取值域不含达标(self):
        assert CONCLUSIONS == ("below", "insufficient")
        assert "meets" not in CONCLUSIONS and "达标" not in CONCLUSIONS

    def test_来源缺失即证据不足(self, cfg, make_node, tmp_path):
        evidence = _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        assert evidence.conclusion == "insufficient"
        assert any("无法评价" in reason or "来源缺失" in reason for reason in evidence.reasons)
        assert evidence.conclusion not in ("meets", "达标")

    def test_可评价项未过阈判不达标(self, cfg, make_node, tmp_path):
        """门禁违规率超上限 ⇒ 可评价地判 `below`（不达标），理由点名实测与阈值。"""
        nodes = _nodes(make_node, scores=(0.6, 0.7, 0.8), violation_indexes=(0, 1, 2))
        evidence = _build(cfg, nodes=nodes, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("门禁违规率" in reason for reason in evidence.reasons)
        assert any("gate_violation_max" in reason for reason in evidence.reasons)

    def test_样本不足如实标注(self, cfg, make_node, tmp_path):
        evidence = _build(cfg, nodes=_nodes(make_node, scores=(0.6,)), data_dir=tmp_path)
        assert any("min_samples" in reason for reason in evidence.reasons)

    def test_判官回传达标即拒绝(self, cfg, make_node, tmp_path, monkeypatch):
        """取值域外的结论一律拒绝（不得暗示可升级）。"""
        from agents.dev import upgrade_evidence as module

        monkeypatch.setattr(module, "_evaluate", lambda *args, **kwargs: ("meets", [], []))
        with pytest.raises(UpgradeEvidenceError, match="结论"):
            _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        assert list(tmp_path.rglob("*.json")) == []  # 不产材料

    def test_继续观察条件为待补齐阈值项量化清单(self, cfg, make_node, tmp_path):
        """C18：继续观察条件 = 待补齐阈值项的量化清单（键 + 阈值 + 阈值） 。"""
        evidence = _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        pending = continuation_conditions(evidence.threshold_snapshot)
        assert {entry["key"] for entry in pending} == {"reliability", "drift"}
        for entry in pending:
            assert entry["threshold_key"] in THRESHOLD_KEYS
            assert evidence.threshold_snapshot[entry["threshold_key"]] is not None
        text = json.dumps(evidence.to_dict(), ensure_ascii=False)
        for entry in pending:  # 材料本体也带量化清单（不只在返回值里）
            assert entry["key"] in text
            assert f"{entry['threshold_key']}={entry['threshold']}" in text
        assert any("继续观察条件" in alert for alert in evidence.alerts)


class Test阈值缺失即报错:
    @pytest.mark.parametrize("missing", list(THRESHOLD_KEYS))
    def test_缺阈值即报错(self, cfg, make_node, tmp_path, missing):
        """阈值缺失即报错——不允许静默"无判据"（FR-010 / C16）。"""
        criteria = dict(cfg.upgrade_criteria)
        del criteria[missing]
        stale = SimpleNamespace(upgrade_criteria=criteria, signals=dict(cfg.signals))
        with pytest.raises(UpgradeEvidenceError, match=missing):
            _build(stale, nodes=_nodes(make_node), data_dir=tmp_path)
        assert list(tmp_path.rglob("*.json")) == []  # 不产材料

    def test_缺判据段即报错(self, cfg, make_node, tmp_path):
        stale = SimpleNamespace(signals=dict(cfg.signals))
        with pytest.raises(UpgradeEvidenceError, match="upgrade_criteria"):
            _build(stale, nodes=_nodes(make_node), data_dir=tmp_path)


class Test人工推翻留痕:
    """C17 场景 4：人推翻必须留痕，且系统字段逐字节不变。"""

    def test_推翻留痕且系统字段不变(self, cfg, make_node, tmp_path):
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        before = _system_view(load_evidence(_PERIOD, data_dir=tmp_path))
        updated = override_conclusion(
            _PERIOD, by="sunqi", reason="补齐锚点通道后复核", data_dir=tmp_path
        )
        assert updated.conclusion == "insufficient"  # 系统结论不因推翻改写
        assert updated.overrides == [
            {"by": "sunqi", "reason": "补齐锚点通道后复核", "at": updated.overrides[0]["at"]}
        ]
        assert updated.overrides[0]["at"]
        assert _system_view(load_evidence(_PERIOD, data_dir=tmp_path)) == before  # 逐字节不变

    @pytest.mark.parametrize(
        "by, reason",
        [("", "理由"), ("sunqi", ""), ("sunqi", "   ")],
        ids=["无人", "空理由", "空白"],
    )
    def test_推翻必填人与理由(self, cfg, make_node, tmp_path, by, reason):
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        before = load_evidence(_PERIOD, data_dir=tmp_path)
        with pytest.raises(UpgradeEvidenceError):
            override_conclusion(_PERIOD, by=by, reason=reason, data_dir=tmp_path)
        assert load_evidence(_PERIOD, data_dir=tmp_path) == before  # 留痕失败零变更

    def test_改写系统字段被拒(self, cfg, make_node, tmp_path):
        """机检：手工把结论改成"达标"或放宽阈值 → 完整性校验失败（系统字段不可改写）。"""
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        path = tmp_path / "dev" / f"{_PERIOD}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["conclusion"] = "达标"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with pytest.raises(UpgradeEvidenceError, match="完整性"):
            override_conclusion(_PERIOD, by="sunqi", reason="理由", data_dir=tmp_path)

    def test_阈值放宽同样被拒(self, cfg, make_node, tmp_path):
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        path = tmp_path / "dev" / f"{_PERIOD}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["threshold_snapshot"]["gate_violation_max"] = 1.0
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with pytest.raises(UpgradeEvidenceError, match="完整性"):
            override_conclusion(_PERIOD, by="sunqi", reason="理由", data_dir=tmp_path)


class Test原始数值计算面:
    def test_门禁违规率口径(self, make_node):
        """门禁违规率 = 含 `rule.*` 判 0 分量的**已评估**节点占比（FAILED 不入分母）。"""
        nodes = [
            make_node(eval_breakdown={"rule.slate_structure@1.0.0": {"score": 1.0}}),
            make_node(eval_breakdown={"rule.slate_combination@1.0.0": {"score": 0.0}}),
            make_node(eval_breakdown={"proxy.buzz_heat@1.0.0": {"score": 0.4}}),
            make_node(
                eval_breakdown={"rule.slate_structure@1.0.0": {"score": 0.0}},
                status=NodeStatus.FAILED,
                score=None,
            ),
        ]
        assert gate_violation_rate_of(nodes) == pytest.approx(1 / 3)
        assert gate_violation_rate_of([]) == 0.0  # 无违规证据

    def test_代理分量分布口径(self, make_node):
        """代理分布只统计代理分量（门禁分量不入分母），且给出均值/极值/逐分量。"""
        nodes = [
            make_node(
                eval_breakdown={
                    "rule.slate_structure@1.0.0": {"score": 0.0},
                    "proxy.genre_regression@1.0.0": {"score": 0.4},
                    "proxy.buzz_heat@1.0.0": {"score": 0.6},
                },
                score=0.5,
            ),
            make_node(
                eval_breakdown={
                    "proxy.genre_regression@1.0.0": {"score": 0.8},
                    "proxy.buzz_heat@1.0.0": {"score": 0.2},
                },
                score=0.5,
            ),
            make_node(eval_breakdown={}, score=None, status=NodeStatus.FAILED),
        ]
        stats = proxy_stats_of(nodes)
        assert stats["count"] == 2  # FAILED/无分量节点不入样本
        assert stats["mean"] == pytest.approx((0.5 + 0.5) / 2)
        assert stats["min"] == pytest.approx(0.5) and stats["max"] == pytest.approx(0.5)
        assert stats["components"] == {
            "proxy.buzz_heat": pytest.approx(0.4),
            "proxy.genre_regression": pytest.approx(0.6),
        }
        assert proxy_stats_of([]) == {
            "count": 0,
            "mean": 0.0,
            "min": 0.0,
            "max": 0.0,
            "components": {},
        }


class Test阈值快照冻结:
    def test_材料冻结后配置变更不影响已产材料(self, cfg, make_node, tmp_path):
        _build(cfg, nodes=_nodes(make_node), data_dir=tmp_path)
        frozen = copy.deepcopy(load_evidence(_PERIOD, data_dir=tmp_path)["threshold_snapshot"])
        changed = copy.deepcopy(cfg.upgrade_criteria)
        changed["gate_violation_max"] = 0.99
        stale = SimpleNamespace(upgrade_criteria=changed, signals=dict(cfg.signals))
        other = _build(stale, nodes=_nodes(make_node), period="2026-W39", data_dir=tmp_path)
        assert other.threshold_snapshot["gate_violation_max"] == pytest.approx(0.99)
        assert load_evidence(_PERIOD, data_dir=tmp_path)["threshold_snapshot"] == frozen
