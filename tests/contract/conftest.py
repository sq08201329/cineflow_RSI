"""契约套件公共夹具：让"真实实现分支"在本地 stub 上可跑（**默认不启用**）。

五个适配器契约套件（视觉/分镜/声音×3/剪辑/宣发）对双实现跑同一套断言，
真实实现分支此前无凭证即 skip。置 `CINEFLOW_CONTRACT_STUB=1` 后，本夹具起五个本地
stub 平台（`127.0.0.1` 随机端口、零外部网络、零真实凭证）并把七组凭证环境变量
指向它们——真实实现分支因此**实际执行**同一套契约断言（生成/渲染侧由 stub 按规范化
参数自行自算，见 tests/contract_platform_stub.py）。

**默认（未置开关）行为与既有逐字一致**：不设任何环境变量，无凭证环境下照旧按用例 skip；
`test_真实适配器无凭证构造即报未配置` 用 monkeypatch 清空环境变量后仍拿到 UnavailableError。

诚实边界：跑通的是"协议实现与既有契约一致"，不是"某家真实厂商已对接"。
"""

import os

import pytest

from tests.contract_platform_stub import credential_env, start_contract_stub, stub_enabled

_SET = object()


@pytest.fixture(scope="session", autouse=True)
def contract_stub_backends():
    """开关开启时把七组凭证指向本地 stub 平台；关闭时不做任何事（零副作用）。"""
    if not stub_enabled():
        yield None
        return
    servers = start_contract_stub()
    env = credential_env(servers)
    previous = {name: os.environ.get(name, _SET) for name in env}
    os.environ.update(env)
    try:
        yield env
    finally:
        for name, value in previous.items():
            if value is _SET:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for server in {id(server): server for server in servers.values()}.values():
            server.stop()
