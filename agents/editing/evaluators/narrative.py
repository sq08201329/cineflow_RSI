"""judge 委员会评估器：judge.narrative_flow（C8，澄清 Q1 口径）。

输入 = summary.py 的 EDL 结构化文本摘要（逐镜：序号/时长/转场/分区/音轨标记）；
候选摘要与每个锚点 EDL（configs 内嵌冻结集，经同一摘要函数）摘要做 3 个固定
提示词的成对比较（全部经 LLM 网关计费，temperature=0 Mock 后端哈希种子确定性
投票），胜率均值映射 [0,1]。平局票 0.5 如实记录不二次裁决（决策 6）。

**版本号 = 1.0.0+j{提示词哈希前8}{锚点集前8}{摘要函数前8}**——三者任一变更
即新版本（原则一；摘要函数是 judge 输入的形成环节，与提示词同等入版本）。
"""

import json

import blake3

from agents.editing.edl import EditDecisionList
from agents.editing.summary import summarize_edl, summary_function_hash
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

EVALUATOR_ID = "judge.narrative_flow"


# judge 单票输出的 token 预算（各 judge 共用口径）。为什么不是网关默认 1024：
# ① 判决输出只需一票（A/B/平局），512 足够；② 判决档是非思考模式（thinking=disabled），
# 预算不会被思维链吃光——真实故障：思考模式下 judge 的 1024 预算被思维链耗尽、正文为空。
JUDGE_MAX_TOKENS = 512


def _vote_of(response_text: str) -> float:
    """LLM 响应 → 确定性投票位（Mock 后端下逐字节可复现的解析规则）。

    哈希 % 3：0 → 负（0.0）、1 → 平局（0.5，如实记录不二次裁决）、2 → 胜（1.0）。
    真实后端接入时此函数替换为答案解析（A/B/平局抽取）；解析规则变更属实现
    变更（提示词+锚点+摘要三段哈希之外由调用方口径承担）。
    """
    return {0: 0.0, 1: 0.5, 2: 1.0}[int(blake3.blake3(response_text.encode()).hexdigest(), 16) % 3]


class NarrativeFlowJudgeEvaluator(Evaluator):
    """judge 委员会：3 固定提示词 × 冻结锚点 EDL 集（摘要成对比较，LLM 全过网关）。"""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        model: str,
        prompts: list[str],
        anchor_edls: tuple[EditDecisionList, ...] | list[EditDecisionList],
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._prompts = list(prompts)
        self._anchor_edls = list(anchor_edls)
        # 锚点集经同一摘要函数产出（澄清 Q1：成对比较的对照面是摘要文本）
        self._anchor_summaries = [summarize_edl(a) for a in self._anchor_edls]
        # 版本号三段哈希：提示词 + 锚点集（规范化 EDL JSON）+ 摘要函数（决策 5）
        prompts_blob = blake3.blake3(
            json.dumps(self._prompts, ensure_ascii=False).encode()
        ).hexdigest()[:8]
        anchors_blob = blake3.blake3(
            json.dumps([a.canonical_json() for a in self._anchor_edls], ensure_ascii=False).encode()
        ).hexdigest()[:8]
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=f"1.0.0+j{prompts_blob}{anchors_blob}{summary_function_hash()}",
            kind=EvaluatorKind.JUDGE,
            deterministic=True,
            cost_per_call=0.002,  # 声明性估值（>0 显式）；实际成本经网关按价目表折算入账
        )
        self.last_usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        candidate = summarize_edl(
            context["edl"],
            context.get("shot_library"),
            context.get("scene_structure"),
        )
        votes: list[float] = []
        usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
        for anchor_summary in self._anchor_summaries:
            for prompt in self._prompts:
                result = self._gateway.chat(
                    f"{prompt}\n候选:\n{candidate}\n锚点:\n{anchor_summary}",
                    model=self._model,  # 旧路径兼容；接档案后模型由角色路由决定
                    role=Role.JUDGE,  # 功能 016：LLM judge 委员会角色
                    temperature=0.0,
                    # 判决输出只需一票：显式给足 512 而不是走网关默认 1024；
                    # 判决档为非思考模式（thinking=disabled），预算不会再被思维链吃光
                    max_tokens=JUDGE_MAX_TOKENS,
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
                "win_count": votes.count(1.0),
                "tie_count": votes.count(0.5),  # 平局如实记录（不二次裁决）
                "loss_count": votes.count(0.0),
                "anchor_count": len(self._anchor_summaries),
                "prompt_count": len(self._prompts),
                # 摘要内容寻址入诊断（judge 输入可审计；计费在 last_usage——
                # 缓存命中零成本会致 diagnostics 不一致，破坏重算逐字节一致）
                "summary_hash": blake3.blake3(candidate.encode()).hexdigest(),
                "llm_calls": usage["llm_calls"],
            },
        )
