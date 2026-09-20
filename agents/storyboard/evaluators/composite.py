"""分镜合成评分（C9）：三 gate 短路 + 不适用分量跳过 + 适用权重归一 + 定点。

与 core/evaluators/composite.py 的差异：分镜评估编排要求 gate 先行、任一判 0 即短路
不跑 judge（省 LLM 成本，004/007 同款纪律）；`proxy.emotion_alignment` 存在"情绪缺失
不适用"语义（diagnostics.applicable=False），不适用分量跳过并按适用权重归一。
本口径进轮次树 config_snapshot.composite_policy（版本元信息，C9）。
gate 语义沿用 core 惯例：rule.* 前缀且 score == 0.0 → 总分 0 短路。
"""

from core.evaluators.base import ArtifactRef
from core.evaluators.quantize import quantize_score

# 合成口径（进 config_snapshot 版本元信息；变更即新口径，需评估兼容性）
COMPOSITE_POLICY = (
    "三 gate 短路（任一判 0 不跑 judge）+ 不适用分量跳过 + 适用权重归一 + quantize 6 位定点"
)

_GATE_PREFIX = "rule."


def _base_key(key: str) -> str:
    return key.rsplit("@", 1)[0] if "@" in key else key


def composite_storyboard(breakdown: dict[str, dict], weights: dict) -> float:
    """分镜节点总分合成。

    breakdown：{evaluator_id@version: {"score":…, "diagnostics":{…}}}；
    weights：evaluator_weights.storyboard 原值（"gate" 字面量或数值权重）。
    1) gate 短路：任一 rule.* 判 0 → 总分 0（不可行解，无视 alignment/judge 得分）；
    2) 适用分量按权重归一：gate 分量不计入加权和；`applicable=False`（情绪缺失等）
       与缺席分量（gate 短路时 judge 未跑）跳过——分母不含其权重，不伪造 0 分拖底；
    3) 无适用连续分量 → 0.0（确定性退化口径）。
    返回未定点化的合成值（定点由调用方 quantize 收口）。
    """
    for key, frag in breakdown.items():
        if _base_key(key).startswith(_GATE_PREFIX) and frag["score"] == 0.0:
            return 0.0
    total_weight = 0.0
    accrued = 0.0
    for key, frag in breakdown.items():
        base = _base_key(key)
        if base.startswith(_GATE_PREFIX):
            continue  # gate 权重恒 0，不参与归一
        if not frag.get("diagnostics", {}).get("applicable", True):
            continue  # 不适用分量：跳过并注明（键仍在 eval_breakdown 落盘）
        weight = weights.get(base)
        if weight is None or str(weight).lower() == "gate":
            continue
        weight = float(weight)
        total_weight += weight
        accrued += weight * frag["score"]
    return accrued / total_weight if total_weight > 0 else 0.0


def evaluate_storyboard(
    evaluators: dict,
    artifact: ArtifactRef,
    context: dict,
    weights: dict,
) -> tuple[dict, float, dict]:
    """五评估器编排（C9）：gate 先行 → 任一判 0 短路（alignment 照跑、judge 不跑）。

    返回 (breakdown, score, judge_usage)；judge_usage 为网关计费用量
    （llm_calls/llm_tokens/cost_usd），短路时全 0——调用方入节点成本。
    """
    judge_usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
    breakdown: dict[str, dict] = {}
    gate_failed = False
    for gate in evaluators["gates"]:
        result = gate.evaluate(artifact, context)
        breakdown[gate.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
        if result.score == 0.0:
            gate_failed = True
    alignment = evaluators["alignment"]
    result = alignment.evaluate(artifact, context)
    breakdown[alignment.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    if gate_failed:
        # gate 短路：合成 0 且不进入 judge（省 LLM 成本；judge 分量缺席不伪造）
        return breakdown, 0.0, judge_usage
    judge = evaluators["judge"]
    result = judge.evaluate(artifact, context)
    breakdown[judge.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    judge_usage = dict(judge.last_usage)
    return breakdown, quantize_score(composite_storyboard(breakdown, weights)), judge_usage
