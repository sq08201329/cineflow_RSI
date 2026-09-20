"""声音合成评分（C8）：双 gate 短路 + 不适用分量跳过 + 适用权重归一。

与 core/evaluators/composite.py 的差异：声音四评估器存在"类型不适用"语义
（ASR 不评非 TTS、情绪不评非 music），不适用分量跳过并按适用权重归一——
本口径进轮次树 config_snapshot.composite_policy（版本元信息，C8）。
gate 语义沿用 core：rule.* 前缀且 score == 0.0 → 总分 0 短路。
"""

# 合成口径（进 config_snapshot 版本元信息；变更即新口径，需评估兼容性）
COMPOSITE_POLICY = "gate 短路 + 不适用分量跳过 + 适用权重归一 + quantize 6 位定点"

_GATE_PREFIX = "rule."


def _base_key(key: str) -> str:
    return key.rsplit("@", 1)[0] if "@" in key else key


def composite_sound(breakdown: dict[str, dict], weights: dict[str, float]) -> float:
    """声音节点总分合成。

    breakdown：{evaluator_id@version: {"score":…, "diagnostics":{…}}}；
    weights：形态配置数值权重（gate 已映射 0.0）。
    返回未定点化的合成值（定点由调用方 quantize_score 收口）。
    """
    # 1) gate 短路：任一 rule.* 判 0 → 总分 0（不可行解，无视 proxy 得分）
    for key, frag in breakdown.items():
        if _base_key(key).startswith(_GATE_PREFIX) and frag["score"] == 0.0:
            return 0.0
    # 2) 适用分量按权重归一（不适用分量跳过，diagnostics.applicable=False）
    total_weight = 0.0
    accrued = 0.0
    for key, frag in breakdown.items():
        base = _base_key(key)
        if base.startswith(_GATE_PREFIX):
            continue  # gate 权重恒 0，不参与归一
        if not frag.get("diagnostics", {}).get("applicable", True):
            continue  # 类型不适用：跳过并注明（键仍在 eval_breakdown 落盘）
        weight = float(weights[base])
        total_weight += weight
        accrued += weight * frag["score"]
    # 无适用 proxy（如 SFX 工件双 proxy 均不适用）→ 0.0（确定性退化口径）
    return accrued / total_weight if total_weight > 0 else 0.0
