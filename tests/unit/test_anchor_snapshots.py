"""锚点得分分布快照单测（功能 010 US2 / T532，先于实现编写；FR-009）。

- 快照落盘 calibration/snapshots/{agent_id}/{evaluator_id}/{period}.json，
  per 评估器 per 周期一条；
- 分布 = [0,1] 十等分分桶计数 + 分位数（p25/p50/p75/p90），字段供 F7 漂移检测消费；
- 被剔除/缺分量的配对不计入分布；与台账同轮次落盘。
"""

import json

import pytest

from core.calibration.ledger import write_anchor_snapshots
from core.calibration.models import PairingRecord

_PERIOD = "2026-W39"


def _pairs(key: str, anchor_scores: list[float]) -> list[PairingRecord]:
    return [
        PairingRecord(anchor_id=f"a{i}", evaluator_key=key, anchor_score=a, auto_score=0.5)
        for i, a in enumerate(anchor_scores)
    ]


def _read_snapshot(data_dir, agent_id, evaluator_id, period=_PERIOD):
    path = data_dir / "snapshots" / agent_id / evaluator_id / f"{period}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class Test快照落盘:
    def test_路径与字段(self, calibration_data_dir):
        pairs = _pairs("proxy.aesthetic@1.0.0", [0.1, 0.2, 0.3, 0.9])
        write_anchor_snapshots(calibration_data_dir, "visual", _PERIOD, pairs)
        payload = _read_snapshot(calibration_data_dir, "visual", "proxy.aesthetic")
        assert payload["agent_id"] == "visual"
        assert payload["evaluator_id"] == "proxy.aesthetic"
        assert payload["period"] == _PERIOD
        assert payload["samples"] == 4
        assert len(payload["buckets"]) == 10
        assert set(payload["quantiles"]) == {"p25", "p50", "p75", "p90"}

    def test_分桶计数口径(self, calibration_data_dir):
        # 每桶 0.1 宽：[0,0.1) … [0.9,1.0]；1.0 落入末桶
        scores = [0.05, 0.15, 0.25, 0.95, 1.0]
        write_anchor_snapshots(
            calibration_data_dir, "visual", _PERIOD, _pairs("proxy.a@1.0.0", scores)
        )
        payload = _read_snapshot(calibration_data_dir, "visual", "proxy.a")
        assert payload["buckets"][0] == 1
        assert payload["buckets"][1] == 1
        assert payload["buckets"][2] == 1
        assert payload["buckets"][9] == 2  # 0.95 与 1.0
        assert sum(payload["buckets"]) == payload["samples"]

    def test_分位数口径(self, calibration_data_dir):
        # 线性插值口径（与 numpy 默认一致）：[0.1, 0.2, 0.3, 0.4, 0.5]
        write_anchor_snapshots(
            calibration_data_dir,
            "visual",
            _PERIOD,
            _pairs("proxy.a@1.0.0", [0.5, 0.1, 0.4, 0.2, 0.3]),
        )
        quantiles = _read_snapshot(calibration_data_dir, "visual", "proxy.a")["quantiles"]
        assert quantiles["p50"] == pytest.approx(0.3)
        assert quantiles["p25"] == pytest.approx(0.2)
        assert quantiles["p75"] == pytest.approx(0.4)
        assert quantiles["p90"] == pytest.approx(0.46)

    def test_每评估器一条(self, calibration_data_dir):
        pairs = _pairs("proxy.a@1.0.0", [0.1, 0.2]) + _pairs("judge.b@1.0.0", [0.8])
        paths = write_anchor_snapshots(calibration_data_dir, "visual", _PERIOD, pairs)
        assert len(paths) == 2
        assert _read_snapshot(calibration_data_dir, "visual", "proxy.a")["samples"] == 2
        assert _read_snapshot(calibration_data_dir, "visual", "judge.b")["samples"] == 1

    def test_剔除与缺分量不计入分布(self, calibration_data_dir):
        pairs = _pairs("human.platform_metrics@1.0.0", [0.7])
        pairs.append(
            PairingRecord(
                anchor_id="a9",
                evaluator_key="human.platform_metrics@1.0.0",
                anchor_score=0.9,
                auto_score=None,
                excluded=True,
                excluded_components=("human.platform_metrics",),
                note="防自循环剔除",
            )
        )
        write_anchor_snapshots(calibration_data_dir, "promo", _PERIOD, pairs)
        payload = _read_snapshot(calibration_data_dir, "promo", "human.platform_metrics")
        assert payload["samples"] == 1  # 剔除记录不进分布
