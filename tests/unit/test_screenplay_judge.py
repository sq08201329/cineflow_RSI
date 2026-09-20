"""judge.dramatic_tension 评估器单测（功能 009 / T918，先于实现编写）。

C10：输入 = 大纲结构化摘要（summary.summarize_outline，确定性）× 3 固定提示词 × 冻结
锚点大纲集成对比较投票 → 胜率；平局 0.5 如实记录不二次裁决；LLM 全过网关计费
（Mock 后端确定性）；同比较重跑逐位一致。
**仅 outline 阶段参与**：scenes/script 阶段在评估器入口即标"不适用"（applicable=False）
且不调用网关（合成层按适用分量归一，不伪造 0 分拖底）——非大纲阶段复用同一实例时
last_usage 必须归零（不得把大纲阶段的计费带进其他阶段）。
版本号 = 1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}（三段任一变更即新版本，原则一）。
锚点大纲集是**冻结对照面**（不参与门禁：其节拍/页数不满足门禁属预期，本评估器不得
对锚点跑门禁）。
"""

import json

import blake3
import pytest

from agents.screenplay.evaluators.dramatic_tension import DramaticTensionJudgeEvaluator
from agents.screenplay.summary import summary_function_hash
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway

_MODEL = "mock-copy-v1"


def _artifact() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


@pytest.fixture()
def ctx(script_artifact):
    return {"artifact": script_artifact}


@pytest.fixture()
def evaluator(mock_gateway, screenplay_config):
    return DramaticTensionJudgeEvaluator(
        mock_gateway,
        model=screenplay_config.judge["model"],
        prompts=list(screenplay_config.judge["prompts"]),
        anchor_outlines=screenplay_config.anchor_outlines,
    )


