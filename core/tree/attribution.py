"""角色 × 档案成本分解归集薄工具（功能 022 / plan D4）。

纯函数、零网关依赖（原则五：core/tree 不 import core/llm_gateway）；
归属字符串（role / profile_id）由调用侧传入，本模块只做结构校验，不耦合 Role 枚举。
分解形态对齐网关 cost_breakdown() 口径：
`{role: {profile_id: {calls, prompt_tokens, completion_tokens, cost_usd}}}`。
"""

from core.tree.errors import ValidationError

_ENTRY_KEYS = ("calls", "prompt_tokens", "completion_tokens", "cost_usd")


def _empty_entry() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}


def _copy_breakdown(breakdown: dict) -> dict:
    """深拷贝分解映射（嵌套三层），保证纯函数不改入参。"""
    return {
        role: {profile_id: dict(entry) for profile_id, entry in profiles.items()}
        for role, profiles in breakdown.items()
    }


def _require_attribution(role: str, profile_id: str) -> None:
    for name, value in (("role", role), ("profile_id", profile_id)):
        if not isinstance(value, str) or not value:
            raise ValidationError(
                f"attribution.{name} 必须为非空字符串（FR-004），实际为 {value!r}"
            )


def _require_non_negative(value, field_name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"attribution.{field_name} 必须为 ≥ 0 的数值，实际为 {value!r}")


def add_call(
    breakdown: dict,
    *,
    role: str,
    profile_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> dict:
    """向分解映射累入一次非缓存 LLM 调用，返回新映射（不改入参）。

    缓存命中（LLMResult.cached=True，零计费）的调用由调用侧跳过，本工具不复查；
    失败/预估入账按 plan D4 由调用侧传 tokens=0、cost_usd=已发生/预估额。
    """
    _require_attribution(role, profile_id)
    _require_non_negative(prompt_tokens, "prompt_tokens")
    _require_non_negative(completion_tokens, "completion_tokens")
    _require_non_negative(cost_usd, "cost_usd")
    result = _copy_breakdown(breakdown)
    entry = result.setdefault(role, {}).setdefault(profile_id, _empty_entry())
    entry["calls"] += 1
    entry["prompt_tokens"] += prompt_tokens
    entry["completion_tokens"] += completion_tokens
    entry["cost_usd"] += cost_usd
    return result


def merge(a: dict, b: dict) -> dict:
    """合并两个分解映射：同键四分量逐项累加，返回新映射（不改入参）。

    供聚合一节点多次调用与 ReplayTrajectory.total_cost 汇总（plan D4）。
    """
    result = _copy_breakdown(a)
    for role, profiles in b.items():
        for profile_id, entry in profiles.items():
            target = result.setdefault(role, {}).setdefault(profile_id, _empty_entry())
            for key in _ENTRY_KEYS:
                target[key] += entry[key]
    return result
