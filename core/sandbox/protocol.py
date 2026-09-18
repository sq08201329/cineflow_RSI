"""沙箱 IPC 协议层（contracts/sandbox-ipc.md §1/§2，research 决策 2）。

stdio JSON Lines：每行一条消息，UTF-8，单消息 ≤ 1MB；三条请求
（observed/probe/shutdown），响应分 result/error 两态。只放行 JSON 值语义
数据——引用与自定义类不可穿越边界（序列化逃逸边界情况）。
"""

import json
from enum import StrEnum
from typing import Any

from core.replay.errors import BudgetExhaustedError
from core.replay.errors import ValidationError as ReplayValidationError
from core.tree.errors import NotFoundError
from core.tree.errors import ValidationError as TreeValidationError

MAX_MESSAGE_BYTES = 1_048_576  # 单消息 1MB 上限
REQUEST_TIMEOUT_SECONDS = 30  # 单请求响应超时（超时 → timeout，容器回收）

# 请求方法白名单
METHODS = ("observed", "probe", "shutdown")


class ErrorCode(StrEnum):
    """错误响应码（契约 §1 error.code 枚举）。"""

    BUDGET_EXCEEDED = "budget_exceeded"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    PROTOCOL_VIOLATION = "protocol_violation"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


class ProtocolViolationError(Exception):
    """协议违例：超尺寸、schema 越界、非值语义——本次运行终止（protocol_violation）。"""


def encode_message(msg: dict) -> bytes:
    """编码为 JSON Lines 字节串；非值语义或超 1MB 即违例。"""
    try:
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolViolationError(f"消息必须为 JSON 值语义数据：{exc}") from exc
    if len(payload) + 1 > MAX_MESSAGE_BYTES:
        raise ProtocolViolationError(f"消息超过 1MB 上限（{len(payload)} 字节）")
    return payload + b"\n"


def decode_message(line: bytes) -> dict:
    """解码一行消息；超尺寸/非 JSON/非字典一律违例。"""
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolViolationError(f"消息超过 1MB 上限（{len(line)} 字节）")
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolViolationError(f"消息不是合法 JSON：{exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolViolationError("消息必须是 JSON 对象")
    return msg


def validate_request(msg: dict) -> None:
    """请求 schema 与白名单校验（字段白名单外即违例）。"""
    allowed_keys = {"id", "method", "params"}
    if not set(msg) <= allowed_keys:
        raise ProtocolViolationError(f"请求含白名单外字段：{sorted(set(msg) - allowed_keys)}")
    if not isinstance(msg.get("id"), int) or isinstance(msg.get("id"), bool):
        raise ProtocolViolationError("请求缺少整数 id")
    method = msg.get("method")
    if method not in METHODS:
        raise ProtocolViolationError(f"白名单外方法：{method!r}（允许 {METHODS}）")

    params = msg.get("params")
    if method == "observed":
        if params is not None:
            raise ProtocolViolationError("observed 不接受 params")
    elif method == "probe":
        if not isinstance(params, dict):
            raise ProtocolViolationError("probe 必须携带 params")
        if set(params) != {"parent_id", "gen_params"}:
            raise ProtocolViolationError("probe.params 必须为 parent_id + gen_params")
        if not isinstance(params["parent_id"], str):
            raise ProtocolViolationError("probe.params.parent_id 必须为字符串")
        if not isinstance(params["gen_params"], dict):
            raise ProtocolViolationError("probe.params.gen_params 必须为字典")
    elif method == "shutdown":
        if not isinstance(params, dict) or set(params) != {"final_node_id"}:
            raise ProtocolViolationError("shutdown.params 必须为 final_node_id")
        if not isinstance(params["final_node_id"], str):
            raise ProtocolViolationError("shutdown.params.final_node_id 必须为字符串")


def make_result(request_id: int, result: Any) -> dict:
    return {"id": request_id, "result": result}


def make_error(request_id: int, code: ErrorCode, message: str) -> dict:
    return {"id": request_id, "error": {"code": str(code), "message": message}}


def error_code_for_exception(exc: Exception) -> ErrorCode:
    """宿主侧异常 → 协议错误码映射（超时映射 protocol_violation/timeout 语义见契约）。"""
    if isinstance(exc, BudgetExhaustedError):
        return ErrorCode.BUDGET_EXCEEDED
    if isinstance(exc, NotFoundError):
        return ErrorCode.NOT_FOUND
    if isinstance(exc, ProtocolViolationError):
        return ErrorCode.PROTOCOL_VIOLATION
    if isinstance(exc, TimeoutError):
        return ErrorCode.TIMEOUT
    if isinstance(exc, (TreeValidationError, ReplayValidationError)):
        return ErrorCode.VALIDATION
    return ErrorCode.INTERNAL


def observation_to_dict(obs) -> dict:
    """Observation → 线上值语义字典（cost 展开为纯字典）。"""
    return {
        "node_id": obs.node_id,
        "depth": obs.depth,
        "score": obs.score,
        "cost": {
            "llm_calls": obs.cost.llm_calls,
            "llm_tokens": obs.cost.llm_tokens,
            "generation_api_calls": obs.cost.generation_api_calls,
            "generation_api_cost_usd": obs.cost.generation_api_cost_usd,
            "human_review_minutes": obs.cost.human_review_minutes,
            "wall_clock_seconds": obs.cost.wall_clock_seconds,
        },
        "fields": obs.fields,
    }


def probe_result_to_dict(result) -> dict:
    """ProbeResult → 线上值语义字典；unknown 态零信息。"""
    if result.status == "unknown":
        return {"status": "unknown"}
    return {
        "status": "ok",
        "nodes": [observation_to_dict(obs) for obs in result.nodes],
        "virtual_cost": {
            "llm_calls": result.virtual_cost.llm_calls,
            "llm_tokens": result.virtual_cost.llm_tokens,
            "generation_api_calls": result.virtual_cost.generation_api_calls,
            "generation_api_cost_usd": result.virtual_cost.generation_api_cost_usd,
            "human_review_minutes": result.virtual_cost.human_review_minutes,
            "wall_clock_seconds": result.virtual_cost.wall_clock_seconds,
        },
    }