class Test成对比较投票:
    def test_投票数与网关计费(self, evaluator, mock_gateway, ctx, screenplay_config):
        """3 提示词 × 2 锚点 = 6 次比较，全部经网关计费入账。"""
        before = mock_gateway.call_count
        result = evaluator.evaluate(_artifact(), ctx)
        expected = len(screenplay_config.judge["prompts"]) * len(screenplay_config.anchor_outlines)
        assert len(result.diagnostics["votes"]) == expected == 6
        assert mock_gateway.call_count - before == 6
        assert evaluator.last_usage["llm_calls"] == 6
        assert evaluator.last_usage["llm_tokens"] > 0
        assert evaluator.last_usage["cost_usd"] > 0  # 按价目表折算（不允许静默零成本）

    def test_胜率为投票均值(self, evaluator, ctx):
        result = evaluator.evaluate(_artifact(), ctx)
        votes = result.diagnostics["votes"]
        assert all(vote in (0.0, 0.5, 1.0) for vote in votes)
        assert result.score == pytest.approx(round(sum(votes) / len(votes), 6))
        assert result.score == round(result.score, 6)  # 定点 6 位

    def test_平局如实记录(self, evaluator, ctx):
        """C10：平局票 0.5 如实记录，不二次裁决。"""
        result = evaluator.evaluate(_artifact(), ctx)
        votes = result.diagnostics["votes"]
        assert result.diagnostics["tie_count"] == votes.count(0.5)
        assert result.diagnostics["win_count"] == votes.count(1.0)
        assert result.diagnostics["loss_count"] == votes.count(0.0)
        assert result.diagnostics["anchor_count"] == 2
        assert result.diagnostics["prompt_count"] == 3
        assert result.diagnostics["applicable"] is True

    def test_摘要是_judge_输入且内容寻址(self, evaluator, ctx, make_script_artifact):
        """C10：judge 输入 = 大纲结构化摘要——摘要变更即摘要哈希变更（可审计）。"""
        base = evaluator.evaluate(_artifact(), ctx)
        other = evaluator.evaluate(
            _artifact(), {"artifact": make_script_artifact(text="另一版大纲正文")}
        )
        assert other.diagnostics["summary_hash"] != base.diagnostics["summary_hash"]
        assert len(other.diagnostics["votes"]) == 6  # 仍全过网关（无短路）

    def test_同比较重跑逐位一致(self, screenplay_config, ctx):
        """C10：全新网关 + 全新评估器重跑同比较 → score/diagnostics 逐位一致。"""

        def _run():
            gateway = LLMGateway(
                MockBackend(),
                price_book={_MODEL: {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
                sleep=lambda _: None,
            )
            evaluator = DramaticTensionJudgeEvaluator(
                gateway,
                model=_MODEL,
                prompts=list(screenplay_config.judge["prompts"]),
                anchor_outlines=screenplay_config.anchor_outlines,
            )
            result = evaluator.evaluate(_artifact(), ctx)
            return result.score, result.diagnostics

        assert _run() == _run()


class Test仅大纲阶段参与:
    """C10 / research 决策 2：judge 只作用于大纲阶段；其他阶段"不适用"由合成层归一。"""

    @pytest.mark.parametrize("stage", ["scenes", "script"])
    def test_非大纲阶段不适用且零调用(self, evaluator, mock_gateway, make_script_artifact, stage):
        before = mock_gateway.call_count
        result = evaluator.evaluate(_artifact(), {"artifact": make_script_artifact(stage=stage)})
        assert result.diagnostics["applicable"] is False
        assert stage in result.diagnostics["note"]
        assert result.diagnostics["stage"] == stage
        assert mock_gateway.call_count == before  # 未调用网关（不产生 judge 费用）
        assert evaluator.last_usage == {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}

    def test_复用实例时计费用量归零(self, evaluator, ctx, make_script_artifact):
        """先评大纲（有计费）再评非大纲：last_usage 必须归零（不得把费用带进其他阶段）。"""
        evaluator.evaluate(_artifact(), ctx)
        assert evaluator.last_usage["llm_calls"] == 6
        evaluator.evaluate(_artifact(), {"artifact": make_script_artifact(stage="script")})
        assert evaluator.last_usage == {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}


class Test版本号三段哈希:
    def test_版本号形态与三段构成(self, evaluator, screenplay_config):
        """C10：版本号 = 1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}。"""
        prompts_blob = blake3.blake3(
            json.dumps(screenplay_config.judge["prompts"], ensure_ascii=False).encode()
        ).hexdigest()[:8]
        anchors_blob = blake3.blake3(
            json.dumps(
                [anchor.canonical_json() for anchor in screenplay_config.anchor_outlines],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:8]
        expected = f"1.0.0+j{prompts_blob}{anchors_blob}{summary_function_hash()}"
        assert evaluator.spec.version == expected

    def test_提示词变更即新版本(self, mock_gateway, screenplay_config, evaluator):
        other = DramaticTensionJudgeEvaluator(
            mock_gateway,
            model=_MODEL,
            prompts=["换一个提示词？"],
            anchor_outlines=screenplay_config.anchor_outlines,
        )
        assert other.spec.version != evaluator.spec.version

    def test_锚点集变更即新版本(self, mock_gateway, screenplay_config, evaluator):
        other = DramaticTensionJudgeEvaluator(
            mock_gateway,
            model=_MODEL,
            prompts=list(screenplay_config.judge["prompts"]),
            anchor_outlines=[screenplay_config.anchor_outlines[0]],
        )
        assert other.spec.version != evaluator.spec.version

    def test_摘要函数哈希入版本(self, evaluator):
        assert evaluator.spec.version.endswith(summary_function_hash())

    def test_注册元数据(self, evaluator):
        """宪章三件套之注册元数据：JUDGE / deterministic / cost_per_call > 0 显式。"""
        spec = evaluator.spec
        assert spec.evaluator_id == "judge.dramatic_tension"
        assert spec.kind is EvaluatorKind.JUDGE
        assert spec.deterministic is True
        assert spec.cost_per_call > 0


class Test锚点为冻结对照面:
    def test_锚点不参与门禁(self, evaluator, screenplay_config):
        """锚点大纲是对照面（结构紧凑，不满足门禁要求属预期）：judge 只做摘要比较。"""
        anchor = screenplay_config.anchor_outlines[0]
        assert len(anchor.beat_ids()) == 3  # 锚点节拍远少于门禁 required 清单
        assert anchor.stage == "outline"
        result = evaluator.evaluate(_artifact(), {"artifact": anchor})
        assert result.diagnostics["applicable"] is True
        assert len(result.diagnostics["votes"]) == 6
