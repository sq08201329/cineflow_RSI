"""judge 委员会评估器：judge.cinematic（research 决策 5）。

候选片段与每个锚点做 3 个固定提示词的成对比较（全部经 LLM 网关计费，
temperature=0 Mock 后端哈希种子确定性投票），胜率均值映射 [0,1]。
提示词文本与锚点集哈希进入版本号——变更即新版本（SC-007 可机检）。
"""

import json

import blake3

from agents.visual.evaluators._versioning import implementation_version
from agents.visual.frames import sampling_spec_hash
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score
from core.llm_gateway.gateway import LLMGateway
from core.llm_gateway.routing import Role  # 功能 016：调用角色（路由只在网关）

EVALUATOR_ID = "judge.cinematic"


def _vote_of(response_text: str) -> int:
    """LLM 响应 → 确定性投票位（Mock 后端下逐字节可复现的解析规则）。

    真实后端接入时此函数替换为答案解析（A/B 抽取）；版本号不变更保护
    由提示词+锚点哈希承担，解析规则变更属实现变更（实现哈希兜底）。
    """
    return int(blake3.blake3(response_text.encode()).hexdigest(), 16) % 2


class CinematicJudgeEvaluator(Evaluator):
    """judge 委员会：3 固定提示词 × 冻结锚点集成对比较（LLM 全过网关）。"""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        model: str,
        prompts: list[str],
        anchor_hashes: list[str],
        sampling_spec: dict,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._prompts = list(prompts)
        self._anchor_hashes = list(anchor_hashes)
        # 版本号携带：实现文件 + 采样规格 + 提示词文本 + 锚点集哈希
        anchors_blob = blake3.blake3(json.dumps(sorted(anchor_hashes)).encode()).hexdigest()
        prompts_blob = blake3.blake3(json.dumps(prompts, ensure_ascii=False).encode()).hexdigest()
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                sampling_spec_hash(sampling_spec), prompts_blob, anchors_blob
            ),
            kind=EvaluatorKind.JUDGE,
            deterministic=True,
            cost_per_call=0.0,  # 实际成本经网关按价目表折算（last_usage 可见）
        )
        self.last_usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        votes: list[int] = []
        usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
        for anchor in self._anchor_hashes:
            for prompt in self._prompts:
                result = self._gateway.chat(
                    f"{prompt}\n候选:{artifact.artifact_hash}\n锚点:{anchor}",
                    model=self._model,  # 旧路径兼容；接档案后模型由角色路由决定
                    role=Role.JUDGE,  # 功能 016：LLM judge 委员会角色
                    temperature=0.0,
                )
                votes.append(_vote_of(result.text))
                usage["llm_calls"] += 1
                usage["llm_tokens"] += (
                    result.usage["prompt_tokens"] + result.usage["completion_tokens"]
                )
                usage["cost_usd"] += result.cost_usd
        self.last_usage = usage
        win_rate = sum(votes) / len(votes) if votes else 0.0
        return EvalResult(
            score=quantize_score(win_rate),
            diagnostics={
                "votes": votes,
                "anchor_count": len(self._anchor_hashes),
                "prompt_count": len(self._prompts),
                # 计费用量在 last_usage（缓存命中零成本会致 diagnostics 不一致，
                # 破坏重算逐字节一致——diagnostics 只放确定性内容）
                "llm_calls": usage["llm_calls"],
            },
        )
