"""生成参数规范化与精确匹配（research 决策 5，FR-003）。

约定：历史节点的生成参数记录在 `observation_context["gen_params"]`（回放重建依据，
见 001 data-model）；probe 的 gen_params 与之做规范化 JSON 精确相等比较——
键排序递归规范化后逐字节相等才算匹配；不做数值容差、不做缺省补齐，
缺字段即不匹配（UNKNOWN）。
"""

import json

from core.replay.errors import ValidationError
from core.tree.models import TreeNode

# 生成参数在 observation_context 中的记录键
GEN_PARAMS_KEY = "gen_params"


def normalize_params(params: dict) -> str:
    """规范化 JSON：键递归排序、紧凑分隔符——同语义参数必得同字符串。"""
    if not isinstance(params, dict):
        raise ValidationError(f"gen_params 必须为 dict，实际为 {type(params).__name__}")
    try:
        return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"gen_params 必须为 JSON 值语义数据：{exc}") from exc


def params_match(recorded: dict, requested: dict) -> bool:
    """规范化后精确相等比较（无模糊匹配、无插值）。"""
    return normalize_params(recorded) == normalize_params(requested)


def node_gen_params(node: TreeNode) -> dict:
    """取历史节点的生成参数（缺省为空 dict——与任何非空参数都不匹配）。"""
    params = node.observation_context.get(GEN_PARAMS_KEY, {})
    return params if isinstance(params, dict) else {}
