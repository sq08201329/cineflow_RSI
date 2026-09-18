"""LLM 网关单测（T204，契约 contracts/llm-gateway.md）。

计费入账、缓存命中零成本不调用后端、指数退避重试（上限 3 次）、
4xx 不重试、缺价目报错、Mock 后端确定性。
"""

import pytest

from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import (
    GatewayError,
    LLMGateway,
    PermanentBackendError,
    PricingNotFoundError,
    TransientBackendError,
)

PRICE_BOOK = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


@pytest.fixture()
def gateway():
    return LLMGateway(MockBackend(), price_book=PRICE_BOOK, sleep=lambda _: None)


class Test计费:
    def test_按价目表折算成本(self, gateway):
        result = gateway.chat("写一句口号", model="mock-copy-v1")
        expected = (
            result.usage["prompt_tokens"] / 1000 * 0.001
            + result.usage["completion_tokens"] / 1000 * 0.002
        )
        assert result.cost_usd == pytest.approx(expected)
        assert result.cost_usd > 0  # 不允许静默零成本
        assert not result.cached

    def test_缺价目即报错(self, gateway):
        with pytest.raises(PricingNotFoundError, match="ghost-model"):
            gateway.chat("hi", model="ghost-model")

    def test_网关账本累计(self, gateway):
        gateway.chat("a", model="mock-copy-v1")
        gateway.chat("b", model="mock-copy-v1")
        assert gateway.total_cost_usd > 0
        assert gateway.call_count == 2


class Test缓存:
    def test_命中缓存零成本不调后端(self, gateway):
        first = gateway.chat("同一提示词", model="mock-copy-v1")
        calls_before = gateway.backend.call_count
        second = gateway.chat("同一提示词", model="mock-copy-v1")
        assert second.cached
        assert second.cost_usd == 0.0
        assert gateway.backend.call_count == calls_before  # 后端未被再次调用
        assert second.text == first.text

    def test_参数不同缓存键不同(self, gateway):
        a = gateway.chat("p", model="mock-copy-v1", temperature=0.0)
        b = gateway.chat("p", model="mock-copy-v1", temperature=0.7)
        assert not b.cached
        assert a.text != b.text  # 采样参数进缓存键与生成种子


class Test重试退避:
    def test_瞬时错误重试后成功(self):
        class FlakyBackend(MockBackend):
            def __init__(self):
                super().__init__()
                self.failures_left = 2

            def complete(self, prompt, *, model, temperature, max_tokens):
                if self.failures_left > 0:
                    self.call_count += 1  # 失败路径自行计数（成功路径由父类计）
                    self.failures_left -= 1
                    raise TransientBackendError("503 后端不可用")
                return super().complete(
                    prompt, model=model, temperature=temperature, max_tokens=max_tokens
                )

        sleeps: list[float] = []
        gw = LLMGateway(FlakyBackend(), price_book=PRICE_BOOK, sleep=sleeps.append)
        result = gw.chat("hi", model="mock-copy-v1")
        assert result.text
        assert gw.backend.call_count == 3  # 失败 2 次 + 第 3 次成功
        assert sleeps == [pytest.approx(0.5), pytest.approx(1.0)]  # 指数退避

    def test_重试上限后抛错(self):
        class AlwaysDown(MockBackend):
            def complete(self, prompt, *, model, temperature, max_tokens):
                self.call_count += 1
                raise TransientBackendError("持续不可用")

        gw = LLMGateway(AlwaysDown(), price_book=PRICE_BOOK, sleep=lambda _: None)
        with pytest.raises(TransientBackendError):
            gw.chat("hi", model="mock-copy-v1")
        assert gw.backend.call_count == 4  # 首次 + 3 次重试，上限即停

    def test_永久错误不重试(self):
        class BadRequest(MockBackend):
            def complete(self, prompt, *, model, temperature, max_tokens):
                self.call_count += 1
                raise PermanentBackendError("400 参数非法")

        gw = LLMGateway(BadRequest(), price_book=PRICE_BOOK, sleep=lambda _: None)
        with pytest.raises(PermanentBackendError):
            gw.chat("hi", model="mock-copy-v1")
        assert gw.backend.call_count == 1  # 4xx 不重试


class TestMock确定性:
    def test_同输入逐字节可复现(self):
        backend = MockBackend()
        a = backend.complete("海报文案", model="mock-copy-v1", temperature=0.0, max_tokens=64)
        b = backend.complete("海报文案", model="mock-copy-v1", temperature=0.0, max_tokens=64)
        assert a == b
        assert a.text and a.prompt_tokens > 0 and a.completion_tokens > 0

    def test_不同_prompt_不同文本(self):
        backend = MockBackend()
        a = backend.complete("甲", model="m", temperature=0.0, max_tokens=64)
        b = backend.complete("乙", model="m", temperature=0.0, max_tokens=64)
        assert a.text != b.text

    def test_网关层错误基类(self):
        assert issubclass(PricingNotFoundError, GatewayError)
