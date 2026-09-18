"""LLM 统一网关（最小版，contracts/llm-gateway.md，宪章原则三）。

一切 LLM 调用必须经此网关：统一计费（价目表折算）、内容哈希缓存
（命中零成本不调后端）、指数退避重试（上限 3 次，4xx 不重试）。
网关内部重试后仍失败的调用，调用方不得再次重试（防双层重试叠加放大）。
网关不做任何提示词业务逻辑（物料组装属 agents 层）。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import blake3

MAX_RETRIES = 3  # 重试上限（首次 + 3 次重试）
INITIAL_BACKOFF_SECONDS = 0.5


class GatewayError(Exception):
    """网关全部错误的基类。"""


class PricingNotFoundError(GatewayError):
    """价目表缺该模型：不允许静默零成本。"""


class TransientBackendError(GatewayError):
    """后端 5xx/超时：可重试。"""


class PermanentBackendError(GatewayError):
    """后端 4xx：不重试直接失败。"""


@dataclass(frozen=True)
class BackendResult:
    """后端原始产出：文本 + token 用量。"""

    text: str
    prompt_tokens: int
    completion_tokens: int


class LLMBackend(Protocol):
    """网关后端协议（Mock / Http 实现）。"""

    call_count: int

    def complete(
        self, prompt: str, *, model: str, temperature: float, max_tokens: int
    ) -> BackendResult: ...


@dataclass(frozen=True)
class LLMResult:
    """网关产出：文本、usage、折算成本、缓存命中标记。"""

    text: str
    usage: dict
    cost_usd: float
    cached: bool


class LLMGateway:
    """统一入口：chat(prompt, model=..., ...) -> LLMResult。"""

    def __init__(
        self,
        backend: LLMBackend,
        price_book: dict[str, dict],
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.backend = backend
        self._price_book = price_book
        self._sleep = sleep
        self._max_retries = max_retries
        self._cache: dict[str, LLMResult] = {}
        self.total_cost_usd = 0.0  # 网关账本（对账三方之一）
        self.call_count = 0  # 真实计费调用次数（缓存命中不计）

    def _cache_key(self, prompt: str, model: str, temperature: float, max_tokens: int) -> str:
        raw = f"{model}|{prompt}|{temperature}|{max_tokens}"
        return blake3.blake3(raw.encode("utf-8")).hexdigest()

    def _price_of(self, model: str) -> dict:
        if model not in self._price_book:
            raise PricingNotFoundError(f"价目表缺少模型 {model!r}（不允许静默零成本）")
        return self._price_book[model]

    def chat(
        self, prompt: str, *, model: str, temperature: float = 0.0, max_tokens: int = 1024
    ) -> LLMResult:
        price = self._price_of(model)  # 缺价目立即报错，不调后端
        key = self._cache_key(prompt, model, temperature, max_tokens)
        if key in self._cache:
            cached = self._cache[key]
            return LLMResult(text=cached.text, usage=cached.usage, cost_usd=0.0, cached=True)

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                raw = self.backend.complete(
                    prompt, model=model, temperature=temperature, max_tokens=max_tokens
                )
                break
            except TransientBackendError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    self._sleep(INITIAL_BACKOFF_SECONDS * (2**attempt))  # 指数退避
            # PermanentBackendError 不重试，直接上抛
        else:
            raise last_error  # type: ignore[misc]

        cost = (
            raw.prompt_tokens / 1000 * price["prompt_per_1k"]
            + raw.completion_tokens / 1000 * price["completion_per_1k"]
        )
        usage = {"prompt_tokens": raw.prompt_tokens, "completion_tokens": raw.completion_tokens}
        result = LLMResult(text=raw.text, usage=usage, cost_usd=cost, cached=False)
        self._cache[key] = result
        self.total_cost_usd += cost
        self.call_count += 1
        return result
