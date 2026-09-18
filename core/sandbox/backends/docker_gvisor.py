"""gVisor Docker 后端（CI 权威，research 决策 1）。

docker run --runtime=runsc：内核级隔离。GitHub Actions ubuntu-latest
可 apt 安装 runsc 并注册为 docker runtime；CI 上必须可用，否则 CI 失败
而非降级（对抗门禁不允许静默豁免）。
"""

from core.sandbox.backends.docker_common import run_docker_backend, runsc_available, security_flags
from core.sandbox.runner import RunLimits

DEFAULT_IMAGE = "python:3.11-slim"


class DockerGVisorBackend:
    """gVisor 后端：与 Hardened 同一加固旗标集 + --runtime=runsc。"""

    name = "docker_gvisor"
    extra_flags: list[str] = ["--runtime=runsc"]

    def __init__(self, image: str = DEFAULT_IMAGE) -> None:
        self.image = image
        self.last_command: list[str] = []

    def available(self) -> bool:
        return runsc_available()

    def security_flags(self, limits: RunLimits) -> list[str]:
        return security_flags(limits)

    def run(self, policy_source: str, handler, limits: RunLimits):
        return run_docker_backend(
            self.image,
            list(self.extra_flags),
            policy_source,
            handler,
            limits,
            record_command=lambda cmd: setattr(self, "last_command", cmd),
        )
