"""真实平台 HTTP 协议客户端（B 路径"协议实现"的公共层，形态无关）。

## 本模块实现的是什么（诚实登记）

实现的是**协议**：请求形态 + 错误映射 + 成本纪律，三个媒体环节（视觉生成 /
分镜预演 / 剪辑渲染）共用一套。**不是"某家真实厂商的对接"**——真实接入时按厂商
API 调整路径/鉴权/字段（属配置或小改）；且"能连本地 stub"**不等于"B 路径已验证"**，
凭证到位后的真实厂商对接仍未做（见 docs/二期升级路径-真实生成与投放.md 诚实边界）。

## 协议（三个环节逐字同一套；tests/integration/test_http_real_stub.py 逐条固化）

```
POST {base}/jobs                      Authorization: Bearer <key>
  body {"params": {...}, "idempotency_key": "<key>"}
  → 202 {"external_id", "estimated_cost_usd", "status",
         可选 "params_hash" / "job_id"}
  幂等：同 idempotency_key 重复提交必须返回同一 external_id（**平台侧语义**，
  适配器只原样透传幂等键，绝不自行编造 external_id）
POST {base}/estimate
  body {"params": {...}} → 200 {"estimated_cost_usd"}
  （只读估价：预算门禁申请前的校验依据，不建任务不计费）
GET  {base}/jobs/{external_id}
  → 200 {"status": "pending|running|succeeded|failed",
         "estimated_cost_usd", "actual_cost_usd", "error"}
  （actual_cost_usd 为 null 表示尚未扣费）
GET  {base}/jobs/{external_id}/artifact
  → 200 二进制体（mp4 原样）+ `X-Artifact-Meta: base64(JSON)`；未就绪 → 409
POST {base}/jobs/{external_id}/cancel → 202（已完成/已失败为无操作）
GET  {base}/health                    → 200（只读探测端点，对齐
                                        ops/check_credentials.py 的 PROBE_PATHS 口径）
```

## 错误映射（不泄漏 urllib/socket 异常类型；文案含响应片段便于诊断）

| 情况 | 映射 |
| --- | --- |
| 429 | `rate_limited`（可重试） |
| 409 | `conflict`（资源状态冲突，如工件未就绪；未指定则回落 `invalid`） |
| 其余 4xx（含 401/403 鉴权被拒） | `invalid`（不重试） |
| 5xx / 连接失败 / 请求超时 / 响应不可解析 | `unavailable`（可重试，文案注明超时值） |

各环节的错误族由调用方注入（`HttpErrors`）：公共层不引入新错误类型，
"错误分型与既有基类一致"由本注入点保证。

## 成本纪律（不本地估算）

金额一律以响应为准（本地零估算）；`actual > estimated` 即显式报错
（`enforce_actual_le_estimate`）——不静默接受、不向下取整。
"""

from __future__ import annotations

import base64
import binascii
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import blake3

SNIPPET_LIMIT = 200  # 错误文案里响应片段的截断长度


@dataclass(frozen=True)
class HttpErrors:
    """某环节的错误族（由各适配器注入自己的既有类型）。

    `conflict` / `anomaly` 未指定时分别回落 `invalid` / `unavailable`——
    回落是显式的，不留"未映射的裸异常"。
    """

    rate_limited: type[Exception]
    unavailable: type[Exception]
    invalid: type[Exception]
    conflict: type[Exception] | None = None
    anomaly: type[Exception] | None = None

    def for_conflict(self) -> type[Exception]:
        return self.conflict or self.invalid

    def for_anomaly(self) -> type[Exception]:
        return self.anomaly or self.unavailable


