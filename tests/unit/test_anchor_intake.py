"""人评录入通道单测（功能 010 US1 / T510，先于实现编写；契约 C2 场景 1~3）。

- 校验：score ∈ [0,1]、reviewer 非空、round 存在且状态 ∈ {open, intake}、
  node_id 在该轮清单内（防录错节点）；
- 写入 calibration_anchors（source=human_blind）；同 (node_id, reviewer, round_id)
  冲突 → 该条拒绝并计数，整批不中断；
- 录入后轮次状态 open → intake。
"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from core.calibration.anchors import intake_anchors
from core.calibration.db import calibration_anchors
from core.calibration.selection import build_blind_list
from core.evaluators.errors import ValidationError

_BASE_TS = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
_BREAKDOWN = {"proxy.aesthetic@1.0.0": {"score": 0.7}}


@pytest.fixture()
def round_with_list(tree_store, build_calibration_tree, calibration_data_dir):
    """3 节点夹具树 + 已落盘的盲评轮次；返回 (round_, node_ids)。"""
    _, node_ids = build_calibration_tree(
        [(0.3, dict(_BREAKDOWN)), (0.9, dict(_BREAKDOWN)), (0.6, dict(_BREAKDOWN))],
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
    return round_, node_ids


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(calibration_anchors)).all()


def _round_status(data_dir, round_):
    path = data_dir / "rounds" / round_.agent_id / f"{round_.round_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["status"]


class Test合法录入:
    def test_三条全部入库且轮次转_intake(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        entries = [
            make_anchor_entry(node_id=nid, score=0.7, reviewer="reviewer-1") for nid in node_ids
        ]
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
        assert accepted == 3
        rows = _rows(anchors_engine)
        assert len(rows) == 3
        for row in rows:
            assert row.source == "human_blind"
            assert row.round_id == round_.round_id
            assert row.reviewer == "reviewer-1"
        assert _round_status(calibration_data_dir, round_) == "intake"


class Test逐条拒绝不中断:
    def test_越界得分该条拒绝并注明取值域(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        entries = [
            make_anchor_entry(node_id=node_ids[0], score=1.2, reviewer="r1"),
            make_anchor_entry(node_id=node_ids[1], score=0.5, reviewer="r1"),
        ]
        rejections: list = []
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn,
                round_.round_id,
                entries,
                data_dir=calibration_data_dir,
                rejections=rejections,
            )
        assert accepted == 1
        assert len(rejections) == 1
        assert "score" in rejections[0]["reason"] and "[0,1]" in rejections[0]["reason"]
        assert len(_rows(anchors_engine)) == 1

    def test_reviewer_为空拒绝(self, anchors_engine, round_with_list, calibration_data_dir):
        round_, node_ids = round_with_list
        rejections: list = []
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn,
                round_.round_id,
                [{"node_id": node_ids[0], "score": 0.5, "reviewer": ""}],
                data_dir=calibration_data_dir,
                rejections=rejections,
            )
        assert accepted == 0
        assert "reviewer" in rejections[0]["reason"]

    def test_清单外节点拒绝(self, anchors_engine, round_with_list, calibration_data_dir):
        round_, _ = round_with_list
        rejections: list = []
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn,
                round_.round_id,
                [{"node_id": "ghost-node", "score": 0.5, "reviewer": "r1"}],
                data_dir=calibration_data_dir,
                rejections=rejections,
            )
        assert accepted == 0
        assert "清单" in rejections[0]["reason"]

    def test_缺字段拒绝(self, anchors_engine, round_with_list, calibration_data_dir):
        round_, node_ids = round_with_list
        rejections: list = []
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn,
                round_.round_id,
                [{"node_id": node_ids[0], "score": 0.5}],  # 缺 reviewer
                data_dir=calibration_data_dir,
                rejections=rejections,
            )
        assert accepted == 0
        assert rejections


class Test幂等拒绝:
    def test_同键重复提交拒绝且不变更(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        entry = make_anchor_entry(node_id=node_ids[0], score=0.7, reviewer="r1")
        with anchors_engine.begin() as conn:
            assert (
                intake_anchors(conn, round_.round_id, [entry], data_dir=calibration_data_dir) == 1
            )
        rejections: list = []
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn,
                round_.round_id,
                [entry],
                data_dir=calibration_data_dir,
                rejections=rejections,
            )
        assert accepted == 0
        assert len(rejections) == 1 and "重复" in rejections[0]["reason"]
        assert len(_rows(anchors_engine)) == 1  # 幂等：不产生变更

    def test_同批内同键第二条拒绝(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        entry = make_anchor_entry(node_id=node_ids[0], score=0.7, reviewer="r1")
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn, round_.round_id, [entry, dict(entry)], data_dir=calibration_data_dir
            )
        assert accepted == 1
        assert len(_rows(anchors_engine)) == 1

    def test_不同评审人同节点可共存(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        entries = [
            make_anchor_entry(node_id=node_ids[0], score=0.7, reviewer="r1"),
            make_anchor_entry(node_id=node_ids[0], score=0.9, reviewer="r2"),
        ]
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
        assert accepted == 2


class Test轮次门禁:
    def test_轮次不存在报错(self, anchors_engine, calibration_data_dir):
        with anchors_engine.begin() as conn:
            with pytest.raises(ValidationError, match="不存在"):
                intake_anchors(conn, "ghost-round", [], data_dir=calibration_data_dir)

    def test_closed_轮次拒绝录入(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        # 直接把轮次文件改写为 closed（收口管线的最终态）
        path = calibration_data_dir / "rounds" / round_.agent_id / f"{round_.round_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "closed"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with anchors_engine.begin() as conn:
            with pytest.raises(ValidationError, match="closed"):
                intake_anchors(
                    conn,
                    round_.round_id,
                    [make_anchor_entry(node_id=node_ids[0], score=0.5, reviewer="r1")],
                    data_dir=calibration_data_dir,
                )

    def test_intake_状态下可继续录入(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        with anchors_engine.begin() as conn:
            intake_anchors(
                conn,
                round_.round_id,
                [make_anchor_entry(node_id=node_ids[0], score=0.5, reviewer="r1")],
                data_dir=calibration_data_dir,
            )
            accepted = intake_anchors(
                conn,
                round_.round_id,
                [make_anchor_entry(node_id=node_ids[1], score=0.6, reviewer="r1")],
                data_dir=calibration_data_dir,
            )
        assert accepted == 1
        assert _round_status(calibration_data_dir, round_) == "intake"


class Test存储层冻结:
    def test_入库锚点_UPDATE_DELETE_被触发器拒绝(
        self, anchors_engine, round_with_list, calibration_data_dir, make_anchor_entry
    ):
        round_, node_ids = round_with_list
        with anchors_engine.begin() as conn:
            intake_anchors(
                conn,
                round_.round_id,
                [make_anchor_entry(node_id=node_ids[0], score=0.5, reviewer="r1")],
                data_dir=calibration_data_dir,
            )
        with pytest.raises(Exception, match="immutable"):
            with anchors_engine.begin() as conn:
                conn.execute(calibration_anchors.update().values(score=0.1))
        with pytest.raises(Exception, match="immutable"):
            with anchors_engine.begin() as conn:
                conn.execute(calibration_anchors.delete())
        with anchors_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM calibration_anchors")).scalar()
        assert count == 1
