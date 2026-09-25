"""开发 Agent 合成评分（契约 C10）：两 gate 短路 + 适用权重归一 + quantize 定点 6 位。

镜像 `agents/screenplay/evaluators/composite.py`：两门禁先行，任一判 0 即**短路**——
总分 0 且两代理**不跑也不计费**（本环节代理确定性零成本，纪律仍在：不可行解不消耗调用）；
`rule.*` 判 0 的语义沿用 core 惯例。适用权重归一：缺席（未跑）与 `applicable=False`
的分量跳过，**分母不含其权重**——不伪造 0 分拖底；无适用连续分量 → 0.0（确定性退化口径）。
本口径进轮次树 `config_snapshot.composite_policy`（版本元信息，历史节点不重算）。
"""

from core.evaluators.base import ArtifactRef
from core.evaluators.quantize import quantize_score

# 合成口径（进 config_snapshot 版本元信息；变更即新口径，需评估兼容性）
COMPOSITE_POLICY = (
    "两 gate 短路（rule.* 判 0 → 总分 0、两代理不跑不计费）+ 缺席/不适用分量跳过"
    " + 适用权重归一 + quantize 6 位定点"
)

_GATE_PREFIX = "rule."


def _base_key(key: str) -> str:
    return key.rsplit("@", 1)[0] if "@" in key else key


def _empty_usage() -> dict:
    return {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}


def composite_dev(breakdown: dict[str, dict], weights: dict) -> float:
    """开发节点总分合成。

    breakdown：{evaluator_id@version: {"score":…, "diagnostics":{…}}}；
    weights：evaluator_weights.dev 原值（"gate" 字面量或数值权重）。
    1) gate 短路：任一 rule.* 判 0 → 总分 0（不可行解，无视代理得分）；
    2) 适用分量按权重归一：gate 分量不计入加权和；缺席分量（gate 短路时代理未跑）与
       `applicable=False` 跳过——分母不含其权重，不伪造 0 分；
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


def evaluate_dev(
    evaluators: dict,
    artifact: ArtifactRef,
    context: dict,
    weights: dict,
) -> tuple[dict, float, dict]:
    """两门禁 + 两代理编排（C10）：门禁判 0 即短路——代理不跑、不计费、不落分量键。

    返回 (breakdown, score, 代理计费用量)；用量为确定性代理的网关计费增量
    （llm_calls/llm_tokens/cost_usd 恒 0，但**字段仍落盘**，调用方入节点成本，C12）。
    **槽位未声明**（最小可行形态 C14）：该槽位不产出键 ⇒ 跳过其工作、breakdown 不含该分量、
    用量里 `undeclared_slots` 如实标注（不伪造分量、不补默认）。
    """
    undeclared = [slot for slot in ("gates", "proxies") if slot not in evaluators]
    breakdown: dict[str, dict] = {}
    gate_failed = False
    for gate in evaluators.get("gates", []):
        result = gate.evaluate(artifact, context)
        breakdown[gate.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
        if result.score == 0.0:
            gate_failed = True
    if gate_failed:
        # gate 短路：门禁分量照常全评（诊断点名全部违规项），代理不跑也不计费、不落键
        return breakdown, 0.0, _empty_usage()
    usage = _empty_usage()
    for proxy in evaluators.get("proxies", []):
        result = proxy.evaluate(artifact, context)
        breakdown[proxy.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
        last_usage = getattr(proxy, "last_usage", None)
        if isinstance(last_usage, dict):
            usage["llm_calls"] += int(last_usage.get("llm_calls", 0))
            usage["llm_tokens"] += int(last_usage.get("llm_tokens", 0))
            usage["cost_usd"] += float(last_usage.get("cost_usd", 0.0))
    if undeclared:
        usage["undeclared_slots"] = undeclared
    return breakdown, quantize_score(composite_dev(breakdown, weights)), usage
