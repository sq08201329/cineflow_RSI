"""偏差计算单测（功能 010 US2 / T517，先于实现编写；契约 C5 场景 1~3）。

- 连续分量：mean_shift = mean(anchor − auto)，Pearson r 与参考实现
  （statistics.correlation）一致；
- judge 类：Kendall τ 序一致（复用 core/replay/unbiasedness.py τ-b），
  禁止胜率与分数直接相减（judge 口径不产 mean_shift）；
- 样本 < min_samples → 只备注"样本不足"，不产偏差值；负相关产出标记。
"""

import statistics

import pytest

from core.calibration.bias import compute_bias
from core.calibration.models import PairingRecord
from core.replay.unbiasedness import kendall_tau

_KEY = "proxy.aesthetic@1.0.0"
_JUDGE_KEY = "judge.cinematic@1.0.0"
_PERIOD = "2026-W39"


def _pairs(pairs: list[tuple[float, float]], key: str = _KEY) -> list[PairingRecord]:
    return [
        PairingRecord(
            anchor_id=f"a{i}", evaluator_key=key, anchor_score=a, auto_score=s
        )
        for i, (a, s) in enumerate(pairs)
    ]


class Test连续口径:
    def test_注入已知偏移(self):
        autos = [0.05, 0.2, 0.4, 0.6, 0.75]
        anchors = [a + 0.2 for a in autos]
        record = compute_bias(
            _pairs(list(zip(anchors, autos, strict=True))),
            evaluator_key=_KEY,
            period=_PERIOD,
            min_samples=3,
        )
        assert record.mean_shift == pytest.approx(0.2, abs=1e-6)
        assert record.pearson_r == pytest.approx(1.0, abs=1e-9)
        assert record.kendall_tau is None
        assert record.samples == 5

    def test_pearson_与参考实现一致(self):
        autos = [0.12, 0.45, 0.33, 0.78, 0.56, 0.91, 0.05]
        anchors = [0.2, 0.4, 0.5, 0.6, 0.55, 0.8, 0.3]
        record = compute_bias(
            _pairs(list(zip(anchors, autos, strict=True))),
            evaluator_key=_KEY,
            period=_PERIOD,
            min_samples=3,
        )
        assert record.pearson_r == pytest.approx(statistics.correlation(anchors, autos))
        assert record.mean_shift == pytest.approx(
            statistics.mean(a - s for a, s in zip(anchors, autos, strict=True))
        )

    def test_负相关产出标记(self):
        autos = [0.1, 0.3, 0.5, 0.7, 0.9]
        anchors = [0.9, 0.7, 0.5, 0.3, 0.1]  # 完全背离
        record = compute_bias(
            _pairs(list(zip(anchors, autos, strict=True))),
            evaluator_key=_KEY,
            period=_PERIOD,
            min_samples=3,
        )
        assert record.pearson_r == pytest.approx(-1.0, abs=1e-9)
        assert "负相关" in record.note

    def test_正相关不带负相关标记(self):
        autos = [0.1, 0.3, 0.5]
        anchors = [0.2, 0.4, 0.6]
        record = compute_bias(
            _pairs(list(zip(anchors, autos, strict=True))),
            evaluator_key=_KEY,
            period=_PERIOD,
            min_samples=3,
        )
        assert "负相关" not in record.note


class TestJudge口径:
    def test_序一致_tau(self):
        autos = [0.2, 0.4, 0.6, 0.8]
        anchors = [0.15, 0.35, 0.7, 0.9]  # 排名完全一致
        record = compute_bias(
            _pairs(list(zip(anchors, autos, strict=True)), key=_JUDGE_KEY),
            evaluator_key=_JUDGE_KEY,
            period=_PERIOD,
            min_samples=3,
            judge=True,
        )
        assert record.kendall_tau == pytest.approx(kendall_tau(anchors, autos))
        assert record.kendall_tau == pytest.approx(1.0)
        # 禁止胜率当分数相减：judge 口径不产 mean_shift / pearson_r
        assert record.mean_shift is None
        assert record.pearson_r is None

    def test_序完全背离(self):
        autos = [0.2, 0.4, 0.6, 0.8]
        anchors = [0.9, 0.7, 0.4, 0.1]
        record = compute_bias(
            _pairs(list(zip(anchors, autos, strict=True)), key=_JUDGE_KEY),
            evaluator_key=_JUDGE_KEY,
            period=_PERIOD,
            min_samples=3,
            judge=True,
        )
        assert record.kendall_tau == pytest.approx(-1.0)
        assert "负相关" in record.note


class Test样本不足:
    def test_两样本不产偏差值(self):
        record = compute_bias(
            _pairs([(0.8, 0.6), (0.9, 0.7)]),
            evaluator_key=_KEY,
            period=_PERIOD,
            min_samples=3,
        )
        assert record.samples == 2
        assert record.mean_shift is None
        assert record.pearson_r is None
        assert "样本不足" in record.note

    def test_剔除与缺分量不计入样本(self):
        pairs = _pairs([(0.8, 0.6), (0.9, 0.7)])
        pairs.append(
            PairingRecord(
                anchor_id="a9",
                evaluator_key=_KEY,
                anchor_score=0.5,
                auto_score=None,
                excluded=True,
                excluded_components=("human.platform_metrics",),
                note="防自循环剔除",
            )
        )
        record = compute_bias(
            pairs, evaluator_key=_KEY, period=_PERIOD, min_samples=3
        )
        assert record.samples == 2  # 剔除记录不计样本
        assert "样本不足" in record.note
