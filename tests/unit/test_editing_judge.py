"""judge.narrative_flow 评估器单测（功能 007 / T722，先于实现编写）。

C8（澄清 Q1 口径）：EDL 摘要（T714）× 3 固定提示词 × 冻结锚点 EDL 集成对比较
投票 → 胜率；平局 0.5 如实记录不二次裁决；LLM 全过网关计费（Mock 后端确定性）；
同比较重跑逐位一致；版本号 = 1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}。
"""

import json

import blake3
import pytest

from agents.editing.evaluators.narrative import NarrativeFlowJudgeEvaluator
from agents.editing.summary import summary_function_hash
from core.evaluators.base import ArtifactRef, EvaluatorKind

_MODEL = "mock-copy-v1"


@pytest.fixture()
def ctx(make_edl, make_shot_library, make_scene_structure):
    library = make_shot_library()
    return {
        "edl": make_edl(),
        "shot_library": library,
        "scene_structure": make_scene_structure(library=library),
    }


@pytest.fixture()
def evaluator(mock_gateway, editing_config):
    return NarrativeFlowJudgeEvaluator(
        mock_gateway,
        model=_MODEL,
        prompts=list(editing_config.judge["prompts"]),
        anchor_edls=editing_config.anchor_edls,
    )


def _artifact() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


class Test成对比较投票:
    def test_投票数与网关计费(self, evaluator, mock_gateway, ctx, editing_config):
        """3 提示词 × 2 锚点 = 6 次比较，全部经网关计费入账。"""
        before = mock_gateway.call_count
        result = evaluator.evaluate(_artifact(), ctx)
        expected_calls = len(editing_config.judge["prompts"]) * len(editing_config.anchor_edls)
        assert len(result.diagnostics["votes"]) == expected_calls == 6
        assert mock_gateway.call_count - before == 6  # 网关调用计数（计费路径）
        assert evaluator.last_usage["llm_calls"] == 6
        assert evaluator.last_usage["cost_usd"] > 0  # 按价目表折算（Mock 零外部费用非不计账）

    def test_胜率为投票均值(self, evaluator, ctx):
        result = evaluator.evaluate(_artifact(), ctx)
        votes = result.diagnostics["votes"]
        assert all(v in (0.0, 0.5, 1.0) for v in votes)
        assert result.score == pytest.approx(round(sum(votes) / len(votes), 6))

    def test_平局如实记录(self, evaluator, ctx):
        """胜率 0.5（平局票）不二次裁决：tie_count 与 votes 自洽落盘。"""
        result = evaluator.evaluate(_artifact(), ctx)
        votes = result.diagnostics["votes"]
        assert result.diagnostics["tie_count"] == votes.count(0.5)
        assert result.diagnostics["win_count"] == votes.count(1.0)
        assert result.diagnostics["anchor_count"] == 2
        assert result.diagnostics["prompt_count"] == 3

    def test_同比较重跑逐位一致(self, mock_gateway, editing_config, ctx):
        """C8 场景 5：全新网关 + 全新评估器重跑同比较 → score/diagnostics 逐位一致。"""
        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        def _run():
            gateway = LLMGateway(
                MockBackend(), price_book={_MODEL: {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
                sleep=lambda _: None,
            )
            ev = NarrativeFlowJudgeEvaluator(
                gateway,
                model=_MODEL,
                prompts=list(editing_config.judge["prompts"]),
                anchor_edls=editing_config.anchor_edls,
            )
            result = ev.evaluate(_artifact(), ctx)
            return result.score, result.diagnostics

        assert _run() == _run()

    def test_摘要为_judge_输入(self, evaluator, ctx, make_edl):
        """澄清 Q1：judge 输入 = EDL 摘要——不同 EDL 摘要有别，比较行为随摘要变化。"""
        other = evaluator.evaluate(
            _artifact(), {**ctx, "edl": make_edl("scene_disorder")}
        )
        # 摘要不同 → 提示词载荷不同 → Mock 后端确定性文本不同（投票大概率变化；
        # 至少摘要函数被实际调用——diagnostics 携带候选摘要哈希供审计）
        assert other.diagnostics["summary_hash"] != evaluator.evaluate(
            _artifact(), ctx
        ).diagnostics["summary_hash"]


class Test版本号三段哈希:
    def test_版本号形态与三段构成(self, evaluator, editing_config):
        """C8：版本号 = 1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}。"""
        prompts_blob = blake3.blake3(
            json.dumps(editing_config.judge["prompts"], ensure_ascii=False).encode()
        ).hexdigest()[:8]
        anchors_blob = blake3.blake3(
            json.dumps(
                [a.canonical_json() for a in editing_config.anchor_edls], ensure_ascii=False
            ).encode()
        ).hexdigest()[:8]
        expected = f"1.0.0+j{prompts_blob}{anchors_blob}{summary_function_hash()}"
        assert evaluator.spec.version == expected

    def test_提示词变更即新版本(self, mock_gateway, editing_config):
        other = NarrativeFlowJudgeEvaluator(
            mock_gateway,
            model=_MODEL,
            prompts=["换一个提示词？"],
            anchor_edls=editing_config.anchor_edls,
        )
        assert other.spec.version != evaluator_spec_version(mock_gateway, editing_config)

    def test_注册元数据(self, evaluator):
        """宪章三件套之注册元数据：JUDGE / deterministic / cost_per_call > 0 显式。"""
        spec = evaluator.spec
        assert spec.evaluator_id == "judge.narrative_flow"
        assert spec.kind is EvaluatorKind.JUDGE
        assert spec.deterministic is True
        assert spec.cost_per_call > 0


def evaluator_spec_version(gateway, config) -> str:
    return NarrativeFlowJudgeEvaluator(
        gateway,
        model=_MODEL,
        prompts=list(config.judge["prompts"]),
        anchor_edls=config.anchor_edls,
    ).spec.version
