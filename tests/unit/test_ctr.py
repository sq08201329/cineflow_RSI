"""CTR 历史代理评估器单测（US1 / T208，proxy.ctr_history）。

分桶贝塔平滑、稀疏桶回退先验、快照哈希版本化（1.0.0+<哈希前12位>）、确定性。
"""

import pytest

from agents.promo.evaluators.ctr import CtrHistoryEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind

HISTORY = [
    # 桶（simulated × copy × [剧情]）：CTR 真值 40/1000 = 0.04
    {"platform": "simulated", "kind": "copy", "tags": ["剧情"], "impressions": 1000, "clicks": 40},
    # 桶（simulated × poster_params × [喜剧]）：60/2000 = 0.03
    {
        "platform": "simulated",
        "kind": "poster_params",
        "tags": ["喜剧"],
        "impressions": 2000,
        "clicks": 60,
    },
]


def _material(kind="copy", tags=("剧情",)):
    return {"content": {"copy": "x"}, "kind": kind, "platform": "simulated", "tags": list(tags)}


@pytest.fixture()
def evaluator(promo_config):
    return CtrHistoryEvaluator(
        HISTORY, ctr_prior=promo_config.ctr_prior, ctr_cap=promo_config.ctr_cap
    )


class Test分桶贝塔平滑:
    def test_桶内估计(self, evaluator):
        """(clicks + α) / (impressions + α + β) = 42/1042，再按 ctr_cap 归一化。"""
        result = evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"material": _material()})
        expected_ctr = (40 + 2.0) / (1000 + 2.0 + 40.0)
        assert result.diagnostics["ctr"] == pytest.approx(expected_ctr)
        assert result.score == pytest.approx(min(1.0, expected_ctr / 0.2))

    def test_稀疏桶回退先验(self, evaluator):
        """无历史桶：回退全局先验 α/(α+β)。"""
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"material": _material(kind="copy", tags=("未知题材",))},
        )
        prior = 2.0 / (2.0 + 40.0)
        assert result.diagnostics["ctr"] == pytest.approx(prior)
        assert result.diagnostics["fallback"] == "prior"

    def test_分桶键含平台类型标签(self, evaluator):
        """标签集合不同 = 不同桶（顺序无关）。"""
        a = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"material": _material(tags=("剧情",))}
        )
        b = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"material": _material(tags=("喜剧",))}
        )
        assert a.diagnostics["ctr"] != b.diagnostics["ctr"]

    def test_标签顺序无关(self, promo_config):
        history = [
            {
                "platform": "simulated",
                "kind": "copy",
                "tags": ["剧情", "院线"],
                "impressions": 100,
                "clicks": 10,
            }
        ]
        ev = CtrHistoryEvaluator(
            history, ctr_prior=promo_config.ctr_prior, ctr_cap=promo_config.ctr_cap
        )
        a = ev.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"material": _material(tags=("剧情", "院线"))}
        )
        b = ev.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"material": _material(tags=("院线", "剧情"))}
        )
        assert a.diagnostics["ctr"] == b.diagnostics["ctr"]


class Test快照版本化与确定性:
    def test_版本号携带快照哈希(self, evaluator):
        assert evaluator.spec.evaluator_id == "proxy.ctr_history"
        assert evaluator.spec.version.startswith("1.0.0+")
        assert len(evaluator.spec.version) == len("1.0.0+") + 12

    def test_数据变即版本变(self, promo_config, evaluator):
        other = CtrHistoryEvaluator(
            HISTORY
            + [
                {
                    "platform": "simulated",
                    "kind": "copy",
                    "tags": ["剧情"],
                    "impressions": 10,
                    "clicks": 1,
                }
            ],
            ctr_prior=promo_config.ctr_prior,
            ctr_cap=promo_config.ctr_cap,
        )
        assert other.spec.version != evaluator.spec.version

    def test_同数据同版本(self, promo_config, evaluator):
        same = CtrHistoryEvaluator(
            list(HISTORY), ctr_prior=promo_config.ctr_prior, ctr_cap=promo_config.ctr_cap
        )
        assert same.spec.version == evaluator.spec.version

    def test_确定性重复评估一致(self, evaluator):
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        ctx = {"material": _material()}
        assert evaluator.evaluate(artifact, ctx) == evaluator.evaluate(artifact, ctx)

    def test_spec_kind与确定性(self, evaluator):
        assert evaluator.spec.kind is EvaluatorKind.PROXY_MODEL
        assert evaluator.spec.deterministic is True
