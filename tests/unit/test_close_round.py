"""轮次收口管线单测（功能 010 US2 / T522 先行测试）。

close_round：配对 → 偏差 → 台账 → 锚点分布快照 → 信度报告，
轮次状态 intake → closed；样本不足照常 closed 并注明。
"""

import json
from datetime import UTC, datetime

import pytest

from core.calibration.anchors import intake_anchors
from core.calibration.config import CalibrationConfig
from core.calibration.rounds import close_round
from core.calibration.selection import build_blind_list
from core.evaluators.errors import ValidationError

_BASE_TS = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
_CONFIG = CalibrationConfig.from_dict(
    {
        "calibration": {
            "period_days": 7,
            "top_k": 5,
            "min_samples": 3,
            "bias_threshold": 0.15,
            "reliability_target": 0.6,
            "ridge_lambda": 1.0,
            "self_pairing_exclusions": {"platform_truth": ["human.platform_metrics"]},
        }
    }
)


@pytest.fixture()
def round_with_anchors(
    tree_store, build_calibration_tree, anchors_engine, calibration_data_dir
):
    """3 节点树 + 轮次 + 3 条人评锚点入库（intake 态）。"""
    breakdown = {
        "proxy.aesthetic@1.0.0": {"score": 0.6},
        "judge.cinematic@1.0.0": {"score": 0.5},
    }
    _, node_ids = build_calibration_tree(
        [(0.5, dict(breakdown)), (0.7, dict(breakdown)), (0.9, dict(breakdown))],
        agent_id="visual",
        base_created_at=_BASE_TS,
    )
    round_ = build_blind_list(
        tree_store,
        agent_id="visual",
        period_start="2026-09-14",
        period_end="2026-09-20",
        top_k=5,
        data_dir=calibration_data_dir,
    )
    entries = [
        {"node_id": nid, "score": score, "reviewer": "r1"}
        for nid, score in zip(node_ids, [0.55, 0.75, 0.95], strict=True)
    ]
    with anchors_engine.begin() as conn:
        intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
    return round_


def _round_status(data_dir, round_):
    path = data_dir / "rounds" / round_.agent_id / f"{round_.round_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["status"]


class Test收口管线:
    def test_全产物落盘且轮次_closed(
        self, tree_store, anchors_engine, calibration_data_dir, round_with_anchors
    ):
        round_ = round_with_anchors
        with anchors_engine.connect() as conn:
            summary = close_round(
                tree_store, conn, calibration_data_dir, round_id=round_.round_id, config=_CONFIG
            )
        assert summary["status"] == "closed"
        assert _round_status(calibration_data_dir, round_) == "closed"
        # 周期标签 = period_end 的 ISO 周
        period = summary["period"]
        assert period == "2026-W38"  # 2026-09-20 属 ISO 第 38 周

        # 台账：两个评估器各一行
        ledger = calibration_data_dir / "ledger" / "visual" / "proxy.aesthetic.jsonl"
        assert ledger.is_file() and len(ledger.read_text().splitlines()) == 1
        judge_ledger = calibration_data_dir / "ledger" / "visual" / "judge.cinematic.jsonl"
        judge_record = json.loads(judge_ledger.read_text().splitlines()[0])
        assert judge_record["kendall_tau"] is not None
        assert judge_record["mean_shift"] is None  # judge 口径不产 mean_shift

        # 快照与报告落盘
        snapshot = (
            calibration_data_dir / "snapshots" / "visual" / "proxy.aesthetic" / f"{period}.json"
        )
        assert snapshot.is_file()
        report = json.loads(
            (calibration_data_dir / "reports" / f"{period}.json").read_text(encoding="utf-8")
        )
        assert report["target"] == 0.6
        assert report["agents"]["visual"]["proxy.aesthetic@1.0.0"]["samples"] == 3

    def test_closed_轮次不可重复收口(
        self, tree_store, anchors_engine, calibration_data_dir, round_with_anchors
    ):
        round_ = round_with_anchors
        with anchors_engine.connect() as conn:
            close_round(
                tree_store, conn, calibration_data_dir, round_id=round_.round_id, config=_CONFIG
            )
            with pytest.raises(ValidationError, match="closed"):
                close_round(
                    tree_store, conn, calibration_data_dir, round_id=round_.round_id, config=_CONFIG
                )

    def test_样本不足照常_closed_并注明(
        self, tree_store, build_calibration_tree, anchors_engine, calibration_data_dir
    ):
        breakdown = {"proxy.aesthetic@1.0.0": {"score": 0.6}}
        _, node_ids = build_calibration_tree(
            [(0.5, dict(breakdown)), (0.7, dict(breakdown))],
            agent_id="visual",
            base_created_at=_BASE_TS,
        )
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start="2026-09-14",
            period_end="2026-09-20",
            top_k=5,
            data_dir=calibration_data_dir,
        )
        entries = [
            {"node_id": nid, "score": s, "reviewer": "r1"}
            for nid, s in zip(node_ids, [0.6, 0.8], strict=True)
        ]
        with anchors_engine.begin() as conn:
            intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
        with anchors_engine.connect() as conn:
            summary = close_round(
                tree_store, conn, calibration_data_dir, round_id=round_.round_id, config=_CONFIG
            )
        assert summary["status"] == "closed"  # 2 样本 < min_samples=3，照常 closed
        # 台账记录注明样本不足、不产偏差值
        ledger = calibration_data_dir / "ledger" / "visual" / "proxy.aesthetic.jsonl"
        record = json.loads(ledger.read_text().splitlines()[0])
        assert record["pearson_r"] is None
        assert "样本不足" in record["note"]
        # 轮次文件注明样本不足
        path = calibration_data_dir / "rounds" / round_.agent_id / f"{round_.round_id}.json"
        assert "样本不足" in json.loads(path.read_text(encoding="utf-8"))["note"]
