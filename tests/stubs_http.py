"""本地 stub 真实平台 HTTP 服务端（tests 基建：驱动 B 路径"协议实现"的端到端验证）。

形态：**真实监听** `127.0.0.1:0`（随机端口）的 `ThreadingHTTPServer` 后台线程，
测试结束即关闭；全程只服务 loopback，**不发任何外部网络、不需要真实凭证**。

按 `core/platform_http.py` 的协议回话（三环节共用同一套端点）：

    POST /jobs                    幂等（同 idempotency_key → 同 external_id）
    POST /estimate                只读估价
    GET  /jobs/{id}               状态机逐次推进（默认 pending → running → succeeded）
    GET  /jobs/{id}/artifact      二进制体 + X-Artifact-Meta（base64(JSON)）
    POST /jobs/{id}/cancel
    GET  /health                  只读探测（对齐 ops/check_credentials.py 的 PROBE_PATHS）

可注入的故障（供适配器错误映射用例逐条固化）：`enqueue_status`（429/5xx/4xx）、
`enqueue_latency`（超时：sleep 超过适配器超时）、`enqueue_raw`（响应不可解析）、
`status_sequence`（终态 failed）、`overcharge_usd`（actual > estimated 的账目异常）。

**幂等语义在服务端**：重复提交同键返回首次的 external_id 与同一任务，
适配器侧不自行编造——这是"幂等键原样透传"的验证面。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import blake3

# 金额口径：常量，或依赖输入的计费模型（params → USD）
CostSpec = float | Callable[[dict], float]

# 工件提供者：规范化参数 →（二进制体，元数据 dict）
ArtifactProvider = Callable[[dict], tuple[bytes, dict]]


def resolve_cost(spec: CostSpec, params: dict) -> float:
    """金额口径求解：常量原样返回，计费模型按规范化参数计算。"""
    return float(spec(params)) if callable(spec) else float(spec)


@dataclass
class _Fault:
    """一次性故障（对应 times 次请求）。"""

    kind: str  # status | latency | raw
    times: int = 1
    status: int = 500
    reason: str = "Injected"
    body: bytes = b""
    latency_s: float = 0.0


@dataclass
class StubPlatformState:
    """stub 平台的可变状态（服务端线程与测试线程共用，加锁访问）。

    金额字段可给常量，也可给 `params → float` 的**计费模型**（真实平台的估价/扣费
    依赖输入规模：分镜按镜头数、剪辑按成片时长、视觉按片段规格）——契约套件驱动的
    stub 用后者，才能对任意输入给出自洽的 estimate 与 actual。
    """

    api_key: str = "stub-key"
    estimated_cost_usd: CostSpec = 0.5
    actual_cost_usd: CostSpec | None = None  # None → 取 estimated（取件时入账）
    overcharge_usd: float = 0.0  # >0 即模拟 actual > estimated 的账目异常
    artifact_provider: ArtifactProvider | None = None
    status_sequence: tuple[str, ...] = ("pending", "running", "succeeded")
    error_message: str = "stub：渲染失败（注入）"
    params_hash_mode: str = "platform"  # platform | absent
    omit_artifact_meta: bool = False  # True → 不发 X-Artifact-Meta（元数据缺失路径）
    jobs: dict[str, dict] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)
    requests: list[dict] = field(default_factory=list)
    faults: list[_Fault] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- 故障注入 ----

    def enqueue_status(self, status: int, *, times: int = 1, body: bytes = b"") -> None:
        with self.lock:
            self.faults.append(_Fault("status", times=times, status=status, body=body))

    def enqueue_latency(self, seconds: float, *, times: int = 1) -> None:
        with self.lock:
            self.faults.append(_Fault("latency", times=times, latency_s=seconds))

    def enqueue_raw(self, body: bytes, *, status: int = 200, times: int = 1) -> None:
        """返回不可解析（非 JSON）响应：适配器必须给出可诊断错误而非崩溃。"""
        with self.lock:
            self.faults.append(_Fault("raw", times=times, status=status, body=body))

    def take_fault(self) -> _Fault | None:
        with self.lock:
            for fault in self.faults:
                if fault.times > 0:
                    fault.times -= 1
                    return fault
            return None

    # ---- 断言面 ----

    def paths(self) -> list[str]:
        with self.lock:
            return [record["path"] for record in self.requests]

    def records_of(self, path: str) -> list[dict]:
        with self.lock:
            return [record for record in self.requests if record["path"] == path]

    def job(self, external_id: str) -> dict:
        with self.lock:
            return dict(self.jobs[external_id])

    def job_ids(self) -> list[str]:
        with self.lock:
            return list(self.jobs)

    @property
    def job_count(self) -> int:
        with self.lock:
            return len(self.jobs)

    def artifact_bytes(self, external_id: str) -> bytes:
        return self.job(external_id)["artifact"]


class _StubHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, state: StubPlatformState) -> None:
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.state = state


class _StubHandler(BaseHTTPRequestHandler):
    """协议回话 + 故障注入；全流程 JSON/二进制自定 Content-Length。"""

    server_version = "StubPlatform/1.0"

    @property
    def state(self) -> StubPlatformState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format, *args) -> None:  # noqa: A002 - 静音 HTTP 访问日志
        return

    # ---- 基础收发 ----

    def _write(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端已超时断开（超时用例的正常现象）

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

    def _send_bytes(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _record(self, method: str, path: str, body: bytes = b"") -> None:
        with self.state.lock:
            self.state.requests.append(
                {
                    "method": method,
                    "path": path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )

    def _fault_gate(self) -> bool:
        """应用注入故障；返回 True 表示请求已被故障应答（调用方直接返回）。"""
        fault = self.state.take_fault()
        if fault is None:
            return False
        if fault.kind == "latency":
            time.sleep(fault.latency_s)  # 超过适配器超时 → 连接层超时
            return False
        if fault.kind == "status":
            self._send_bytes(
                fault.status,
                fault.body or json.dumps({"error": "injected"}).encode(),
                {"Content-Type": "application/json"},
            )
            return True
        self._send_bytes(fault.status, fault.body, {"Content-Type": "text/plain"})
        return True

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.state.api_key}"

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self._record("GET", self.path)
        if self._fault_gate():
            return
        if not self._authorized():
            self._send_json(401, {"error": "invalid api key"})
            return
        parts = self.path.strip("/").split("/")
        if parts == ["health"]:
            self._send_json(200, {"status": "ok"})
            return
        if len(parts) == 2 and parts[0] == "jobs":
            self._get_job(parts[1])
            return
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "artifact":
            self._get_artifact(parts[1])
            return
        self._send_json(404, {"error": f"unknown path {self.path}"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = self._read_body()
        self._record("POST", self.path, body)
        if self._fault_gate():
            return
        if not self._authorized():
            self._send_json(401, {"error": "invalid api key"})
            return
        parts = self.path.strip("/").split("/")
        if parts == ["jobs"]:
            self._create_job(body)
            return
        if parts == ["estimate"]:
            self._estimate(body)
            return
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
            self._cancel(parts[1])
            return
        self._send_json(404, {"error": f"unknown path {self.path}"})

    # ---- 端点实现 ----

    def _estimate(self, body: bytes) -> None:
        try:
            params = json.loads(body.decode("utf-8"))["params"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            self._send_json(400, {"error": "body is not json"})
            return
        self._send_json(
            200, {"estimated_cost_usd": resolve_cost(self.state.estimated_cost_usd, params)}
        )

    def _create_job(self, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8"))
            params = payload["params"]
            key = payload["idempotency_key"]
            assert isinstance(key, str) and key
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, AssertionError):
            self._send_json(400, {"error": "body must be {params, idempotency_key}"})
            return
        with self.state.lock:
            existing = self.state.by_key.get(key)
            if existing is not None:  # 幂等：同键返回同一任务（不新建、不重复计费）
                job = self.state.jobs[existing]
                repeat = True
            else:
                external_id = f"stub-{blake3.blake3(key.encode()).hexdigest()[:12]}"
                estimated = resolve_cost(self.state.estimated_cost_usd, params)
                actual_spec = self.state.actual_cost_usd
                actual = estimated if actual_spec is None else resolve_cost(actual_spec, params)
                artifact, meta = (
                    self.state.artifact_provider(params)
                    if self.state.artifact_provider is not None
                    else (b"", {})
                )
                self.state.jobs[external_id] = {
                    "external_id": external_id,
                    "job_id": f"job-{len(self.state.jobs) + 1:04d}",
                    "params": params,
                    "params_hash": blake3.blake3(
                        json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest(),
                    "estimated_cost_usd": estimated,
                    "actual_cost_usd": actual + self.state.overcharge_usd,
                    "booked": False,
                    "step": 0,
                    "status": "pending",
                    "artifact": artifact,
                    "meta": meta,
                }
                self.state.by_key[key] = external_id
                job = self.state.jobs[external_id]
                repeat = False
        response = {
            "external_id": job["external_id"],
            "estimated_cost_usd": job["estimated_cost_usd"],
            "status": job["status"],
            "job_id": job["job_id"],
        }
        if self.state.params_hash_mode == "platform":
            response["params_hash"] = job["params_hash"]
        response["idempotent_repeat"] = repeat
        self._send_json(202, response)

    def _get_job(self, external_id: str) -> None:
        with self.state.lock:
            job = self.state.jobs.get(external_id)
            if job is None:
                self._send_json(404, {"error": f"unknown job {external_id}"})
                return
            sequence = self.state.status_sequence
            if job["step"] < len(sequence):  # 每次查询推进一格（确定性）
                job["status"] = sequence[job["step"]]
                job["step"] += 1
            status = job["status"]
            payload = {
                "status": status,
                "estimated_cost_usd": job["estimated_cost_usd"],
                # 取件前不扣费：actual 以平台为准，未扣费如实 null
                "actual_cost_usd": job["actual_cost_usd"] if job["booked"] else None,
                "error": self.state.error_message if status == "failed" else None,
            }
        self._send_json(200, payload)

    def _get_artifact(self, external_id: str) -> None:
        with self.state.lock:
            job = self.state.jobs.get(external_id)
            if job is None:
                self._send_json(404, {"error": f"unknown job {external_id}"})
                return
            if job["status"] != "succeeded":
                self._send_json(409, {"error": f"artifact not ready（status={job['status']}）"})
                return
            job["booked"] = True  # 取件即入账（同模拟实现"首次取件扣费"口径）
            artifact, meta = job["artifact"], job["meta"]
            omit_meta = self.state.omit_artifact_meta
        encoded = base64.b64encode(json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        headers = {"Content-Type": "video/mp4"}
        if not omit_meta:
            headers["X-Artifact-Meta"] = encoded.decode("ascii")
        self._send_bytes(200, artifact, headers)

    def _cancel(self, external_id: str) -> None:
        with self.state.lock:
            job = self.state.jobs.get(external_id)
            if job is None:
                self._send_json(404, {"error": f"unknown job {external_id}"})
                return
            if job["status"] not in ("succeeded", "failed"):  # 终态为无操作（幂等）
                job["status"] = "failed"
                job["step"] = len(self.state.status_sequence)
            status = job["status"]
        self._send_json(202, {"status": status, "external_id": external_id})


class StubPlatformServer:
    """监听 127.0.0.1 随机端口的 stub 平台（上下文管理器即起停）。"""

    def __init__(self, **state_kwargs: Any) -> None:
        self.state = StubPlatformState(**state_kwargs)
        self._httpd = _StubHttpServer(self.state)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="stub-platform", daemon=True
        )

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def api_key(self) -> str:
        return self.state.api_key

    # ---- 断言面与故障注入（委托给 state，测试侧只面对 server） ----

    def enqueue_status(self, status: int, *, times: int = 1, body: bytes = b"") -> None:
        self.state.enqueue_status(status, times=times, body=body)

    def enqueue_latency(self, seconds: float, *, times: int = 1) -> None:
        self.state.enqueue_latency(seconds, times=times)

    def enqueue_raw(self, body: bytes, *, status: int = 200, times: int = 1) -> None:
        self.state.enqueue_raw(body, status=status, times=times)

    def paths(self) -> list[str]:
        return self.state.paths()

    def records_of(self, path: str) -> list[dict]:
        return self.state.records_of(path)

    def job(self, external_id: str) -> dict:
        return self.state.job(external_id)

    def job_ids(self) -> list[str]:
        return self.state.job_ids()

    @property
    def job_count(self) -> int:
        return self.state.job_count

    def artifact_bytes(self, external_id: str) -> bytes:
        return self.state.artifact_bytes(external_id)

    def start(self) -> StubPlatformServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> StubPlatformServer:
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()
