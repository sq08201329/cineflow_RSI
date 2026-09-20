"""升级判据材料单测（功能 009 / T927，先于实现编写；C15）。

判据阈值配置化 + **系统自动判定**（澄清 Q1）：阈值快照（来自
`ScreenplayConfig.upgrade_criteria`）+ 原始数值（judge 信度相关系数与样本量——来源 010
台账；漂移指标；门禁违规分布；人评锚点计数）→ 结论 `meets | below`（相关系数 ≥
judge_r_target、样本量 ≥ min_samples、漂移在 drift_band 内、门禁违规率 ≤
gate_violation_max 四条齐达才算达标）；阈值缺失即报错（不允许静默"无判据"）。

材料为**不可变快照**（`calibration/upgrade-events/{period}.json`，已存在即拒绝）；
人可推翻结论但**必须留痕**（人/时间/理由），且**系统结论字段逐字节不变**；
不达标必须如实标注（不得暗示可升级）。
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.upgrade_evidence import (
    UpgradeEvidence,
    UpgradeEvidenceError,
    build_upgrade_evidence,
    gate_violation_rate_of,
    judge_reliability,
    load_evidence,
    override_conclusion,
)
from core.tree.models import NodeStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_PERIOD = "2026-W38"
# 010 台账记录（judge 分量：Kendall τ + 样本量）
_KEY = "judge.dramatic_tension@1.0.0+j1"
_LEDGER_OK = {"evaluator_key": _KEY, "samples": 8, "kendall_tau": 0.72}
_LEDGER_THIN = {"evaluator_key": _KEY, "samples": 2, "kendall_tau": 0.80}
_LEDGER_NEG = {"evaluator_key": _KEY, "samples": 9, "kendall_tau": -0.40}


@pytest.fixture()
def cfg():
    return ScreenplayConfig.from_dict(copy.deepcopy(_REAL_CONFIG))


@pytest.fixture()
def calibration():
    """010 校准配置（提供信度目标语义：判据快照记录 reliability_target 供对账）。"""
    from core.calibration.config import CalibrationConfig

    return CalibrationConfig.from_dict(copy.deepcopy(_REAL_CONFIG))


def _build(
    cfg,
    calibration,
    *,
    ledger=_LEDGER_OK,
    drift=0.05,
    gate_violation_rate=0.1,
    human_anchor_count=5,
    period=_PERIOD,
    data_dir,
):
    return build_upgrade_evidence(
        period,
        cfg,
        ledger,
        calibration,
        drift=drift,
        gate_violation_rate=gate_violation_rate,
        human_anchor_count=human_anchor_count,
        data_dir=data_dir,
    )


def _system_view(payload: dict) -> str:
    """系统写入部分（排除 overrides）的规范化文本——"逐字节不变"机检口径。"""
    return json.dumps(
        {key: value for key, value in payload.items() if key != "overrides"},
        ensure_ascii=False,
        sort_keys=True,
    )


class Test判据材料内容:
    def test_达标结论为_meets(self, cfg, calibration, tmp_path):
        """C15 场景 1：四条齐达 → meets，数值与阈值快照齐全。"""
        evidence = _build(cfg, calibration, data_dir=tmp_path)
        assert isinstance(evidence, UpgradeEvidence)
        assert evidence.conclusion == "meets"
        assert evidence.reasons == []
        criteria = {
            key: evidence.threshold_snapshot[key]
            for key in ("judge_r_target", "min_samples", "drift_band", "gate_violation_max")
        }
        assert criteria == {
            "judge_r_target": 0.6,
            "min_samples": 5,
            "drift_band": 0.1,
            "gate_violation_max": 0.2,
        }
        assert evidence.raw["judge_correlation"] == pytest.approx(0.72)
        assert evidence.raw["judge_samples"] == 8
        assert evidence.raw["judge_metric"] == "kendall_tau"
        assert evidence.raw["drift"] == pytest.approx(0.05)
        assert evidence.raw["gate_violation_rate"] == pytest.approx(0.1)
        assert evidence.raw["human_anchor_count"] == 5

    def test_阈值快照记录_010_信度目标与材料落盘(self, cfg, calibration, tmp_path):
        """快照含 010 信度目标（对账口径）+ 材料落 calibration/upgrade-events/{period}.json。"""
        evidence = _build(cfg, calibration, data_dir=tmp_path)
        assert evidence.threshold_snapshot["reliability_target"] == pytest.approx(0.6)
        path = tmp_path / f"{_PERIOD}.json"
        assert path.is_file()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["period"] == _PERIOD
        assert payload["conclusion"] == "meets"
        assert payload["threshold_snapshot"] == evidence.threshold_snapshot
        assert payload["system_digest"] == evidence.system_digest
        assert payload["overrides"] == []

    def test_材料不可变_重复生成即拒绝(self, cfg, calibration, tmp_path):
        _build(cfg, calibration, data_dir=tmp_path)
        with pytest.raises(UpgradeEvidenceError, match="已存在"):
            _build(cfg, calibration, data_dir=tmp_path)
        # 只增不改：内容与首次一致
        payload = load_evidence(_PERIOD, data_dir=tmp_path)
        assert payload["conclusion"] == "meets"

    def test_不同周期各产一条(self, cfg, calibration, tmp_path):
        _build(cfg, calibration, data_dir=tmp_path)
        other = _build(cfg, calibration, period="2026-W39", data_dir=tmp_path)
        assert other.period == "2026-W39"
        assert sorted(path.name for path in tmp_path.glob("*.json")) == [
            "2026-W38.json",
            "2026-W39.json",
        ]


class Test系统自动判定:
    def test_样本不足判_below_并标注(self, cfg, calibration, tmp_path):
        """C15 场景 2：样本量 < min_samples → below + "样本不足"标注（不得暗示可升级）。"""
        evidence = _build(cfg, calibration, ledger=_LEDGER_THIN, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("样本不足" in reason for reason in evidence.reasons)
        assert "样本不足" in evidence.to_json()

    def test_负相关判_below_并告警(self, cfg, calibration, tmp_path):
        """C15 场景 3：相关系数为负 → below + 告警条目。"""
        evidence = _build(cfg, calibration, ledger=_LEDGER_NEG, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("负相关" in alert for alert in evidence.alerts)
        assert any("负相关" in reason for reason in evidence.reasons)

    def test_信度低于目标判_below(self, cfg, calibration, tmp_path):
        ledger = {"samples": 9, "kendall_tau": 0.35}
        evidence = _build(cfg, calibration, ledger=ledger, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("0.35" in reason for reason in evidence.reasons)

    def test_漂移越带判_below(self, cfg, calibration, tmp_path):
        evidence = _build(cfg, calibration, drift=0.4, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("漂移" in reason for reason in evidence.reasons)

    def test_漂移缺失判_below_并标注(self, cfg, calibration, tmp_path):
        """漂移未测量不得判定达标（判据不完整如实标注，原则六）。"""
        evidence = _build(cfg, calibration, drift=None, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("漂移指标缺失" in reason for reason in evidence.reasons)

    def test_门禁违规率超限判_below(self, cfg, calibration, tmp_path):
        evidence = _build(cfg, calibration, gate_violation_rate=0.5, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert any("门禁违规率" in reason for reason in evidence.reasons)

    def test_无台账记录判_below(self, cfg, calibration, tmp_path):
        evidence = _build(cfg, calibration, ledger=None, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        assert evidence.raw["judge_samples"] == 0
        assert any("台账" in reason for reason in evidence.reasons)

    def test_边界取闭区间(self, cfg, calibration, tmp_path):
        """相关系数 == target、样本 == min_samples、漂移 == band、违规率 == max 均达标。"""
        evidence = _build(
            cfg,
            calibration,
            ledger={"samples": 5, "kendall_tau": 0.6},
            drift=0.1,
            gate_violation_rate=0.2,
            data_dir=tmp_path,
        )
        assert evidence.conclusion == "meets"


class Test阈值缺失即报错:
    @pytest.mark.parametrize(
        "missing",
        ["judge_r_target", "min_samples", "drift_band", "gate_violation_max"],
    )
    def test_缺阈值即报错(self, cfg, calibration, tmp_path, missing):
        """阈值缺失即报错——不允许静默"无判据"（澄清 Q1 / FR-010）。"""
        criteria = dict(cfg.upgrade_criteria)
        del criteria[missing]
        stale = SimpleNamespace(upgrade_criteria=criteria)
        with pytest.raises(UpgradeEvidenceError, match=missing):
            _build(stale, calibration, data_dir=tmp_path)
        assert list(tmp_path.glob("*.json")) == []  # 不产材料

    def test_缺_010_信度目标即报错(self, cfg, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="reliability_target"):
            _build(cfg, SimpleNamespace(), data_dir=tmp_path)

    def test_周期为空即报错(self, cfg, calibration, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="period"):
            _build(cfg, calibration, period="", data_dir=tmp_path)


class Test人推翻留痕:
    """C15 场景 4：人推翻 below 结论 → 留痕（人/时间/理由）且系统结论字段逐字节不变。"""

    def test_推翻留痕且系统结论不变(self, cfg, calibration, tmp_path):
        evidence = _build(cfg, calibration, ledger=_LEDGER_THIN, data_dir=tmp_path)
        assert evidence.conclusion == "below"
        before = _system_view(load_evidence(_PERIOD, data_dir=tmp_path))
        updated = override_conclusion(
            _PERIOD, by="sunqi", reason="已补齐人工锚点，下周期复核", data_dir=tmp_path
        )
        assert updated.conclusion == "below"  # 系统结论不因推翻改写
        assert updated.overrides == [
            {
                "by": "sunqi",
                "reason": "已补齐人工锚点，下周期复核",
                "at": updated.overrides[0]["at"],
            }
        ]
        assert updated.overrides[0]["at"]
        after_payload = load_evidence(_PERIOD, data_dir=tmp_path)
        assert _system_view(after_payload) == before  # 逐字节不变（含结论与阈值快照）
        assert after_payload["overrides"] == updated.overrides

    def test_推翻二次追加不覆盖(self, cfg, calibration, tmp_path):
        _build(cfg, calibration, data_dir=tmp_path)
        system_before = _system_view(load_evidence(_PERIOD, data_dir=tmp_path))
        override_conclusion(_PERIOD, by="sunqi", reason="第一次", data_dir=tmp_path)
        second = override_conclusion(_PERIOD, by="reviewer-2", reason="第二次", data_dir=tmp_path)
        assert [record["by"] for record in second.overrides] == ["sunqi", "reviewer-2"]
        assert _system_view(load_evidence(_PERIOD, data_dir=tmp_path)) == system_before

    @pytest.mark.parametrize(
        "by, reason",
        [("", "理由"), ("sunqi", ""), ("sunqi", "   ")],
        ids=["无人", "空理由", "空白"],
    )
    def test_推翻必填人与理由(self, cfg, calibration, tmp_path, by, reason):
        _build(cfg, calibration, data_dir=tmp_path)
        before = load_evidence(_PERIOD, data_dir=tmp_path)
        with pytest.raises(UpgradeEvidenceError):
            override_conclusion(_PERIOD, by=by, reason=reason, data_dir=tmp_path)
        assert load_evidence(_PERIOD, data_dir=tmp_path) == before  # 留痕失败零变更

    def test_材料不存在即报错(self, tmp_path):
        with pytest.raises(UpgradeEvidenceError, match="不存在"):
            override_conclusion(_PERIOD, by="sunqi", reason="理由", data_dir=tmp_path)

    def test_篡改系统字段被拒(self, cfg, calibration, tmp_path):
        """快照完整性：系统字段被手工改写 → 推翻被拒（system_digest 机检）。"""
        _build(cfg, calibration, data_dir=tmp_path)
        path = tmp_path / f"{_PERIOD}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["threshold_snapshot"]["judge_r_target"] = 0.01  # 手工放宽阈值
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with pytest.raises(UpgradeEvidenceError, match="完整性"):
            override_conclusion(_PERIOD, by="sunqi", reason="理由", data_dir=tmp_path)


class Test原始数值计算面:
    def test_judge_信度从台账读取(self):
        assert judge_reliability(_LEDGER_OK) == {
            "correlation": 0.72,
            "samples": 8,
            "metric": "kendall_tau",
            "note": "",
        }
        pearson = {"samples": 6, "pearson_r": 0.55, "note": "连续分量口径"}
        assert judge_reliability(pearson)["metric"] == "pearson_r"
        assert judge_reliability(None)["samples"] == 0
        assert "台账" in judge_reliability(None)["note"]

    def test_门禁违规率口径(self, make_script_artifact, make_node):
        """门禁违规率 = 含 rule.* 判 0 分量的节点占比（可复现计算）。"""
        nodes = [
            make_node(eval_breakdown={"rule.beat_structure@1.0.0": {"score": 1.0}}),
            make_node(eval_breakdown={"rule.beat_structure@1.0.0": {"score": 0.0}}),
            make_node(eval_breakdown={"proxy.entity_consistency@1.0.0": {"score": 0.4}}),
            make_node(
                eval_breakdown={"rule.scene_character@1.0.0": {"score": 0.0}},
                status=NodeStatus.FAILED,
                score=None,
            ),
        ]
        # 分母 = 已评估节点 4 个；分子 = 含 rule 判 0 的 2 个（FAILED 节点不入分母）
        assert gate_violation_rate_of(nodes) == pytest.approx(1 / 3)

    def test_门禁违规率无节点为_0(self):
        assert gate_violation_rate_of([]) == 0.0
