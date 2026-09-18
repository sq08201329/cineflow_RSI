"""无偏性验收门禁测试（US3 / T131，发布阻塞）。

- 一致轨迹对 100% 放行（τ ≥ 0.95）；
- 注入偏差轨迹对 100% 拒绝（SC-002 回归用例）；
- 报告 JSON schema 校验（data-model §4）；
- FAILED 轮次（得分为 None）剔除并计入 notes；
- 门槛可由 configs 覆盖。
"""

import json
from pathlib import Path

import pytest
import yaml

from core.replay.unbiasedness import verify_unbiasedness
from tests.unbiasedness.fixtures import biased_pairs, consistent_pair

pytestmark = pytest.mark.unbiasedness

MOVIE_YAML = Path(__file__).resolve().parents[2] / "configs" / "movie.yaml"


class Test放行与拒绝:
    def test_一致轨迹对放行(self):
        real, replay = consistent_pair()
        report = verify_unbiasedness(real, replay, policy_version="a1b2c3d4e5f6")
        assert report.verdict == "pass"
        assert report.tau >= 0.95

    def test_注入偏差_百分之百拒绝(self):
        """SC-002：每种注入偏差形态都必须被判拒绝。"""
        variants = biased_pairs()
        assert len(variants) >= 5  # N 种偏差形态
        for name, real, replay in variants:
            report = verify_unbiasedness(real, replay)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < 0.95

    def test_门槛可覆盖(self):
        """configs 的 replay.unbiasedness_tau_threshold 可覆盖默认门槛。"""
        config = yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8"))
        threshold = config["replay"]["unbiasedness_tau_threshold"]
        assert threshold == 0.95
        real, replay = consistent_pair()
        # 一致的轨迹在更严门槛下仍放行；τ=0.9 的轨迹在默认门槛拒绝、在 0.8 门槛放行
        assert verify_unbiasedness(real, replay, threshold=threshold).verdict == "pass"
        near = verify_unbiasedness([0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.4, 0.3])
        assert near.tau == pytest.approx(2 / 3)
        assert near.verdict == "reject"
        assert (
            verify_unbiasedness([0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.4, 0.3], threshold=0.5).verdict
            == "pass"
        )


class TestFAILED轮次剔除:
    def test_失败轮次剔除后计算(self):
        real = [0.1, None, 0.9, 0.5]
        replay = [0.1, 0.4, 0.9, 0.5]
        report = verify_unbiasedness(real, replay)
        assert report.verdict == "pass"  # 剔除 None 后 τ=1
        assert "剔除" in report.notes and "1" in report.notes
        assert report.real_scores == [0.1, 0.9, 0.5]  # 报告含剔除后的序列

    def test_双侧失败位置取有效交集(self):
        real = [0.1, None, 0.9]
        replay = [0.2, 0.8, None]
        report = verify_unbiasedness(real, replay)
        assert report.real_scores == [0.1]
        # 剔除后仅剩 1 个样本 → 样本不足
        assert report.verdict == "reject"
        assert "样本不足" in report.notes


class Test报告schema:
    def test_报告字段齐备且可JSON序列化(self):
        real, replay = consistent_pair()
        report = verify_unbiasedness(real, replay, policy_version="a1b2c3d4e5f6")
        data = json.loads(report.to_json())
        assert set(data) == {
            "policy_version",
            "tau",
            "threshold",
            "verdict",
            "real_scores",
            "replay_scores",
            "notes",
        }
        assert data["policy_version"] == "a1b2c3d4e5f6"
        assert data["threshold"] == 0.95
        assert data["verdict"] in ("pass", "reject")
        assert isinstance(data["tau"], float)
        assert data["real_scores"] == real
        assert data["replay_scores"] == replay
