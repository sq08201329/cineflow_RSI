"""LLM 网关 http 后端契约套件（B 路径协议实现，等价于各平台适配器的契约套件）。

后端（`core.llm_gateway.backends.http.HttpBackend`）与网关（`LLMGateway`，唯一 LLM 入口）
的契约在此固化，**用本地 loopback stub 驱动**（`tests/stubs_http.py` 的 /chat/completions）：
零真实凭证、零外部网络。

三条契约（对齐各平台适配器契约套件的同款口径）：

1. **内容与 usage 以响应为准**：文本取 `choices[0].message.content`、token 取 `usage`；
   请求体（model/messages/temperature/max_tokens）与 Bearer 鉴权头原样透传；
2. **计费口径**：成本由**网关**按 `price_book` 折算（后端不本地估算 token）；
   usage 缺失/非法即报错——**不允许静默按 0 tokens 计费**（"不允许静默零成本"纪律）；
3. **错误映射**：5xx/超时/不可解析 → `TransientBackendError`（可重试）、429 限流 → 瞬态
   （对"4xx 不重试"的显式例外：限流是瞬态，网关退避正是对策）、其余 4xx → `PermanentBackendError`
   （不重试）；**不泄漏 urllib/socket 异常类型**。

另覆盖网关语义未被本后端破坏：缓存命中零成本且不再调后端、限流后网关退避重试成功。

诚实边界：跑通的是"协议实现与既有契约一致"，**不是"某家真实厂商已对接"**
（非 OpenAI 兼容的厂商需换后端实现，网关与计费口径不变）。
"""

import json

import pytest

from core.llm_gateway.backends.http import HttpBackend
from core.llm_gateway.gateway import (
    LLMGateway,
    PermanentBackendError,
    TransientBackendError,
)
from tests.stubs_http import StubPlatformServer


@pytest.fixture()
def stub_factory():
    """stub 平台工厂：随机端口启动，测试结束统一关闭（无残留线程/端口）。"""
    servers: list[StubPlatformServer] = []

    def _make(**kwargs) -> StubPlatformServer:
        server = StubPlatformServer(**kwargs).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()


# ---------------------------------------------------------------------------
# LLM 网关 http 后端（HttpBackend）：chat/completions + usage 计费
# ---------------------------------------------------------------------------


class Test后端契约:
    def _backend(self, server, **kwargs) -> HttpBackend:
        return HttpBackend(server.base_url, server.api_key, **kwargs)

    def test_全链_内容与_usage_以响应为准(self, stub_factory):
        server = stub_factory()
        backend = self._backend(server)
        result = backend.complete(
            "写一句台词", model="mock-copy-v1", temperature=0.0, max_tokens=64
        )

        assert result.text == "echo: mock-copy-v1"
        assert (result.prompt_tokens, result.completion_tokens) == (11, 7)
        assert backend.call_count == 1

        # 请求体与鉴权头原样（OpenAI 兼容口径）
        record = server.records_of("/chat/completions")[0]
        assert record["headers"]["authorization"] == "Bearer stub-key"
        body = json.loads(record["body"].decode("utf-8"))
        assert body["model"] == "mock-copy-v1"
        assert body["messages"] == [{"role": "user", "content": "写一句台词"}]
        assert body["max_tokens"] == 64 and body["temperature"] == 0.0

    def test_网关折算成本_按_usage_与价目表(self, stub_factory):
        """网关计费口径：cost = prompt/1000×prompt_per_1k + completion/1000×completion_per_1k。"""
        server = stub_factory()
        price_book = {"mock-copy-v1": {"prompt_per_1k": 1.0, "completion_per_1k": 2.0}}
        gateway = LLMGateway(self._backend(server), price_book, sleep=lambda _: None)
        result = gateway.chat("写一句台词", model="mock-copy-v1")
        assert result.usage == {"prompt_tokens": 11, "completion_tokens": 7}
        assert result.cost_usd == pytest.approx(11 / 1000 * 1.0 + 7 / 1000 * 2.0)
        assert result.cached is False

        cached = gateway.chat("写一句台词", model="mock-copy-v1")
        assert cached.cached is True and cached.cost_usd == 0.0  # 缓存命中零成本
        assert len(server.chat_requests()) == 1  # 缓存命中不调后端

    def test_usage_缺失_拒绝而非静默零成本(self, stub_factory):
        server = stub_factory(
            chat_provider=lambda payload: {"choices": [{"message": {"content": "ok"}}]}
        )
        with pytest.raises(TransientBackendError, match="缺 usage"):
            self._backend(server).complete("x", model="m", temperature=0.0, max_tokens=1)

    def test_usage_非法_拒绝(self, stub_factory):
        server = stub_factory(
            chat_provider=lambda payload: {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": "many"},
            }
        )
        with pytest.raises(TransientBackendError, match="usage 字段"):
            self._backend(server).complete("x", model="m", temperature=0.0, max_tokens=1)

    def test_响应缺_choices_给出可诊断错误(self, stub_factory):
        server = stub_factory(
            chat_provider=lambda payload: {
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )
        with pytest.raises(TransientBackendError, match="choices"):
            self._backend(server).complete("x", model="m", temperature=0.0, max_tokens=1)

    def test_响应不可解析_给出可诊断错误(self, stub_factory):
        server = stub_factory()
        server.enqueue_raw(b"<html>upstream error</html>")
        with pytest.raises(TransientBackendError, match="响应不可解析"):
            self._backend(server).complete("x", model="m", temperature=0.0, max_tokens=1)

    def test_错误映射_4xx永久_5xx与429与超时瞬态(self, stub_factory):
        server = stub_factory()
        backend = self._backend(server)
        server.enqueue_status(401)
        with pytest.raises(PermanentBackendError, match="HTTP 401"):
            backend.complete("x", model="m", temperature=0.0, max_tokens=1)
        server.enqueue_status(500)
        with pytest.raises(TransientBackendError, match="HTTP 500"):
            backend.complete("x", model="m", temperature=0.0, max_tokens=1)
        server.enqueue_status(429)
        with pytest.raises(TransientBackendError, match="限流"):
            backend.complete("x", model="m", temperature=0.0, max_tokens=1)
        server.enqueue_latency(1.5)
        slow = self._backend(server, timeout_seconds=0.2)
        with pytest.raises(TransientBackendError, match="超时 0.2s"):
            slow.complete("x", model="m", temperature=0.0, max_tokens=1)

    def test_网关重试语义_限流后成功(self, stub_factory):
        """429 归瞬态：网关退避重试后成功（睡由 gateway 注入，不真等）。"""
        server = stub_factory()
        price_book = {"mock-copy-v1": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0}}
        gateway = LLMGateway(self._backend(server), price_book, sleep=lambda _: None)
        server.enqueue_status(429)
        result = gateway.chat("写一句台词", model="mock-copy-v1")
        assert result.text == "echo: mock-copy-v1"
        assert len(server.chat_requests()) == 1  # 仅成功那次到达业务端点
