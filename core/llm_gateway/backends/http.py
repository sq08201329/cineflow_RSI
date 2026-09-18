"""OpenAI 兼容 HTTP 后端（骨架；凭证经环境变量注入）。

本期无凭证环境，不由自动化测试覆盖真实调用（契约 §3）。
错误映射：HTTP 5xx / 超时 → TransientBackendError（网关可重试）；
HTTP 4xx → PermanentBackendError（不重试）。不泄漏 urllib 异常类型。
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


class HttpBackend:
    """OpenAI 兼容 /chat/completions 端点后端。"""

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
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise PermanentBackendError(f"HTTP {exc.code}: {exc.reason}") from exc
            raise TransientBackendError(f"HTTP {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientBackendError(f"网络/超时：{exc}") from exc

        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return BackendResult(
            text=choice,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )
