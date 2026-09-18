"""沙箱 IPC 协议单测（US2 / T118）。

契约（contracts/sandbox-ipc.md §1/§2）：stdio JSON Lines 消息 schema、
字段白名单、单消息 1MB 上限、值语义校验、异常→错误码映射。
"""

import pytest

from core.replay.errors import BudgetExhaustedError
from core.replay.errors import ValidationError as ReplayValidationError
from core.sandbox.protocol import (
    MAX_MESSAGE_BYTES,
    ErrorCode,
    ProtocolViolationError,
    decode_message,
    encode_message,
    error_code_for_exception,
    make_error,
    make_result,
    validate_request,
)
from core.tree.errors import NotFoundError
from core.tree.errors import ValidationError as TreeValidationError


class Test编解码:
    def test_往返一致(self):
        msg = {"id": 2, "method": "probe", "params": {"parent_id": "n1", "gen_params": {"t": 0.3}}}
        assert decode_message(encode_message(msg)) == msg

    def test_编码以换行结尾(self):
        assert encode_message({"id": 1, "method": "observed"}).endswith(b"\n")

    def test_编码超限报_ProtocolViolationError(self):
        big = {"id": 1, "method": "observed", "pad": "x" * MAX_MESSAGE_BYTES}
        with pytest.raises(ProtocolViolationError, match="1MB|上限|超"):
            encode_message(big)

    def test_解码超限报_ProtocolViolationError(self):
        with pytest.raises(ProtocolViolationError):
            decode_message(b"x" * (MAX_MESSAGE_BYTES + 1))

    def test_解码非_JSON_报_ProtocolViolationError(self):
        with pytest.raises(ProtocolViolationError):
            decode_message(b"not-json\n")

    def test_解码非字典报_ProtocolViolationError(self):
        with pytest.raises(ProtocolViolationError):
            decode_message(b"[1,2,3]\n")

    def test_值语义_非JSON值拒绝(self):
        """边界：引用/自定义类不可穿越 IPC 边界（值语义）。"""
        with pytest.raises(ProtocolViolationError):
            encode_message({"id": 1, "method": "observed", "obj": object()})


class Test请求schema:
    @pytest.mark.parametrize(
        "msg",
        [
            {"id": 1, "method": "observed"},
            {"id": 2, "method": "probe", "params": {"parent_id": "n", "gen_params": {}}},
            {"id": 3, "method": "shutdown", "params": {"final_node_id": "n"}},
        ],
    )
    def test_合法请求通过(self, msg):
        validate_request(msg)

    @pytest.mark.parametrize(
        "msg",
        [
            {"id": 1, "method": "eval"},  # 白名单外方法
            {"id": 1, "method": "observed", "extra": 1},  # 白名单外字段
            {"method": "observed"},  # 缺 id
            {"id": "1", "method": "observed"},  # id 非整数
            {"id": 1},  # 缺 method
            {"id": 2, "method": "probe"},  # probe 缺 params
            {"id": 2, "method": "probe", "params": {"parent_id": "n"}},  # 缺 gen_params
            {"id": 2, "method": "probe", "params": {"gen_params": {}, "parent_id": 1}},  # 类型错
            {"id": 3, "method": "shutdown"},  # shutdown 缺 final_node_id
        ],
    )
    def test_非法请求拒绝(self, msg):
        with pytest.raises(ProtocolViolationError):
            validate_request(msg)


class Test响应构造:
    def test_make_result(self):
        assert make_result(1, {"nodes": {}}) == {"id": 1, "result": {"nodes": {}}}

    def test_make_error(self):
        resp = make_error(2, ErrorCode.BUDGET_EXCEEDED, "预算耗尽")
        assert resp == {"id": 2, "error": {"code": "budget_exceeded", "message": "预算耗尽"}}


class Test异常映射:
    @pytest.mark.parametrize(
        "exc, code",
        [
            (BudgetExhaustedError("x"), ErrorCode.BUDGET_EXCEEDED),
            (NotFoundError("x"), ErrorCode.NOT_FOUND),
            (TreeValidationError("x"), ErrorCode.VALIDATION),
            (ReplayValidationError("x"), ErrorCode.VALIDATION),
            (ProtocolViolationError("x"), ErrorCode.PROTOCOL_VIOLATION),
            (TimeoutError("x"), ErrorCode.TIMEOUT),
            (RuntimeError("x"), ErrorCode.INTERNAL),
        ],
    )
    def test_映射(self, exc, code):
        assert error_code_for_exception(exc) == code
