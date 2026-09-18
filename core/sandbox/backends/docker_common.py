"""Docker 容器后端的公共实现：加固旗标、命令组装、stdio 泵与容器回收。

隔离语义（逐条对应宪章原则四与契约 §3）：
- --network=none：无网络；
- --read-only + 只读挂载 /work + 小型 tmpfs /tmp：文件系统只读；
- --cap-drop=ALL + no-new-privileges：无提权面；
- seccomp：Docker 默认 seccomp 配置始终生效（不显式放宽即为强制）；
- --memory/--cpus/--pids-limit：资源限额；
- 不注入任何对象存储凭证环境变量（唯一注入的是 PYTHONDONTWRITEBYTECODE）；
- 素材卷一期不挂载（二期接入素材库时再定义，届时一律只读）；
- 结束/超时/违例一律 docker rm -f 强制回收，不留孤儿容器。
"""

import subprocess
import threading
import time
import uuid
from pathlib import Path

from core.sandbox.protocol import REQUEST_TIMEOUT_SECONDS
from core.sandbox.runner import BackendOutcome, RunLimits, RunStatus

CONTAINER_PREFIX = "cineflow-sandbox-"
POLICY_SIDE_SOURCE = Path(__file__).resolve().parent.parent / "policy_side.py"


def security_flags(limits: RunLimits) -> list[str]:
    """加固旗标（隔离语义的直接载体，对抗测试逐条断言）。"""
    return [
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--memory={limits.memory_mb}m",
        f"--cpus={limits.cpu}",
        f"--pids-limit={limits.pids}",
    ]


def build_command(
    image: str, name: str, workdir: Path, limits: RunLimits, extra_flags: list[str]
) -> list[str]:
    """组装 docker run 命令：加固旗标 + 只读工作挂载 + stdio 交互。"""
    return [
        "docker",
        "run",
        "-i",
        "--rm",
        f"--name={name}",
        *security_flags(limits),
        *extra_flags,
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",  # 只读根 fs 下禁写字节码；非凭证，唯一注入的 env
        "-v",
        f"{workdir}:/work:ro",
        image,
        "python",
        "/work/policy_side.py",
        "/work/policy.py",
        "/work/budget.json",
    ]


def _force_remove(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def run_container(command: list[str], name: str, handler, limits: RunLimits) -> BackendOutcome:
    """起容器并泵 stdio IPC：读请求行 → handler → 写响应行；超时/违例一律回收。

    读者线程阻塞读 stdout 行，主线程以超时从队列取行（单请求 30s、总墙钟限额）。
    """
    import queue

    proc = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    lines: queue.Queue[bytes] = queue.Queue()
    stderr_chunks: list[bytes] = []

    def _read_stdout():
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(b"")  # EOF 哨兵

    def _read_stderr():
        assert proc.stderr is not None
        stderr_chunks.append(proc.stderr.read())

    threads = [
        threading.Thread(target=_read_stdout, daemon=True),
        threading.Thread(target=_read_stderr, daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + limits.wall_clock_seconds
    timed_out = False
    try:
        while True:
            remaining = min(REQUEST_TIMEOUT_SECONDS, deadline - time.monotonic())
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                timed_out = True
                break
            if not line:  # EOF：容器侧结束输出
                break
            response, stop = handler.handle(line)
            if response is not None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(response)
                    proc.stdin.flush()
                except BrokenPipeError:
                    break  # 容器已退出
            if stop:
                break
        try:
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        if proc.poll() is None:
            proc.kill()
        _force_remove(name)  # 强制回收，不留孤儿（--rm 之外的兜底）
        proc.wait(timeout=10)

    stderr_tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-2000:]

    if timed_out:
        return BackendOutcome(status=RunStatus.TIMEOUT, stderr_tail=stderr_tail)
    if handler.violation is not None:
        return BackendOutcome(status=RunStatus.PROTOCOL_VIOLATION, stderr_tail=stderr_tail)
    exit_code = proc.returncode
    if handler.final_node_id is not None and exit_code == 0:
        return BackendOutcome(status=RunStatus.COMPLETED, stderr_tail=stderr_tail)
    if exit_code == 137:
        return BackendOutcome(status=RunStatus.RESOURCE_EXCEEDED, stderr_tail=stderr_tail)
    return BackendOutcome(status=RunStatus.POLICY_ERROR, stderr_tail=stderr_tail)


def prepare_workdir(policy_source: str, handler, workdir: Path) -> None:
    """准备只读挂载目录：策略文件、预算文件、策略侧 IPC 客户端。"""
    import json

    workdir.mkdir(parents=True, exist_ok=True)
    workdir.chmod(0o755)  # tempfile 默认 0700；--cap-drop=ALL 后容器内无 CAP_DAC_OVERRIDE 可穿越
    (workdir / "policy.py").write_text(policy_source, encoding="utf-8")
    budget = handler.budget
    (workdir / "budget.json").write_text(
        json.dumps({"max_probes": budget.max_probes, "max_generation_calls": 0}),
        encoding="utf-8",
    )
    (workdir / "policy_side.py").write_text(
        POLICY_SIDE_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for file in workdir.iterdir():
        file.chmod(0o644)


def run_docker_backend(
    image: str, extra_flags: list[str], policy_source, handler, limits, record_command=None
):
    """后端公共流程：准备工作目录 → 起容器 → 泵 IPC → 回收。"""
    import tempfile

    name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="cineflow-sandbox-work-") as tmp:
        workdir = Path(tmp)
        prepare_workdir(policy_source, handler, workdir)
        command = build_command(image, name, workdir, limits, extra_flags)
        if record_command is not None:
            record_command(command)
        return run_container(command, name, handler, limits)


def docker_available() -> bool:
    try:
        return (
            subprocess.run(["docker", "version"], capture_output=True, timeout=15).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def runsc_available() -> bool:
    """探测 docker runtime 列表是否含 runsc（gVisor）。"""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0 and "runsc" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
