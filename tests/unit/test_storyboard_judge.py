"""judge.script_fit 评估器单测（功能 008 / T822，先于实现编写）。

C8：ShotList 结构化摘要（summary.summarize_shotlist，确定性）× 3 固定提示词 ×
冻结锚点 ShotList 集成对比较投票 → 胜率；平局 0.5 如实记录不二次裁决；LLM 全过
网关计费（Mock 后端确定性）；同比较重跑逐位一致；
版本号 = 1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}（三段任一变更即新版本，原则一）。
"""

import json

import blake3
import pytest

from agents.storyboard.evaluators.script_fit import ScriptFitJudgeEvaluator
from agents.storyboard.summary import summarize_shotlist, summary_function_hash
from core.evaluators.base import ArtifactRef, EvaluatorKind

_MODEL = "mock-copy-v1"


@pytest.fixture()
def ctx(make_shotlist, make_script_segment):
    return {"shotlist": make_shotlist(), "script": make_script_segment()}


@pytest.fixture()
def evaluator(mock_gateway, storyboard_config):
    return ScriptFitJudgeEvaluator(
        mock_gateway,
        model=storyboard_config.judge["model"],
        prompts=list(storyboard_config.judge["prompts"]),
        anchor_shotlists=storyboard_config.anchor_shotlists,
    )


def _artifact() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


class Test成对比较投票:
    def test_投票数与网关计费(self, evaluator, mock_gateway, ctx, storyboard_config):
        """3 提示词 × 2 锚点 = 6 次比较，全部经网关计费入账。"""
        before = mock_gateway.call_count
        result = evaluator.evaluate(_artifact(), ctx)
        expected = len(storyboard_config.judge["prompts"]) * len(storyboard_config.anchor_shotlists)
        assert len(result.diagnostics["votes"]) == expected == 6
        assert mock_gateway.call_count - before == 6
        assert evaluator.last_usage["llm_calls"] == 6
        assert evaluator.last_usage["cost_usd"] > 0  # 按价目表折算（不允许静默零成本）

    def test_胜率为投票均值(self, evaluator, ctx):
        result = evaluator.evaluate(_artifact(), ctx)
        votes = result.diagnostics["votes"]
        assert all(v in (0.0, 0.5, 1.0) for v in votes)
        assert result.score == pytest.approx(round(sum(votes) / len(votes), 6))
        assert result.score == round(result.score, 6)  # 定点 6 位

    def test_平局如实记录(self, evaluator, ctx):
        """C8：平局票 0.5 如实记录，不二次裁决。"""
        result = evaluator.evaluate(_artifact(), ctx)
        votes = result.diagnostics["votes"]
        assert result.diagnostics["tie_count"] == votes.count(0.5)
        assert result.diagnostics["win_count"] == votes.count(1.0)
        assert result.diagnostics["loss_count"] == votes.count(0.0)
        assert result.diagnostics["anchor_count"] == 2
        assert result.diagnostics["prompt_count"] == 3

    def test_同比较重跑逐位一致(self, mock_gateway, storyboard_config, ctx):
        """C8：全新网关 + 全新评估器重跑同比较 → score/diagnostics 逐位一致。"""
        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        def _run():
            gateway = LLMGateway(
                MockBackend(),
                price_book={_MODEL: {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
                sleep=lambda _: None,
            )
            evaluator = ScriptFitJudgeEvaluator(
                gateway,
                model=_MODEL,
                prompts=list(storyboard_config.judge["prompts"]),
                anchor_shotlists=storyboard_config.anchor_shotlists,
            )
            result = evaluator.evaluate(_artifact(), ctx)
            return result.score, result.diagnostics

        assert _run() == _run()

    def test_摘要为_judge_输入(self, evaluator, ctx, make_shotlist):
        """C8：judge 输入 = ShotList 摘要（× 剧本段落）——摘要不同则判据可审计。"""
        base = evaluator.evaluate(_artifact(), ctx)
        other = evaluator.evaluate(
            _artifact(),
            {**ctx, "shotlist": make_shotlist("size_out_of_range")},
        )
        assert other.diagnostics["summary_hash"] != base.diagnostics["summary_hash"]

    def test_剧本段落影响候选摘要(self, evaluator, ctx, make_script_segment):
        """剧本段落（关键行/情绪）进入候选摘要 → 摘要哈希随之变化（诚实输入）。"""
        base = evaluator.evaluate(_artifact(), ctx)
        other_script = make_script_segment()
        scenes = [
            {
                "scene_id": scene.scene_id,
                "axis_base": scene.axis_base,
                "lines": [
                    {**line.to_dict(), "emotion": "awe" if line.emotion else None}
                    for line in scene.lines
                ],
            }
            for scene in other_script.scenes
        ]
        from agents.storyboard.script import ScriptSegment

        shifted = evaluator.evaluate(_artifact(), {**ctx, "script": ScriptSegment(scenes=scenes)})
        assert shifted.diagnostics["summary_hash"] != base.diagnostics["summary_hash"]
        assert len(shifted.diagnostics["votes"]) == 6  # 仍然全过网关（无短路）


class Test版本号三段哈希:
    def test_版本号形态与三段构成(self, evaluator, storyboard_config):
        """C8：版本号 = 1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}。"""
        prompts_blob = blake3.blake3(
            json.dumps(storyboard_config.judge["prompts"], ensure_ascii=False).encode()
        ).hexdigest()[:8]
        anchors_blob = blake3.blake3(
            json.dumps(
                [a.canonical_json() for a in storyboard_config.anchor_shotlists],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:8]
        expected = f"1.0.0+j{prompts_blob}{anchors_blob}{summary_function_hash()}"
        assert evaluator.spec.version == expected

    def test_提示词变更即新版本(self, mock_gateway, storyboard_config, evaluator):
        other = ScriptFitJudgeEvaluator(
            mock_gateway,
            model=_MODEL,
            prompts=["换一个提示词？"],
            anchor_shotlists=storyboard_config.anchor_shotlists,
        )
        assert other.spec.version != evaluator.spec.version

    def test_锚点集变更即新版本(self, mock_gateway, storyboard_config, evaluator):
        other = ScriptFitJudgeEvaluator(
            mock_gateway,
            model=_MODEL,
            prompts=list(storyboard_config.judge["prompts"]),
            anchor_shotlists=[storyboard_config.anchor_shotlists[0]],
        )
        assert other.spec.version != evaluator.spec.version

    def test_摘要函数哈希入版本(self, evaluator):
        assert evaluator.spec.version.endswith(summary_function_hash())

    def test_注册元数据(self, evaluator):
        """宪章三件套之注册元数据：JUDGE / deterministic / cost_per_call > 0 显式。"""
        spec = evaluator.spec
        assert spec.evaluator_id == "judge.script_fit"
        assert spec.kind is EvaluatorKind.JUDGE
        assert spec.deterministic is True
        assert spec.cost_per_call > 0


class Test锚点摘要口径:
    def test_锚点集经同一摘要函数产出(self, storyboard_config):
        """锚点 ShotList（无剧本）经同一摘要函数产出：候选/锚点文本口径一致。"""
        anchor = storyboard_config.anchor_shotlists[0]
        text = summarize_shotlist(anchor)
        assert "shot-anchor-a1" in text
        assert "景别" in text