def snippet(raw: bytes, *, limit: int = SNIPPET_LIMIT) -> str:
    """响应片段（空白折叠 + 截断）：错误文案可诊断，又不把整个响应当报错刷屏。"""
    text = " ".join(raw.decode("utf-8", "replace").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（截断，原文 {len(text)} 字符）"


def canonical_params(params: Mapping[str, Any]) -> str:
    """规范化参数 JSON（键排序、中文不转义）：幂等键/参数指纹的同一口径。"""
    return json.dumps(params, sort_keys=True, ensure_ascii=False)


def params_fingerprint(params: Mapping[str, Any]) -> str:
    """参数指纹（BLAKE3，同 004/006/007 既有 canonical 口径）。

    仅用于（a）幂等键派生、（b）平台未回传 `params_hash` 时的运营表键兜底——
    纯哈希，不含任何本地费用/规格估算。
    """
    return blake3.blake3(canonical_params(params).encode()).hexdigest()


def idempotency_key(namespace: str, params: Mapping[str, Any]) -> str:
    """由规范化参数确定性派生幂等键：同输入重复渲染命中同一平台任务（0 重复扣费）。

    幂等键的**语义实现**在平台侧（同键必返回同一 external_id）；此处只保证
    "同输入同键"，不承担去重。
    """
    return f"{namespace}:{params_fingerprint(params)}"


class PlatformHttpClient:
    """真实平台 HTTP 客户端：Bearer 鉴权 + 超时 + 统一错误映射。

    只连 `base_url` 指向的地址（不跟随跳转到其他主机、不读其他环境变量）；
    `timeout_s` 由适配器构造参数注入（形态配置/构造参数可配，默认取保守值）。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        errors: HttpErrors,
        timeout_s: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._errors = errors
        self._timeout_s = float(timeout_s)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def post_json(self, path: str, payload: Mapping[str, Any]) -> dict:
        raw, _ = self._request("POST", path, payload=payload)
        return self._json(raw, path)

    def get_json(self, path: str) -> dict:
        raw, _ = self._request("GET", path)
        return self._json(raw, path)

    def get_bytes(self, path: str) -> tuple[bytes, dict[str, str]]:
        """取二进制工件：返回（字节体，小写键响应头）。"""
        return self._request("GET", path)

    def _request(
        self, method: str, path: str, *, payload: Mapping[str, Any] | None = None
    ) -> tuple[bytes, dict[str, str]]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json, */*"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                return response.read(), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, path) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise self._errors.unavailable(
                f"真实平台连接失败/超时（{method} {self._base_url}{path}，"
                f"超时 {self._timeout_s:g}s）：{exc}"
            ) from exc

    def _http_error(self, exc: urllib.error.HTTPError, path: str) -> Exception:
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 - 读不到体仍要给出映射后的错误
            raw = b""
        detail = f"HTTP {exc.code} {exc.reason}"
        if raw:
            detail = f"{detail}；响应片段：{snippet(raw)}"
        where = f"{self._base_url}{path}"
        if exc.code == 429:
            return self._errors.rate_limited(f"真实平台限流（{where}）：{detail}（可重试）")
        if exc.code == 409:
            return self._errors.for_conflict()(f"真实平台资源状态冲突（{where}）：{detail}")
        if 400 <= exc.code < 500:
            return self._errors.invalid(f"真实平台拒绝请求（{where}）：{detail}")
        return self._errors.unavailable(f"真实平台不可用（{where}）：{detail}")

    def _json(self, raw: bytes, path: str) -> dict:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise self._errors.unavailable(
                f"真实平台响应不可解析（{self._base_url}{path}）：{exc}；响应片段：{snippet(raw)}"
            ) from exc
        if not isinstance(payload, dict):
            raise self._errors.unavailable(
                f"真实平台响应形态不符（{self._base_url}{path}，期望 JSON 对象）："
                f"响应片段：{snippet(raw)}"
            )
        return payload


def required_str(payload: Mapping[str, Any], key: str, *, errors: HttpErrors, where: str) -> str:
    """必填字符串字段：缺失/类型不符即 unavailable（响应形态不符，不猜测补默认值）。"""
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise errors.unavailable(f"{where} 缺必填字段 {key!r}（期望非空字符串，实际 {value!r}）")
    return value


def optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def required_cost(payload: Mapping[str, Any], key: str, *, errors: HttpErrors, where: str) -> float:
    """必填金额字段（USD）：非数值/负数/bool 一律拒绝（金额以响应为准，不本地估算）。"""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise errors.unavailable(
            f"{where} 的金额字段 {key!r} 非合法数值（实际 {value!r}）——金额一律以平台响应为准"
        )
    return float(value)


def optional_cost(
    payload: Mapping[str, Any], key: str, *, errors: HttpErrors, where: str
) -> float | None:
    """可选金额字段：null/缺失即 None（语义为"尚未扣费"）；给了非数值即拒绝。"""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise errors.unavailable(
            f"{where} 的金额字段 {key!r} 非合法数值（实际 {value!r}）——金额一律以平台响应为准"
        )
    return float(value)


def enforce_actual_le_estimate(
    actual: float,
    estimated: float,
    *,
    errors: HttpErrors,
    where: str,
) -> float:
    """`actual ≤ estimated` 纪律：超出即显式报错（不静默接受平台账目异常）。"""
    if actual > estimated + 1e-9:
        raise errors.for_anomaly()(
            f"{where} 平台实际扣费 ${actual:.4f} 超过预估 ${estimated:.4f}"
            "——账目异常，拒绝入账（实际扣费以平台为准，但不接受超出预估的扣费）"
        )
    return actual


def decode_artifact_meta(headers: Mapping[str, str], *, errors: HttpErrors, where: str) -> dict:
    """解析 `X-Artifact-Meta`（base64(JSON)）工件元数据响应头。

    缺失/非 base64/非 JSON 对象一律 unavailable 并给出片段——元数据是评估器输入，
    不敢猜、不静默降级为空元数据。
    """
    raw = headers.get("x-artifact-meta")
    if not raw:
        raise errors.unavailable(f"{where} 缺工件元数据响应头 X-Artifact-Meta（base64(JSON)）")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise errors.unavailable(
            f"{where} 的 X-Artifact-Meta 非合法 base64：{exc}；片段：{snippet(raw.encode())}"
        ) from exc
    try:
        meta = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.unavailable(
            f"{where} 的 X-Artifact-Meta 非合法 JSON：{exc}；片段：{snippet(decoded)}"
        ) from exc
    if not isinstance(meta, dict):
        raise errors.unavailable(
            f"{where} 的 X-Artifact-Meta 期望 JSON 对象，实际 {type(meta).__name__}"
        )
    return meta


TERMINAL_STATUS = "succeeded"
FAILED_STATUS = "failed"
PENDING_STATUSES = ("pending", "running")


def await_succeeded(
    client: PlatformHttpClient,
    external_id: str,
    *,
    errors: HttpErrors,
    failed_error: type[Exception],
    poll_interval_s: float,
    poll_deadline_s: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """轮询至终态：succeeded 返回末次状态响应；failed 抛 `failed_error`；超时抛 unavailable。

    只读轮询（不重提任务、不重复计费）；超时不作"任务失败"处理——任务可能仍在平台侧
    执行，错误文案给出 external_id 与截止值便于凭据复查。同步渲染接口（分镜/剪辑
    `render`）用本函数等待；异步接口（视觉 `get_status`）不轮询，节奏由调用方决定。
    """
    deadline = time.monotonic() + poll_deadline_s
    while True:
        where = f"GET /jobs/{external_id} 响应"
        payload = client.get_json(f"/jobs/{external_id}")
        status = payload.get("status")
        if status == TERMINAL_STATUS:
            return payload
        if status == FAILED_STATUS:
            reason = payload.get("error") or "平台未提供错误详情"
            raise failed_error(f"真实平台渲染失败（任务 {external_id}）：{reason}")
        if status not in PENDING_STATUSES:
            raise errors.unavailable(
                f"{where} 的平台状态未登记（实际 {status!r}）：本协议取值为 "
                f"{[TERMINAL_STATUS, FAILED_STATUS, *PENDING_STATUSES]}——不静默猜测未知状态"
            )
        if time.monotonic() >= deadline:
            raise errors.unavailable(
                f"等待平台渲染完成超时（任务 {external_id}，截止 {poll_deadline_s:g}s，"
                f"轮询间隔 {poll_interval_s:g}s）——任务可能仍在平台侧执行，"
                "可凭 external_id 复查后决定重试或取消"
            )
        sleep(poll_interval_s)
