"""通用件判据材料单测（功能 017 / T1711，先于实现编写；契约 C16/C17）。

`core/degraded/evidence.py` = 009 `upgrade_evidence.py` 的机制抽出，落到**判据项**抽象：

- 判据项 = 键 + 阈值键 + 提供者可调用；逐项取值形态 = **实测值** 或
  **`无法评价（来源缺失）` + 缺失原因**——**无空白、无省略**（漏配任一阈值项即产出报错）；
- 材料 = 全量阈值快照 + 逐项取值 + 系统结论 + 推翻留痕，落
  `{data_dir}/{agent_id}/{period}.json`（按 agent 分目录：同周期双写不互相覆盖）；
- **系统字段不可改写**：写 `达标`（或结论取值域外的值）被拒；手工改写系统字段即机检失败
  （`system_digest` 内容哈希）；人工推翻**只追加**记录，系统字段逐字节不变；
- 材料为 **append-only 不可变快照**：同周期已存在即拒绝重产。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.degraded.evidence import (
    CONCLUSIONS,
    MEASURED,
    MISSING_SOURCE,
    ItemValue,
    UpgradeEvidence,
    UpgradeEvidenceError,
    build_upgrade_evidence,
    load_evidence,
    measured,
    override_conclusion,
    unavailable,
)

_PERIOD = "2026-W38"
_AGENT_ID = "screenplay"
_THRESHOLDS = {"correlation_target": 0.6, "min_samples": 5, "gate_violation_max": 0.2}


def _cfg(**overrides):
    fields = {"upgrade_criteria": dict(_THRESHOLDS)}
    fields.update(overrides)
    return SimpleNamespace(**fields)


_DIRECTIONS = {"correlation_target": ">=", "min_samples": ">=", "gate_violation_max": "<="}


def _judge(snapshot: dict, raw: dict, items) -> tuple[str, list[str], list[str]]:
    """判定（业务侧注入口径）：存在无法评价项 ⇒ insufficient；越阈 ⇒ below；齐达 ⇒ meets。"""
    missing = [item.key for item in items if item.status == MISSING_SOURCE]
    if missing:
        return "insufficient", [f"{MISSING_SOURCE}：{', '.join(missing)}"], []
    reasons = [
        f"{item.key} 越阈（{raw[item.key]} 不满足 "
        f"{item.threshold_key}={snapshot[item.threshold_key]}）"
        for item in items
        if item.threshold_key and not _within(raw[item.key], snapshot[item.threshold_key], item)
    ]
    return ("below" if reasons else "meets"), reasons, []


def _within(value, limit, item) -> bool:
    if _DIRECTIONS[item.threshold_key] == ">=":
        return value >= limit
    return value <= limit


def _items(*, correlation=0.72, samples=8, violations=None, note="kendall_tau"):
    return (
        {
            "key": "correlation",
            "threshold_key": "correlation_target",
            "provider": lambda: (
                measured(correlation, metric=note) if correlation is not None else unavailable(note)
            ),
        },
        {
            "key": "samples",
            "threshold_key": "min_samples",
            "provider": lambda: measured(samples),
        },
        {
            "key": "violation_rate",
            "threshold_key": "gate_violation_max",
            "provider": lambda: measured(0.0 if violations is None else violations),
        },
        # 无阈值的观测项（不进阈值快照，仍必须有取值形态）
        {
            "key": "anchor_count",
            "threshold_key": None,
            "provider": lambda: measured(5),
        },
    )


def _build(data_dir, *, cfg=None, items=None, judge=_judge, period=_PERIOD, **kwargs):
    return build_upgrade_evidence(
        period,
        cfg or _cfg(),
        _items() if items is None else items,
        judge=judge,
        agent_id=kwargs.pop("agent_id", _AGENT_ID),
        data_dir=data_dir,
        **kwargs,
    )


def _material_path(data_dir, *, period: str = _PERIOD, agent_id: str = _AGENT_ID) -> Path:
    return Path(data_dir) / agent_id / f"{period}.json"


def _system_view(payload: dict) -> str:
    """系统写入部分（排除 overrides）的规范化文本——"逐字节不变"机检口径。"""
    return json.dumps(
        {key: value for key, value in payload.items() if key != "overrides"},
        ensure_ascii=False,
        sort_keys=True,
    )


class Test逐项可评价性:
    def test_各项取值形态齐全(self, tmp_path):
        """FR-010：每项输出实测值或无法评价（来源缺失）+ 缺失原因，无空白无省略。"""
        evidence = _build(tmp_path, items=_items(correlation=None, note="台账无本周期记录"))
        assert isinstance(evidence, UpgradeEvidence)
        assert [item["key"] for item in evidence.items] == [
            "correlation",
            "samples",
            "violation_rate",
            "anchor_count",
        ]
        statuses = {item["key"]: item["status"] for item in evidence.items}
        assert statuses == {
            "correlation": MISSING_SOURCE,
            "samples": MEASURED,
            "violation_rate": MEASURED,
            "anchor_count": MEASURED,
        }
        missing = next(item for item in evidence.items if item["key"] == "correlation")
        assert missing["missing_reason"] == "台账无本周期记录"
        assert missing["value"] is None
        payload = load_evidence(_PERIOD, agent_id=_AGENT_ID, data_dir=tmp_path)
        assert payload["items"] == evidence.items
        assert payload["raw"]["correlation"] is None

    def test_实测值附带口径并入_raw(self, tmp_path):
        evidence = _build(tmp_path)
        assert evidence.raw["correlation"] == pytest.approx(0.72)
        assert evidence.raw["metric"] == "kendall_tau"  # extra 口径字段随实测值入 raw
        assert evidence.raw["samples"] == 8
        assert evidence.raw["anchor_count"] == 5
        assert evidence.threshold_snapshot == _THRESHOLDS
        assert evidence.conclusion == "meets"
        assert evidence.reasons == []

    def test_每项必有取值形态(self, tmp_path):
        """无空白：提供者返回不可评价值却未登记原因 ⇒ 拒绝出材料。"""
        items = (
            {
                "key": "correlation",
                "threshold_key": "correlation_target",
                "provider": lambda: unavailable(""),
            },
        )
        with pytest.raises(UpgradeEvidenceError, match="缺失原因"):
            _build(tmp_path, items=items)
        assert not _material_path(tmp_path).exists()

    def test_实测项不得同时声明缺失原因(self, tmp_path):
        items = (
            {
                "key": "correlation",
                "threshold_key": "correlation_target",
                "provider": lambda: ItemValue(value=0.7, missing_reason="矛盾声明"),
            },
        )
        with pytest.raises(UpgradeEvidenceError, match="不得同时"):
            _build(tmp_path, items=items)

    def test_判据项键重复即报错(self, tmp_path):
        item = {
            "key": "correlation",
            "threshold_key": "correlation_target",
            "provider": lambda: measured(0.7),
        }
        with pytest.raises(UpgradeEvidenceError, match="重复"):
            _build(tmp_path, items=(item, dict(item)))

    def test_判据项为空即报错(self, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="判据项"):
            _build(tmp_path, items=())

    def test_提供者缺失即报错(self, tmp_path):
        items = ({"key": "correlation", "threshold_key": "correlation_target"},)
        with pytest.raises(UpgradeEvidenceError, match="provider"):
            _build(tmp_path, items=items)

    def test_提供者返回形态非法即报错(self, tmp_path):
        items = (
            {
                "key": "correlation",
                "threshold_key": "correlation_target",
                "provider": lambda: 0.7,
            },
        )
        with pytest.raises(UpgradeEvidenceError, match="ItemValue"):
            _build(tmp_path, items=items)


class Test阈值全量声明:
    @pytest.mark.parametrize("missing", ["correlation_target", "min_samples", "gate_violation_max"])
    def test_阈值缺失即报错不产材料(self, tmp_path, missing):
        criteria = dict(_THRESHOLDS)
        del criteria[missing]
        with pytest.raises(UpgradeEvidenceError, match=missing):
            _build(tmp_path, cfg=_cfg(upgrade_criteria=criteria))
        assert list(tmp_path.rglob("*.json")) == []

    def test_无判据配置即报错(self, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="upgrade_criteria"):
            _build(tmp_path, cfg=SimpleNamespace())

    def test_外部快照须覆盖全部阈值键(self, tmp_path):
        """快照由调用方给定时同样全量校验（不得静默漏项）。"""
        items = _items()
        with pytest.raises(UpgradeEvidenceError, match="gate_violation_max"):
            build_upgrade_evidence(
                _PERIOD,
                _cfg(),
                items,
                judge=_judge,
                agent_id=_AGENT_ID,
                data_dir=tmp_path,
                snapshot={"correlation_target": 0.6, "min_samples": 5},
            )

    def test_外部快照原样冻结(self, tmp_path):
        snapshot = {**_THRESHOLDS, "reliability_target": 0.55}
        evidence = build_upgrade_evidence(
            _PERIOD,
            _cfg(),
            _items(),
            judge=_judge,
            agent_id=_AGENT_ID,
            data_dir=tmp_path,
            snapshot=snapshot,
        )
        assert evidence.threshold_snapshot == snapshot

    def test_周期为空即报错(self, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="period"):
            _build(tmp_path, period="")


class Test系统结论不可改写:
    def test_结论取值域外的值被拒(self, tmp_path):
        """写 `达标` 被拒：结论必须落在声明的取值域内（不得暗示可升级）。"""
        with pytest.raises(UpgradeEvidenceError, match="结论"):
            _build(tmp_path, judge=lambda snapshot, raw, items: ("达标", [], []))
        with pytest.raises(UpgradeEvidenceError, match="结论"):
            _build(
                tmp_path,
                judge=lambda snapshot, raw, items: ("meets", [], []),
                conclusions=("below", "insufficient"),
            )
        assert list(tmp_path.rglob("*.json")) == []

    def test_取值域可收窄且默认含三态(self):
        assert CONCLUSIONS == ("meets", "below", "insufficient")

    def test_推翻留痕且系统字段逐字节不变(self, tmp_path):
        _build(tmp_path, items=_items(correlation=0.2))
        before = _system_view(load_evidence(_PERIOD, agent_id=_AGENT_ID, data_dir=tmp_path))
        updated = override_conclusion(
            _PERIOD,
            by="sunqi",
            reason="已补齐锚点，下周期复核",
            agent_id=_AGENT_ID,
            data_dir=tmp_path,
        )
        assert updated.overrides == [
            {
                "by": "sunqi",
                "reason": "已补齐锚点，下周期复核",
                "at": updated.overrides[0]["at"],
            }
        ]
        after = load_evidence(_PERIOD, agent_id=_AGENT_ID, data_dir=tmp_path)
        assert _system_view(after) == before  # 系统字段（含结论与阈值快照）逐字节不变
        assert after["conclusion"] == "below"

    def test_推翻二次追加不覆盖(self, tmp_path):
        _build(tmp_path)
        system_before = _system_view(load_evidence(_PERIOD, agent_id=_AGENT_ID, data_dir=tmp_path))
        override_conclusion(
            _PERIOD, by="sunqi", reason="第一次", agent_id=_AGENT_ID, data_dir=tmp_path
        )
        second = override_conclusion(
            _PERIOD, by="reviewer-2", reason="第二次", agent_id=_AGENT_ID, data_dir=tmp_path
        )
        assert [record["by"] for record in second.overrides] == ["sunqi", "reviewer-2"]
        assert (
            _system_view(load_evidence(_PERIOD, agent_id=_AGENT_ID, data_dir=tmp_path))
            == system_before
        )

    @pytest.mark.parametrize(
        "by, reason",
        [("", "理由"), ("sunqi", ""), ("sunqi", "   ")],
        ids=["无人", "空理由", "仅空白"],
    )
    def test_推翻必填人与理由(self, tmp_path, by, reason):
        _build(tmp_path)
        before = _material_path(tmp_path).read_bytes()
        with pytest.raises(UpgradeEvidenceError):
            override_conclusion(
                _PERIOD, by=by, reason=reason, agent_id=_AGENT_ID, data_dir=tmp_path
            )
        assert _material_path(tmp_path).read_bytes() == before  # 留痕失败零变更

    def test_篡改系统字段后被拒(self, tmp_path):
        _build(tmp_path, items=_items(correlation=0.2))
        path = _material_path(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["conclusion"] = "达标"  # 手工改写系统字段
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with pytest.raises(UpgradeEvidenceError, match="完整性"):
            override_conclusion(
                _PERIOD, by="sunqi", reason="理由", agent_id=_AGENT_ID, data_dir=tmp_path
            )

    def test_篡改阈值快照后被拒(self, tmp_path):
        _build(tmp_path)
        path = _material_path(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["threshold_snapshot"]["correlation_target"] = 0.01  # 手工放宽阈值
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with pytest.raises(UpgradeEvidenceError, match="完整性"):
            override_conclusion(
                _PERIOD, by="sunqi", reason="理由", agent_id=_AGENT_ID, data_dir=tmp_path
            )

    def test_篡改判据项后被拒(self, tmp_path):
        """逐项取值形态同属系统字段：改写即机检失败。"""
        _build(tmp_path, items=_items(correlation=None))
        path = _material_path(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["items"][0] = {**payload["items"][0], "status": MEASURED, "value": 0.9}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with pytest.raises(UpgradeEvidenceError, match="完整性"):
            override_conclusion(
                _PERIOD, by="sunqi", reason="理由", agent_id=_AGENT_ID, data_dir=tmp_path
            )

    def test_材料不存在即报错(self, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="不存在"):
            override_conclusion(
                _PERIOD, by="sunqi", reason="理由", agent_id=_AGENT_ID, data_dir=tmp_path
            )


class Test落盘与只增不改:
    def test_按_agent_分目录且同周期双写不覆盖(self, tmp_path):
        """C3 动机：路径必须含 agent id，否则两个降级 Agent 同 ISO 周互相覆盖。"""
        first = _build(tmp_path)
        second = build_upgrade_evidence(
            _PERIOD,
            _cfg(),
            _items(correlation=None),
            judge=_judge,
            agent_id="dev",
            data_dir=tmp_path,
        )
        assert first.agent_id == _AGENT_ID and second.agent_id == "dev"
        assert _material_path(tmp_path).is_file()
        assert _material_path(tmp_path, agent_id="dev").is_file()
        assert load_evidence(_PERIOD, agent_id="dev", data_dir=tmp_path)["conclusion"] == (
            "insufficient"
        )

    def test_同周期重产即拒绝(self, tmp_path):
        _build(tmp_path)
        before = _material_path(tmp_path).read_bytes()
        with pytest.raises(UpgradeEvidenceError, match="只增不改"):
            _build(tmp_path)
        assert _material_path(tmp_path).read_bytes() == before

    def test_不同周期各产一条(self, tmp_path):
        _build(tmp_path)
        other = _build(tmp_path, period="2026-W39")
        assert other.period == "2026-W39"
        assert sorted(item.name for item in (_material_path(tmp_path).parent).glob("*.json")) == [
            "2026-W38.json",
            "2026-W39.json",
        ]

    def test_材料不存在即报错(self, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="不存在"):
            load_evidence(_PERIOD, agent_id=_AGENT_ID, data_dir=tmp_path)

    def test_材料文件内容与返回值一致(self, tmp_path):
        evidence = _build(tmp_path, items=_items(correlation=None))
        payload = json.loads(_material_path(tmp_path).read_text(encoding="utf-8"))
        assert payload["period"] == _PERIOD
        assert payload["agent_id"] == _AGENT_ID
        assert payload["conclusion"] == evidence.conclusion == "insufficient"
        assert payload["system_digest"] == evidence.system_digest
        assert payload["overrides"] == []
        assert payload["threshold_snapshot"] == evidence.threshold_snapshot
        assert evidence.to_dict()["items"] == payload["items"]
