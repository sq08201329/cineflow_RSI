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
from collections.abc import Mapping

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
    """OpenAI 兼容 /chat/completions 端点后端（协议实现；本地 stub 已验证）。

    ## 凭证中立（功能 016 / FR-006/007）：端点与密钥**由路由层注入**

    - `HttpBackend(base_url, api_key)`：**显式传入即用传入值**——构造函数**不读任何环境变量**
      （消除"平台自带一个无关 `OPENAI_API_KEY` 被当成档案就绪"的假阳性）；
    - `HttpBackend.from_profile(profile)`：按档案声明读取——URL 形态用档案里的端点，
      env 形态读 `base_url_env` 声明的变量名；密钥读 `api_key_env` 声明的变量名；
      **声明的变量未设置即报错并指名道姓**；`legacy_env=True` 时 `legacy_env_note` 标注
      "沿用旧变量名（建议改中立名）"（报告层据此提示迁移）；
    - `HttpBackend.from_profiles(load)`：多档案**按 `model`（= 路由命中的档案 id）分派**到各自端点；
    - `HttpBackend.from_env()`：旧路径（显式读 `OPENAI_BASE_URL` / `OPENAI_API_KEY`），
      仅供未接档案的装配点与"代码读取点"机检使用；
    - 裸构造 `HttpBackend()` 一律报错（不再隐式读环境）。
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 30.0,
        legacy_env_note: str = "",
        request_options: Mapping | None = None,
        model_name: str | None = None,
    ) -> None:
        if not base_url or not api_key:
            raise GatewayError(
                "HttpBackend 需要显式注入 base_url 与 api_key（不再隐式读 OPENAI_*）："
                "请用 HttpBackend.from_profile(档案) / from_profiles(配置档案) 注入，"
                "或旧路径 HttpBackend.from_env()（显式读 OPENAI_BASE_URL / OPENAI_API_KEY）"
            )
        self.base_url = base_url
        self.api_key = api_key
        self.legacy_env_note = legacy_env_note
        # 档案声明的请求参数（白名单已在 profiles 层校验）：合并进请求体，
        # **不覆盖**调用方显式给出的 model/messages/temperature/max_tokens（显式入参优先）
        self._request_options = {str(k): v for k, v in dict(request_options or {}).items()}
        # 请求体里的 model：档案声明的**厂商模型名**（缺省 = 传入的 model，兼容旧形态）
        self._model_name = model_name or ""
        self._models: dict = {}
        self._timeout = timeout_seconds
        self.call_count = 0

    # ---- 构造入口（凭证中立）----

    @classmethod
    def from_env(cls, *, timeout_seconds: float = 30.0) -> "HttpBackend":
        """旧路径：显式读 `OPENAI_BASE_URL` / `OPENAI_API_KEY`（未接档案时的兼容入口）。"""
        base_url = os.environ.get("OPENAI_BASE_URL", "")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not base_url or not api_key:
            raise GatewayError(
                "HttpBackend 缺凭证：需要 OPENAI_BASE_URL / OPENAI_API_KEY"
                "（或用配置档案注入：HttpBackend.from_profile(档案)）"
            )
        return cls(base_url, api_key, timeout_seconds=timeout_seconds)

    @classmethod
    def from_profile(
        cls, profile, *, timeout_seconds: float | None = None, environ: dict | None = None
    ) -> "HttpBackend":
        """按**档案声明**注入端点与密钥（凭证只按声明读取，不碰其它变量）。

        超时优先级：显式入参 > 档案声明的 `timeout_seconds` > 默认 30s。
        推理模型（如 deepseek-flash）的思维链+正文常超过 30s，故档案里可声明更长超时
        （真实实测：30s 会 `The read operation timed out`）。
        """
        if timeout_seconds is None:
            timeout_seconds = getattr(profile, "timeout_seconds", None) or 30.0
        env = os.environ if environ is None else environ
        if profile.base_url:
            base_url = str(profile.base_url)
        else:
            base_url = env.get(str(profile.base_url_env), "")
            if not base_url:
                raise GatewayError(
                    f"档案 {profile.profile_id!r} 声明的端点变量 {profile.base_url_env}"
                    " 未设置——凭证只按档案声明读取（不隐式读 OPENAI_*）"
                )
        api_key = env.get(str(profile.api_key_env), "")
        if not api_key:
            raise GatewayError(
                f"档案 {profile.profile_id!r} 声明的凭证变量 {profile.api_key_env}"
                " 未设置——凭证只按档案声明读取（不隐式读 OPENAI_*）"
            )
        note = ""
        if profile.legacy_env:
            note = (
                f"档案 {profile.profile_id!r} 沿用旧变量名（{profile.api_key_env}）："
                "建议改中立名（如 <VENDOR>_API_KEY）以避免与其它供应商的同名变量混淆"
            )
        return cls(
            base_url,
            api_key,
            timeout_seconds=timeout_seconds,
            legacy_env_note=note,
            request_options=getattr(profile, "request_options", None),
            model_name=getattr(profile, "vendor_model", None),
        )

    @classmethod
    def from_profiles(
        cls,
        load,
        *,
        timeout_seconds: float | None = None,
        environ: dict | None = None,
    ) -> "HttpBackend":
        """多档案：按 `model`（= 路由命中的档案 id）分派到各自端点。

        **只在"被用到的档案"上要求凭证**：已就绪的档案构建端点；缺凭证的档案记入 `pending`
        并在**被调用时**报错（指名缺哪个变量，绝不静默回落到其它档案）——这样"只拿到一家
        凭证"的用户仍可启动（未用到的那条档案不阻塞装配）；但**默认档案缺凭证即装配期拒绝**
        （它几乎必然被用到，fail-fast 更安全），全部档案都缺也直接拒绝。
        """
        env = os.environ if environ is None else environ
        backends: dict = {}
        pending: dict[str, str] = {}
        for profile_id, profile in load.profiles.items():
            try:
                backends[profile_id] = cls.from_profile(
                    profile, timeout_seconds=timeout_seconds, environ=env
                )
            except GatewayError as exc:
                pending[profile_id] = str(exc)
        if not backends:
            raise GatewayError(
                "配置档案全部缺凭证（凭证只按档案声明读取）：" + "；".join(sorted(pending.values()))
            )
        default = getattr(getattr(load, "routing", None), "default_profile", "")
        if default and default in pending:
            raise GatewayError(
                f"默认档案 {default!r} 缺凭证，装配期即拒绝（不静默回落）：{pending[default]}"
            )
        return _RoutedHttpBackend(backends, pending=pending, timeout_seconds=timeout_seconds)

    def _endpoint_for(self, model: str) -> tuple[str, str]:
        """本次调用的（端点，密钥）：单后端按自身端点；多档案后端按 model 分派（见子类）。"""
        return self.base_url, self.api_key

    def complete(
        self, prompt: str, *, model: str, temperature: float, max_tokens: int
    ) -> BackendResult:
        """`model` 参数 = **档案 id**（路由键）；请求体里的 model 由档案声明的厂商模型名决定。"""
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

    def _options_for(self, model: str) -> dict:
        """本次请求生效的**档案请求参数**：单端点后端用自身选项；多档案后端按 model 分派。"""
        return dict(self._request_options)

    def _vendor_model_for(self, model: str) -> str:
        """请求体里的 model：档案声明的厂商模型名（`model` 仍是**档案 id**，用于分派）。"""
        if model in self._models:
            return self._models[model] or model
        return self._model_name or model

    def _post(self, prompt: str, *, model: str, temperature: float, max_tokens: int) -> bytes:
        base_url, api_key = self._endpoint_for(model)
        # 档案请求参数先入、显式入参后覆盖：`thinking`/`reasoning_effort` 由档案声明
        # （思考模式下 temperature 被厂商忽略——如实登记，不做本地改写）
        body: dict = dict(self._options_for(model))
        body.update(
            {
                "model": self._vendor_model_for(model),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
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
        if not content.strip():
            # 空正文的**形态诊断**（真实排障需要）：是被截断（finish_reason=length）、
            # 只有思维链（reasoning_content 非空）、还是平台就是回了空串。
            message = payload["choices"][0].get("message") or {}
            finish = payload["choices"][0].get("finish_reason")
            reasoning = message.get("reasoning_content")
            raise TransientBackendError(
                "后端返回空 content："
                f"finish_reason={finish!r}，reasoning_content="
                + ("非空（模型只产出思维链，正文被截断或未输出）" if reasoning else "空/缺失")
                + f"；响应片段：{_snippet(raw)}"
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


class _RoutedHttpBackend(HttpBackend):
    """按 `model`（= 档案 id）分派到各自端点的后端（多档案配置用）。

    路由决策在网关（角色 → 档案），本类只做"档案 id → 端点"的机械分派：**未知档案即报错**，
    不静默回落到默认端点（避免把请求打到错误的供应商）。
    """

    def __init__(
        self, backends: dict, *, pending: dict | None = None, timeout_seconds: float = 30.0
    ) -> None:
        if not backends:
            raise GatewayError("HttpBackend.from_profiles 需要至少一个档案后端")
        first = next(iter(backends.values()))
        super().__init__(first.base_url, first.api_key, timeout_seconds=timeout_seconds)
        self._backends = dict(backends)
        self._models = {pid: backend._model_name for pid, backend in backends.items()}
        self._pending = dict(pending or {})  # 缺凭证的档案：调用时报错（不静默回落）
        notes = [
            backend.legacy_env_note for backend in backends.values() if backend.legacy_env_note
        ]
        if self._pending:
            notes.append(f"以下档案缺凭证（调用即失败）：{sorted(self._pending)}")
        self.legacy_env_note = "；".join(notes)

    def _options_for(self, model: str) -> dict:
        backend = self._backends.get(model)
        return (
            dict(backend._request_options) if backend is not None else dict(self._request_options)
        )

    def _endpoint_for(self, model: str) -> tuple[str, str]:
        backend = self._backends.get(model)
        if backend is None:
            if model in self._pending:
                raise GatewayError(
                    f"档案 {model!r} 缺凭证（凭证只按档案声明读取，调用即失败、"
                    f"不静默回落到其它档案）：{self._pending[model]}"
                )
            raise GatewayError(
                f"未知档案端点 {model!r}：已注入档案 {sorted(self._backends)}"
                "（不静默回落到默认端点）"
            )
        return backend.base_url, backend.api_key
