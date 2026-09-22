"""OpenAI 兼容 HTTP 后端（B 路径**协议实现**，本批由骨架落地）。

## 实现的是什么（诚实登记，宪章原则六）

**协议实现**：OpenAI 兼容的 `POST {base}/chat/completions` + 规范错误分型；
**不是"某家真实厂商的对接"**——真实接入仍需按厂商 API 调整路径/字段（属配置或小改）。
**"能连本地 stub"不等于"B 路径已验证"**：真实凭证、真实厂商、真实计费均未跑过。

## 协议与计费口径

```
POST {base}/chat/completions   Authorization: Bearer <key>
  body {"model", "messages": [{"role": "user", "content": prompt}], "temperature", "max_tokens"}
  → 200 {"choices": [{"message": {"content": "..."}}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 34}}
```

- **内容与 usage 一律以响应为准**：本后端**不本地估算** token（不按字符数猜）；
  usage 缺失/非法即报错——绝不静默按 0 tokens 计费（网关"不允许静默零成本"纪律，
  成本折算由网关按 `price_book` 完成，后端只如实回传 usage）；
- **缓存/重试语义由网关承载**（内容哈希缓存命中零成本、指数退避上限 3 次），
  本后端只做一次 HTTP 调用，**不自行重试**（防双层重试叠加放大）。

## 错误映射（不泄漏 urllib/socket 异常类型）

| 情况 | 映射 |
| --- | --- |
| 5xx / 连接失败 / 超时 | `TransientBackendError`（网关可重试，文案注明超时值） |
| 429 限流 | `TransientBackendError`（**对既有"4xx 不重试"的一处显式例外**：限流是瞬态，
  网关的指数退避正是对策；若映射为不重试，一次限流会打挂整条链） |
| 其余 4xx（含 401/403 鉴权被拒） | `PermanentBackendError`（不重试，直接失败） |
| 响应不可解析 / 结构不符 / usage 缺失 | `TransientBackendError`（带截断响应片段；
  重试后仍失败即上抛，不静默降级） |

凭证经环境变量注入（OPENAI_BASE_URL / OPENAI_API_KEY，构造器直接读取，无 `from_env`）；
无凭证构造即 `GatewayError`（不假装可调用）。**网关仍是唯一 LLM 入口**：业务代码不得直连本类。
"""

import json
import os
import urllib.error
import urllib.request

from core.llm_gateway.gateway import (
    BackendResult,
    GatewayError,
    PermanentBackendError,
    TransientBackendError,
)

SNIPPET_LIMIT = 200


def _snippet(raw: bytes, *, limit: int = SNIPPET_LIMIT) -> str:
    """响应片段（空白折叠 + 截断）：错误文案可诊断，又不把整个响应当报错刷屏。"""
    text = " ".join(raw.decode("utf-8", "replace").split())
    return text if len(text) <= limit else f"{text[:limit]}…（截断）"


def _tokens_of(usage: dict, key: str) -> int:
    """usage 字段解析：必须为 ≥ 0 的整数——缺失/类型不符即报错（不按 0 计费的静默降级）。"""
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TransientBackendError(
            f"后端 usage 字段 {key!r} 缺失或非法：{value!r}——不允许静默按 0 计费"
        )
    return value


class HttpBackend:
    """OpenAI 兼容 /chat/completions 端点后端（协议实现；本地 stub 已验证）。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.base_url or not self.api_key:
            raise GatewayError("HttpBackend 缺凭证：需要 OPENAI_BASE_URL / OPENAI_API_KEY")
        self._timeout = timeout_seconds
        self.call_count = 0

    def complete(
        self, prompt: str, *, model: str, temperature: float, max_tokens: int
    ) -> BackendResult:
        self.call_count += 1
        raw = self._post(prompt, model=model, temperature=temperature, max_tokens=max_tokens)
        payload = self._json(raw)
        usage = self._usage_of(payload, raw)
        return BackendResult(
            text=self._content_of(payload, raw),
            prompt_tokens=_tokens_of(usage, "prompt_tokens"),
            completion_tokens=_tokens_of(usage, "completion_tokens"),
        )

    # ---- 内部 ----

    def _post(self, prompt: str, *, model: str, temperature: float, max_tokens: int) -> bytes:
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientBackendError(
                f"网络/超时（{self.base_url.rstrip('/')}/chat/completions，"
                f"超时 {self._timeout:g}s）：{exc}"
            ) from exc

    def _http_error(self, exc: urllib.error.HTTPError) -> GatewayError:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 - 读不到体仍要给出映射后的错误
            body = b""
        detail = f"HTTP {exc.code} {exc.reason}"
        if body:
            detail = f"{detail}；响应片段：{_snippet(body)}"
        if exc.code == 429:  # 限流：瞬态（见模块 docstring 的显式例外说明）
            return TransientBackendError(f"后端限流（可重试）：{detail}")
        if 400 <= exc.code < 500:
            return PermanentBackendError(f"后端拒绝请求（不重试）：{detail}")
        return TransientBackendError(f"后端不可用（可重试）：{detail}")

    def _json(self, raw: bytes) -> dict:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransientBackendError(
                f"后端响应不可解析：{exc}；响应片段：{_snippet(raw)}"
            ) from exc
        if not isinstance(payload, dict):
            raise TransientBackendError(
                f"后端响应期望 JSON 对象，实际 {type(payload).__name__}；响应片段：{_snippet(raw)}"
            )
        return payload

    def _content_of(self, payload: dict, raw: bytes) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TransientBackendError(
                f"后端响应缺 choices[0].message.content（{exc}）；响应片段：{_snippet(raw)}"
            ) from exc
        if not isinstance(content, str):
            raise TransientBackendError(
                f"后端响应 content 非字符串（实际 {type(content).__name__}）；"
                f"响应片段：{_snippet(raw)}"
            )
        return content

    def _usage_of(self, payload: dict, raw: bytes) -> dict:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise TransientBackendError(
                "后端响应缺 usage（无法按价目表折算成本）——不允许静默按 0 tokens 计费；"
                f"响应片段：{_snippet(raw)}"
            )
        return usage
