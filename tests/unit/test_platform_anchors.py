"""平台真值锚点适配单测（功能 010 US1 / T511，先于实现编写；契约 C3 场景 1~2）。

- 读 promo 运营回流（ingested 行的 metrics.platform_metrics），按 configs 的
  promo.metric_weights 归一化口径（复用 PlatformMetricsEvaluator）合成 score ∈ [0,1]；
- source=platform_truth、reviewer=渠道标识（material.platform）；
- 同轮重复采集 → 唯一键幂等拒绝，不产生重复锚点。
"""

import pytest
from sqlalchemy import select

from agents.promo.anchors import collect_platform_anchors
from core.calibration.db import calibration_anchors

# 夹具默认快照的期望得分（口径：metric_weights {ctr:0.5, completion_rate:0.3, conversions:0.2}，
# ctr_cap=0.2、转化率上限 0.05）：
# ctr_n = min(1, 0.05/0.2) = 0.25；conv_n = min(1, (12/1000)/0.05) = 0.24
# score = 0.5*0.25 + 0.3*0.6 + 0.2*0.24 = 0.353
EXPECTED_SCORE = 0.5 * 0.25 + 0.3 * 0.6 + 0.2 * 0.24


def _anchor_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(calibration_anchors)).all()


class Test平台真值采集:
    def test_12条回流全部入库(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        for _ in range(12):
            make_platform_backfill()
        with anchors_engine.begin() as conn:
            anchors = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
        assert len(anchors) == 12
        rows = _anchor_rows(anchors_engine)
        assert len(rows) == 12
        for row in rows:
            assert row.source == "platform_truth"
            assert row.agent_id == "promo"
            assert row.reviewer == "douyin"  # 渠道标识
            assert row.round_id == "calib-r1"
            assert row.score == pytest.approx(EXPECTED_SCORE)

    def test_归一化口径复用_metric_weights(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        make_platform_backfill(snapshot={"ctr": 0.2, "completion_rate": 1.0, "conversions": 50})
        with anchors_engine.begin() as conn:
            anchors = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
        # ctr_n=1.0、completion=1.0、conv_n=min(1,(50/1000)/0.05)=1.0 → 满分
        assert anchors[0].score == pytest.approx(1.0)

    def test_非_ingested_与缺指标行跳过(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        make_platform_backfill(status="delivered")  # 未回流
        make_platform_backfill(metrics={"material": {"platform": "douyin"}})  # 缺指标
        make_platform_backfill()
        with anchors_engine.begin() as conn:
            anchors = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
        assert len(anchors) == 1

    def test_渠道标识进入_reviewer(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        make_platform_backfill(material={"platform": "kuaishou"})
        with anchors_engine.begin() as conn:
            anchors = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
        assert anchors[0].reviewer == "kuaishou"


class Test同轮幂等:
    def test_重复采集不产生重复锚点(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        for _ in range(3):
            make_platform_backfill()
        with anchors_engine.begin() as conn:
            first = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
            second = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
        assert len(first) == 3
        assert second == []
        assert len(_anchor_rows(anchors_engine)) == 3

    def test_不同轮次同渠道同节点被唯一键拒绝(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        """同 (node_id, reviewer, round_id) 才算重复；换轮次可再采集（同节点新周期锚点）。"""
        make_platform_backfill()
        with anchors_engine.begin() as conn:
            collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
            second_round = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r2", config=promo_config
            )
        assert len(second_round) == 1  # round_id 不同 → 唯一键不冲突
        assert len(_anchor_rows(anchors_engine)) == 2
