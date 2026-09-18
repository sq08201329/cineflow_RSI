"""平台真值评估器单测（US1 / T209，human.platform_metrics@1.0.0）。

归一化合成（metric_weights）、越界指标拒绝、human 锚点注册语义（写入即冻结）。
"""

import pytest

from agents.promo.evaluators.platform_metrics import (
    PlatformMetricsEvaluator,
)
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.errors import ValidationError


def _metrics(**overrides):
    snapshot = {
        "ctr": 0.08,
        "completion_rate": 0.6,
        "conversions": 20,
        "impressions": 1000,
        "clicks": 80,
        "platform_timestamp": 1700000000.0,
        "data_version": "v1",
    }
    snapshot.update(overrides)
    return snapshot


@pytest.fixture()
def evaluator(promo_config):
    return PlatformMetricsEvaluator(promo_config.metric_weights, ctr_cap=promo_config.ctr_cap)


class Test注册元数据:
    def test_spec_human锚点(self, evaluator):
        spec = evaluator.spec
        assert spec.key == "human.platform_metrics@1.0.0"
        assert spec.kind is EvaluatorKind.HUMAN
        # human 锚点：唯一允许 deterministic=False 注册的类型（产出写入即冻结为常数）
        assert spec.deterministic is False


class Test归一化合成:
    def test_加权合成(self, evaluator):
        # ctr_n = 0.08/0.2 = 0.4；completion = 0.6；conv_n = (20/1000)/0.05 = 0.4
        # score = 0.5*0.4 + 0.3*0.6 + 0.2*0.4 = 0.46
        result = evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"metrics": _metrics()})
        assert result.score == pytest.approx(0.46)
        assert result.diagnostics["ctr"] == 0.08  # 原始指标保留在 diagnostics

    def test_零曝光转化为零(self, evaluator):
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"metrics": _metrics(conversions=0, impressions=0, clicks=0, ctr=0.0)},
        )
        assert 0.0 <= result.score <= 1.0


class Test越界拒绝:
    @pytest.mark.parametrize(
        "bad",
        [
            {"ctr": 1.01},
            {"ctr": -0.1},
            {"completion_rate": 2.0},
            {"conversions": -1},
            {"impressions": -5},
            {"clicks": -1},
        ],
    )
    def test_越界指标拒绝(self, evaluator, bad):
        """FR-006：异常指标校验拒绝（比率 ∉ [0,1]、负计数）。"""
        with pytest.raises(ValidationError):
            evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"metrics": _metrics(**bad)})

    def test_边界值合法(self, evaluator):
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"metrics": _metrics(ctr=1.0, completion_rate=0.0)},
        )
        assert 0.0 <= result.score <= 1.0
