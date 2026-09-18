"""合规评估器单测（US1 / T207，rule.material_compliance@1.0.0）。

尺寸/时长/文案长度/敏感词拦截；缺配置拒投不放行（边界语义）。
"""

import pytest

from agents.promo.config import PromoConfigError
from agents.promo.evaluators.compliance import MaterialComplianceEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind


def _material(**overrides):
    content = {
        "copy": "光影之间，故事开始。",
        "poster_size": "1080x1920",
        "duration_seconds": 15,
    }
    content.update(overrides)
    return {"content": content, "kind": "copy", "platform": "simulated", "tags": ["剧情"]}


@pytest.fixture()
def evaluator(promo_config):
    return MaterialComplianceEvaluator(promo_config)


class Test注册元数据:
    def test_spec_合规(self, evaluator):
        spec = evaluator.spec
        assert spec.key == "rule.material_compliance@1.0.0"
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True


class Test合规检查:
    def test_合格物料满分(self, evaluator):
        result = evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"material": _material()})
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []

    def test_敏感词拦截(self, evaluator):
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"material": _material(copy="全网第一好看的片子")},
        )
        assert result.score == 0.0
        assert any("敏感词" in v for v in result.diagnostics["violations"])

    def test_文案超长拦截(self, evaluator, promo_config):
        limit = promo_config.material_spec["max_copy_chars"]
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"material": _material(copy="字" * (limit + 1))},
        )
        assert result.score == 0.0

    def test_尺寸不符拦截(self, evaluator):
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"material": _material(poster_size="800x600")},
        )
        assert result.score == 0.0

    def test_时长超限拦截(self, evaluator, promo_config):
        limit = promo_config.material_spec["max_duration_seconds"]
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32),
            {"material": _material(duration_seconds=limit + 1)},
        )
        assert result.score == 0.0

    def test_确定性(self, evaluator):
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        ctx = {"material": _material()}
        assert evaluator.evaluate(artifact, ctx) == evaluator.evaluate(artifact, ctx)


class Test缺配置拒投:
    def test_缺敏感词库_评估器报错(self, promo_config):
        from dataclasses import replace

        broken = replace(promo_config, sensitive_words=[])
        with pytest.raises(PromoConfigError, match="敏感词"):
            MaterialComplianceEvaluator(broken)

    def test_缺物料规格_评估器报错(self, promo_config):
        from dataclasses import replace

        broken = replace(promo_config, material_spec={})
        with pytest.raises(PromoConfigError, match="物料规格"):
            MaterialComplianceEvaluator(broken)
