"""沙箱后端装配链（contracts/sandbox-ipc.md §4）。

顺序：DockerGVisorBackend（--runtime=runsc 可用，CI 权威）→
DockerHardenedBackend（本地兜底）→ 两者皆不可用则报错
（CI 必须 gVisor 可用，否则 CI 失败而非降级）。
"""

from core.sandbox.backends.docker_gvisor import DockerGVisorBackend
from core.sandbox.backends.docker_hardened import DockerHardenedBackend


class NoBackendAvailableError(Exception):
    """无任何可用沙箱后端。"""


def select_backend(image: str | None = None):
    """按装配顺序选择后端；皆不可用则报错。"""
    kwargs = {} if image is None else {"image": image}
    gvisor = DockerGVisorBackend(**kwargs)
    if gvisor.available():
        return gvisor
    hardened = DockerHardenedBackend(**kwargs)
    if hardened.available():
        return hardened
    raise NoBackendAvailableError("Docker 不可用且无 runsc：无法装配任何沙箱后端")
