"""judge 委员会评估器单测（US1 / T314，judge.cinematic）。

3 固定提示词 × 锚点集成对比较投票、胜率映射 [0,1]、
提示词/锚点变更 → 注册键变更（SC-007）、LLM 调用全部经网关计费入账。
"""

import pytest

from agents.visual.evaluators.cinematic import CinematicJudgeEvaluator
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway

PROMPTS = ["哪一段镜头的电影感更强？", "哪一段的叙事张力更好？", "哪一段的画面构图更专业？"]
ANCHOR_HASHES = ["aa" * 32, "bb" * 32]


@pytest.fixture()
def gateway():
    return LLMGateway(
        MockBackend(),
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
    )


@pytest.fixture()
def evaluator(gateway, visual_config):
    return CinematicJudgeEvaluator(
        gateway,
        model="mock-copy-v1",
        prompts=PROMPTS,
        anchor_hashes=ANCHOR_HASHES,
        sampling_spec=visual_config.frame_sampling,
    )


class Test委员会投票:
    def test_投票次数与胜率映射(self, gateway, evaluator):
        result = evaluator.evaluate(ArtifactRef(artifact_hash="cd" * 32), {})
        assert len(result.diagnostics["votes"]) == 3 * len(ANCHOR_HASHES)  # 3 提示词 × 锚点集
        assert 0.0 <= result.score <= 1.0
        wins = sum(result.diagnostics["votes"])
        assert result.score == pytest.approx(wins / len(result.diagnostics["votes"]))

    def test_LLM调用全部经网关入账(self, gateway, evaluator):
        before = gateway.call_count
        evaluator.evaluate(ArtifactRef(artifact_hash="cd" * 32), {})
        assert gateway.call_count - before == 3 * len(ANCHOR_HASHES)
        assert gateway.total_cost_usd > 0
        assert evaluator.last_usage["llm_calls"] == 6

    def test_重算逐字节一致(self, gateway, evaluator):
        artifact = ArtifactRef(artifact_hash="cd" * 32)
        a = evaluator.evaluate(artifact, {})
        # 缓存命中后重算零新调用且得分一致（确定性）
        b = evaluator.evaluate(artifact, {})
        assert a == b


class Test版本冻结:
    def test_提示词变更_注册键变更(self, gateway, visual_config, evaluator):
        """SC-007：提示词文本变更 → 版本号变更（可机检）。"""
        other = CinematicJudgeEvaluator(
            gateway, model="mock-copy-v1",
            prompts=["换一个提示词"] + PROMPTS[1:],
            anchor_hashes=ANCHOR_HASHES,
            sampling_spec=visual_config.frame_sampling,
        )
        assert other.spec.key != evaluator.spec.key

    def test_锚点集变更_注册键变更(self, gateway, visual_config, evaluator):
        other = CinematicJudgeEvaluator(
            gateway, model="mock-copy-v1", prompts=PROMPTS,
            anchor_hashes=ANCHOR_HASHES + ["cc" * 32],
            sampling_spec=visual_config.frame_sampling,
        )
        assert other.spec.key != evaluator.spec.key

    def test_spec_kind与确定性(self, evaluator):
        assert evaluator.spec.evaluator_id == "judge.cinematic"
        assert evaluator.spec.kind is EvaluatorKind.JUDGE
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.version.startswith("1.0.0+")
