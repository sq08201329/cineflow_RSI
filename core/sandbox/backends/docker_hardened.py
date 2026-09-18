"""加固 Docker 后端（本地兜底，research 决策 1）。

隔离语义与 gVisor 等价（无网络、无凭证、只读挂载、资源限额），
内核隔离强度较低；CI 权威后端为 DockerGVisorBackend。
"""

from core.sandbox.backends.docker_common import docker_available, run_docker_backend, security_flags
from core.sandbox.runner import RunLimits

DEFAULT_IMAGE = "python:3.11-slim"


class DockerHardenedBackend:
    """加固旗标容器后端：--network=none + 只读 + cap-drop + 限额。"""

    name = "docker_hardened"
    extra_flags: list[str] = []

    def __init__(self, image: str = DEFAULT_IMAGE) -> None:
        self.image = image
        self.last_command: list[str] = []  # 最近一次容器命令（隔离断言与审计用）

    def available(self) -> bool:
        return docker_available()

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
