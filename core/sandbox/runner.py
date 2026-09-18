"""沙箱执行入口（contracts/sandbox-ipc.md §3，T126）。

执行顺序（强制）：静态检查 → 版本落盘 → 起容器 → IPC 桥接 → 回收。
策略进程内不存在模拟器对象：宿主侧 IOHandler 把容器经 stdio 发来的
IPC 请求转发给进程内 ReplaySimulator，响应填充至时延量子整数倍后发出。
"""

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.replay.simulator import ReplaySimulator, pad_to_quantum
from core.replay.trajectory import ReplayTrajectory, TrajectoryStatus
from core.sandbox.protocol import (
    ErrorCode,
    ProtocolViolationError,
    decode_message,
    encode_message,
    error_code_for_exception,
    make_error,
    make_result,
    observation_to_dict,
    probe_result_to_dict,
    validate_request,
)
from policies.static_check import StaticCheckError, check_policy_source
from policies.versioning import policy_version, record_policy


@dataclass(frozen=True)
class RunLimits:
    """资源限额（data-model §3）。"""

    cpu: float = 1.0
    memory_mb: int = 256
    pids: int = 64
    wall_clock_seconds: int = 120
    ipc_msg_max_bytes: int = 1_048_576
    latency_quantum_ms: int = 50


class RunStatus(StrEnum):
    """沙箱运行结局。"""

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    POLICY_ERROR = "policy_error"
    PROTOCOL_VIOLATION = "protocol_violation"
    RESOURCE_EXCEEDED = "resource_exceeded"


@dataclass(frozen=True)
class RunResult:
    """沙箱运行结果：轨迹（成功时）+ 截断诊断。"""

    status: RunStatus
    trajectory: ReplayTrajectory | None = None
    stderr_tail: str = ""


@dataclass
class BackendOutcome:
    """后端（容器生命周期）层面的运行结果，由 runner 归并为 RunResult。"""

    status: RunStatus
    stderr_tail: str = ""


class IOHandler:
    """宿主侧 IPC 桥：请求校验 → 模拟器转发 → 响应序列化 → 量子填充。

    response_times_ms 是计时侧信道回归的观测点（对抗测试断言量子化）。
    """

    def __init__(self, simulator: ReplaySimulator, limits: RunLimits) -> None:
        self._simulator = simulator
        self._limits = limits
        self.response_times_ms: list[float] = []
        self.final_node_id: str | None = None
        self.violation: str | None = None
        self.budget_exceeded = False

    @property
    def budget(self):
        return self._simulator.budget

    def handle(self, line: bytes) -> tuple[bytes | None, bool]:
        """处理一条请求行，返回 (响应字节 | None, 是否终止本次运行)。"""
        start = time.perf_counter()
        response, stop = self._dispatch(line)
        pad_to_quantum(start, self._limits.latency_quantum_ms)
        self.response_times_ms.append((time.perf_counter() - start) * 1000)
        return response, stop

    def _dispatch(self, line: bytes) -> tuple[bytes | None, bool]:
        try:
            msg = decode_message(line)
            validate_request(msg)
        except ProtocolViolationError as exc:
            # 协议违例：回错误帧后终止本次运行（契约 §2）
            self.violation = str(exc)
            return encode_message(make_error(0, ErrorCode.PROTOCOL_VIOLATION, str(exc))), True

        request_id = msg["id"]
        method = msg["method"]
        if method == "observed":
            nodes = self._simulator.observed()
            result = {"nodes": {nid: observation_to_dict(o) for nid, o in nodes.items()}}
            return encode_message(make_result(request_id, result)), False
        if method == "probe":
            params = msg["params"]
            try:
                probe_result = self._simulator.probe(params["parent_id"], params["gen_params"])
            except Exception as exc:  # noqa: BLE001 - 统一映射为协议错误帧
                code = error_code_for_exception(exc)
                if code is ErrorCode.BUDGET_EXCEEDED:
                    self.budget_exceeded = True
                return encode_message(make_error(request_id, code, str(exc))), False
            return encode_message(
                make_result(request_id, probe_result_to_dict(probe_result))
            ), False
        # shutdown：策略以最终 node_id 结束，ack 后终止
        self.final_node_id = msg["params"]["final_node_id"]
        return encode_message(make_result(request_id, {"final_node_id": self.final_node_id})), True


def create_io_handler(simulator: ReplaySimulator, limits: RunLimits) -> IOHandler:
    """装配 IPC 桥（对抗测试直接驱动后端时用）。"""
    return IOHandler(simulator, limits)


def run_policy(
    policy_source: str,
    simulator: ReplaySimulator,
    limits: RunLimits,
    backend,
    *,
    history_root: Path | str = "policies/history",
) -> RunResult:
    """沙箱回放入口：静态检查 → 版本落盘 → 容器执行 → 轨迹归并。"""
    version = policy_version(policy_source)

    # 第一道防线：静态检查不过 → 不起容器，直接 policy_error
    try:
        check_policy_source(policy_source)
    except StaticCheckError as exc:
        simulator.finalize(
            policy_version=version,
            final_node_id=None,
            status=TrajectoryStatus.POLICY_ERROR,
            diagnostics={"stage": "static_check", "error": str(exc)},
        )
        return RunResult(
            status=RunStatus.POLICY_ERROR,
            trajectory=simulator.trajectory(),
            stderr_tail=f"静态检查未通过：{exc}"[-2000:],
        )

    # 版本固定：BLAKE3 前 12 位，幂等落盘 policies/history/{agent_id}/{version}.py
    record_policy(policy_source, simulator.agent_id or "unknown", history_root=history_root)

    handler = IOHandler(simulator, limits)
    outcome = backend.run(policy_source, handler, limits)

    # 归并结局：协议违例优先；预算耗尽记入轨迹结局
    if handler.violation is not None:
        status = RunStatus.PROTOCOL_VIOLATION
    else:
        status = outcome.status
    if status is RunStatus.COMPLETED:
        trajectory_status = TrajectoryStatus.COMPLETED
    elif status is RunStatus.TIMEOUT:
        trajectory_status = TrajectoryStatus.TIMEOUT
    elif handler.budget_exceeded:
        trajectory_status = TrajectoryStatus.BUDGET_EXCEEDED
    else:
        trajectory_status = TrajectoryStatus.POLICY_ERROR

    simulator.finalize(
        policy_version=version,
        final_node_id=handler.final_node_id,
        status=trajectory_status,
        diagnostics={
            "run_status": status.value,
            "protocol_violation": handler.violation,
        },
    )
    return RunResult(
        status=status, trajectory=simulator.trajectory(), stderr_tail=outcome.stderr_tail
    )
