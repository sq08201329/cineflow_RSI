"""评估器侧领域模型单测（US2 / T021）。

覆盖 data-model.md §1：EvaluatorSpec 必填校验、EvalResult score ∈ [0,1]、
ArtifactRef 哈希格式、Evaluator.compare 默认 NotImplementedError（FR-013）。
"""

from dataclasses import FrozenInstanceError

import pytest

from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.errors import ValidationError
from tests.stubs import StubProxyEvaluator


class TestEvaluatorSpec:
    def _spec_kwargs(self, **overrides):
        fields = {
            "evaluator_id": "rule.x",
            "version": "1.0.0",
            "kind": EvaluatorKind.RULE,
            "deterministic": True,
            "cost_per_call": 0.01,
            "calibration": {},
        }
        fields.update(overrides)
        return fields

    @pytest.mark.parametrize("field", ["evaluator_id", "version"])
    def test_必填字段为空拒构造(self, field):
        with pytest.raises(ValidationError):
            EvaluatorSpec(**self._spec_kwargs(**{field: ""}))

    def test_kind_接受字符串并归一化(self):
        spec = EvaluatorSpec(**self._spec_kwargs(kind="proxy_model"))
        assert spec.kind is EvaluatorKind.PROXY_MODEL

    def test_kind_非法值拒构造(self):
        with pytest.raises(ValidationError):
            EvaluatorSpec(**self._spec_kwargs(kind="not-a-kind"))

    def test_cost_per_call_负数拒构造(self):
        with pytest.raises(ValidationError):
            EvaluatorSpec(**self._spec_kwargs(cost_per_call=-0.1))

    def test_key_为_evaluator_id_at_version(self):
        spec = EvaluatorSpec(**self._spec_kwargs())
        assert spec.key == "rule.x@1.0.0"

    def test_spec_不可变(self):
        spec = EvaluatorSpec(**self._spec_kwargs())
        with pytest.raises(FrozenInstanceError):
            spec.version = "2.0.0"  # type: ignore[misc]

    def test_四类_kind_齐备(self):
        assert {k.value for k in EvaluatorKind} == {"rule", "proxy_model", "judge", "human"}


class TestEvalResult:
    @pytest.mark.parametrize("bad_score", [-0.01, 1.01, 2.0, True])
    def test_score_越界或非法类型拒构造(self, bad_score):
        with pytest.raises(ValidationError):
            EvalResult(score=bad_score)

    @pytest.mark.parametrize("ok_score", [0.0, 0.5, 1.0])
    def test_score_边界合法(self, ok_score):
        assert EvalResult(score=ok_score).score == ok_score

    def test_diagnostics_默认为空字典(self):
        assert EvalResult(score=0.5).diagnostics == {}

    def test_不可变(self):
        result = EvalResult(score=0.5, diagnostics={"note": "可读"})
        with pytest.raises(FrozenInstanceError):
            result.score = 0.6  # type: ignore[misc]


class TestArtifactRef:
    def test_合法构造(self):
        ref = ArtifactRef(artifact_hash="ab" * 32)
        assert ref.metadata == {}

    @pytest.mark.parametrize("bad_hash", ["short", "zz" * 32, "AB" * 32])
    def test_哈希格式非法拒构造(self, bad_hash):
        with pytest.raises(ValidationError):
            ArtifactRef(artifact_hash=bad_hash)


class TestEvaluator基类:
    def test_compare_默认_NotImplementedError(self):
        """FR-013：仅 judge 类实现 compare；其他类型默认不可用。"""
        evaluator = StubProxyEvaluator()
        ref = ArtifactRef(artifact_hash="ab" * 32)
        with pytest.raises(NotImplementedError):
            evaluator.compare(ref, ref, {})

    def test_evaluate_返回合规_EvalResult(self):
        evaluator = StubProxyEvaluator(score=0.8)
        ref = ArtifactRef(artifact_hash="ab" * 32)
        result = evaluator.evaluate(ref, {"round": 1})
        assert 0.0 <= result.score <= 1.0
        assert isinstance(result.diagnostics, dict)

    def test_确定性桩重复调用结果逐字节相同(self):
        evaluator = StubProxyEvaluator(score=0.8)
        ref = ArtifactRef(artifact_hash="ab" * 32)
        assert evaluator.evaluate(ref, {}) == evaluator.evaluate(ref, {})
