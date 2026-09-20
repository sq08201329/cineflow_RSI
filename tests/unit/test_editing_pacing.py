"""节奏曲线代理评估器单测（功能 007 / T721，先于实现编写）。

C7：成片镜头时长序列按相对位置分段（PacingBaseline.segments 的 span）统计
均值/方差，与基准段的加权欧氏距离 d → score = 1 − min(1, d/d_cap)，定点 6 位；
贴近 vs 背离基准分差显著（场景 4）；距离口径误差 < 1e-6；缺基准拒绝启动
（配置纪律——不允许静默无基准打分）。
"""

import math

import pytest

from agents.editing.config import EditingConfigError
from agents.editing.evaluators.pacing import PacingCurveEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind

# 测试基准：两段等分；var_ms 为 ms² 量纲（d_cap 同量纲，否则距离饱和恒 0）
_BASELINE = {
    "d_cap": 2000000.0,
    "segments": [
        {"span": [0.0, 0.5], "mean_ms": 2000, "var_ms": 250000, "weight": 1.0},
        {"span": [0.5, 1.0], "mean_ms": 4000, "var_ms": 1000000, "weight": 2.0},
    ],
}


def _evaluate(durations, baseline=_BASELINE):
    evaluator = PacingCurveEvaluator(baseline)
    artifact = ArtifactRef(artifact_hash="ab" * 32, metadata={"shot_durations_ms": durations})
    return evaluator.evaluate(artifact, {})


def _expected(durations, baseline=_BASELINE) -> float:
    """独立手算口径（测试侧的平行实现，交叉验证 < 1e-6）。"""
    n = len(durations)
    distance_sq = 0.0
    for segment in baseline["segments"]:
        lo, hi = segment["span"]
        shots = [d for i, d in enumerate(durations) if lo <= (i + 0.5) / n < hi]
        if not shots:
            continue  # 空段跳过（无镜头落入不臆造统计）
        mean = sum(shots) / len(shots)
        var = sum((d - mean) ** 2 for d in shots) / len(shots)  # 总体方差
        distance_sq += segment["weight"] * (
            (mean - segment["mean_ms"]) ** 2 + (var - segment["var_ms"]) ** 2
        )
    d = math.sqrt(distance_sq)
    return 1.0 - min(1.0, d / baseline["d_cap"])


class Test距离口径:
    def test_手算交叉验证误差小于阈值(self):
        """C7：距离口径误差 < 1e-6（测试侧平行实现交叉验证）。"""
        durations = [1600, 2500, 3000, 5000]  # 前段 mean 2050 var 202500；后段精确命中
        result = _evaluate(durations)
        assert abs(result.score - _expected(durations)) < 1e-6
        expected_d = math.sqrt((2050 - 2000) ** 2 + (202500 - 250000) ** 2)
        assert result.diagnostics["distance"] == pytest.approx(expected_d)

    def test_定点六位(self):
        result = _evaluate([1450, 2550, 3100, 4900])
        assert result.score == round(result.score, 6)

    def test_诊断分段统计(self):
        result = _evaluate([1600, 2500, 3000, 5000])
        segments = result.diagnostics["segments"]
        assert len(segments) == 2
        assert segments[0]["shot_count"] == 2
        assert segments[0]["mean_ms"] == pytest.approx(2050.0)
        assert segments[0]["var_ms"] == pytest.approx(202500.0)
        assert result.diagnostics["d_cap"] == 2000000.0


class Test贴近与背离:
    def test_贴近基准满分(self):
        """完全贴合基准统计（mean/var 双双命中）→ d=0 → score=1.0。"""
        assert _evaluate([1500, 2500, 3000, 5000]).score == 1.0

    def test_背离基准低分(self):
        """前段过碎（200ms）、后段拖沓（20s）→ d 逼近 d_cap → 低分。"""
        assert _evaluate([200, 200, 20000, 20000]).score < 0.3

    def test_分差显著(self):
        """验收场景 4：贴近 vs 背离两组分差显著（> 0.5）。"""
        close = _evaluate([1450, 2550, 3100, 4900]).score
        far = _evaluate([200, 200, 20000, 20000]).score
        assert close - far > 0.5


class Test分段语义:
    def test_相对位置分段与段权重(self):
        """权重生效：同样的均值偏差落在高权重段（后段 w=2.0）扣分更多。"""
        front_deviated = _evaluate([3000, 4000, 3000, 5000]).score  # 偏差在前段
        back_deviated = _evaluate([1500, 2500, 5000, 6000]).score  # 同等偏差在后段
        assert back_deviated < front_deviated

    def test_空段跳过不臆造(self):
        """无镜头落入的段不参与距离（diagnostics 注明 shot_count=0）。"""
        result = _evaluate([1500])  # 单镜头相对位置 0.5 → 只落后段
        counts = [s["shot_count"] for s in result.diagnostics["segments"]]
        assert counts == [0, 1]  # 前段为空且被跳过
        assert 0.0 <= result.score <= 1.0


class Test基准纪律:
    @pytest.mark.parametrize("baseline", [None, {}, {"segments": []}])
    def test_缺基准拒绝启动(self, baseline):
        """C7：基准缺失 → 拒绝启动并报错（不允许静默无基准打分）。"""
        with pytest.raises(EditingConfigError, match="基准|pacing_baseline|segments"):
            PacingCurveEvaluator(baseline)

    def test_真实配置装配与评分(self, editing_config):
        """movie.yaml 三段基准（前段留存权重上调）装配可用。"""
        evaluator = PacingCurveEvaluator(editing_config.pacing_baseline)
        artifact = ArtifactRef(
            artifact_hash="cd" * 32, metadata={"shot_durations_ms": [1200, 4000, 2000]}
        )
        result = evaluator.evaluate(artifact, {})
        assert 0.0 <= result.score <= 1.0
        assert result.diagnostics["d_cap"] == 2000000.0

    def test_注册元数据(self):
        spec = PacingCurveEvaluator(_BASELINE).spec
        assert spec.evaluator_id == "proxy.pacing_curve"
        assert spec.kind is EvaluatorKind.PROXY_MODEL
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert "+" in spec.version
