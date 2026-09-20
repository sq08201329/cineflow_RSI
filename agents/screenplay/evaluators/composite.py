"""剧本合成评分（C11）：四 gate 短路 + 不适用分量跳过 + 适用权重归一 + quantize 定点。

与 core/evaluators/composite.py 的差异：剧本评估编排要求 gate 先行、任一判 0 即短路
不跑 judge（省 LLM 成本，004/007/008 同款纪律）；`judge.dramatic_tension` 存在
"仅大纲阶段适用"语义（非大纲阶段 `diagnostics.applicable=False`），不适用分量跳过并
按适用权重归一（不伪造 0 分拖底）。本口径进轮次树 config_snapshot.composite_policy
（版本元信息，C11）。gate 语义沿用 core 惯例：`rule.*` 前缀且 score == 0.0 → 总分 0。
"""

from core.evaluators.base import ArtifactRef
from core.evaluators.quantize import quantize_score

# 合成口径（进 config_snapshot 版本元信息；变更即新口径，需评估兼容性）
COMPOSITE_POLICY = (
    "四 gate 短路（任一判 0 不跑 judge）+ 不适用分量跳过 + 适用权重归一 + quantize 6 位定点"
)

_GATE_PREFIX = "rule."


def _base_key(key: str) -> str:
    return key.rsplit("@", 1)[0] if "@" in key else key


def _empty_usage() -> dict:
    return {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}


def composite_screenplay(breakdown: dict[str, dict], weights: dict) -> float:
    """剧本节点总分合成。

    breakdown：{evaluator_id@version: {"score":…, "diagnostics":{…}}}；
    weights：evaluator_weights.screenplay 原值（"gate" 字面量或数值权重）。
    1) gate 短路：任一 rule.* 判 0 → 总分 0（不可行解，无视 proxy/judge 得分）；
    2) 适用分量按权重归一：gate 分量不计入加权和；`applicable=False`（judge 非大纲
       阶段等）与缺席分量（gate 短路时 judge 未跑）跳过——分母不含其权重，不伪造 0 分；
    3) 无适用连续分量 → 0.0（确定性退化口径）。
    返回未定点化的合成值（定点由调用方 quantize 收口）。
    """
    for key, fragment in breakdown.items():
        if _base_key(key).startswith(_GATE_PREFIX) and fragment["score"] == 0.0:
            return 0.0
    total_weight = 0.0
    accrued = 0.0
    for key, fragment in breakdown.items():
        base = _base_key(key)
        if base.startswith(_GATE_PREFIX):
            continue  # gate 权重恒 0，不参与归一
        if not fragment.get("diagnostics", {}).get("applicable", True):
            continue  # 不适用分量：跳过并注明（键仍在 eval_breakdown 落盘）
        weight = weights.get(base)
        if weight is None or str(weight).lower() == "gate":
            continue
        weight = float(weight)
        total_weight += weight
        accrued += weight * fragment["score"]
    return accrued / total_weight if total_weight > 0 else 0.0


def evaluate_screenplay(
    evaluators: dict,
    artifact: ArtifactRef,
    context: dict,
    weights: dict,
) -> tuple[dict, float, dict]:
    """七评估器编排（C11）：四 gate 先行 → 任一判 0 短路（两 proxy 照跑、judge 不跑）。

    返回 (breakdown, score, judge_usage)；judge_usage 为网关计费用量
    （llm_calls/llm_tokens/cost_usd），短路时为全 0——调用方入节点成本。
    非大纲阶段的 judge 分量照常评估（入口即标"不适用"、零网关调用），键留在
    breakdown 供合成跳过与审计。
    """
    judge_usage = _empty_usage()
    breakdown: dict[str, dict] = {}
    gate_failed = False
    for gate in evaluators["gates"]:
        result = gate.evaluate(artifact, context)
        breakdown[gate.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
        if result.score == 0.0:
            gate_failed = True
    for proxy in evaluators["proxies"]:
        result = proxy.evaluate(artifact, context)
        breakdown[proxy.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    if gate_failed:
        # gate 短路：合成 0 且不进入 judge（省 LLM 成本；judge 分量缺席不伪造）
        return breakdown, 0.0, judge_usage
    judge = evaluators["judge"]
    result = judge.evaluate(artifact, context)
    breakdown[judge.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    judge_usage = dict(judge.last_usage)
    return breakdown, quantize_score(composite_screenplay(breakdown, weights)), judge_usage
