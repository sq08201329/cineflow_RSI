"""真实视频生成适配器（B 路径**协议实现**，T325：骨架 → 本批落地）。

## 实现的是什么（诚实登记，宪章原则六）

**协议实现**：实现在 `core/platform_http.py` 定死的请求形态 + 错误映射 + 成本纪律，
**不是"某家真实厂商的对接"**——真实接入仍需按厂商 API 调整路径/鉴权/字段（属配置或
小改）。**"能连本地 stub"不等于"B 路径已验证"**：真实凭证、真实厂商、真实计费均未跑过；
本地端到端验证形态见 `tests/integration/test_http_real_stub.py`（本地 loopback stub，
零外部网络、零凭证）。

## 协议（本环节用到的端点）

```
POST {base}/jobs {"params": gen_params, "idempotency_key": <key>}
  → 202 {"external_id", "estimated_cost_usd", "status", ["params_hash", "job_id"]}
GET  {base}/jobs/{id}
  → {"status": "pending|running|succeeded|failed", "estimated_cost_usd",
     "actual_cost_usd", "error"}
GET  {base}/jobs/{id}/artifact → mp4 原样字节
POST {base}/jobs/{id}/cancel
GET  {base}/health（只读探测，ops/check_credentials.py 的 PROBE_PATHS 口径）
```

## 与契约套件一致的语义（同 SimulatedVideoGen，受 tests/contract 同一套件约束）

- **状态机映射**：pending→SUBMITTED、running→GENERATING、succeeded→COMPLETED、
  failed→FAILED；未登记状态一律 UnavailableError（不静默猜）；
- **幂等**：`idempotency_key` 原样透传，同键重复提交返回**平台给的**同一 external_id
  （适配器不自行编造 external_id；去重语义在平台侧）；
- **成本**：预估取自 submit 响应、实际取自状态响应，**本地零估算**；
  `actual > estimated` 显式报错（账目异常，不静默接受）；
- **错误分型**：429→RateLimitedError、409（工件未就绪）→ArtifactNotReadyError、
  其余 4xx（含 401/403）→InvalidParamsError、5xx/连接失败/超时/响应不可解析→
  UnavailableError（超时文案注明超时值）；不泄漏 urllib/socket 异常类型；
- **超时**：构造参数 `request_timeout_s`（默认 30s 保守值；真实平台若更慢按部署调大）；
- **不阻塞**：`get_status` 是单次查询（不轮询不 sleep）——轮询节奏由调用方
  `agents.visual.clip.produce_clip` 决定（既有 5 次上限语义不变）。

## 协议假设（真实接入时按厂商 API 校准的字段）

- `params` 原样透传调用方给的 `gen_params`（含中文风格名，UTF-8 JSON）；
- `params_hash`：平台回传则原样采用（运营表唯一键分量），未回传时兜底为
  `core.platform_http.params_fingerprint(gen_params)`（纯哈希，无本地估算）；
- `job_id`：平台内部任务号，未回传时以 `external_id` 为准；
- 取消语义：取消后任务进入终态 failed（本协议不另设 cancelled 终态）。

凭证经环境变量注入（VISUAL_GEN_BASE_URL / VISUAL_GEN_API_KEY）；无凭证构造即
UnavailableError——不假装生成（宪章原则六）。
"""

import os

from agents.visual.platform.base import (
    ArtifactNotReadyError,
    GenJob,
    GenJobStatus,
    InvalidParamsError,
    RateLimitedError,
    UnavailableError,
)
from core.platform_http import (
    HttpErrors,
    PlatformHttpClient,
    enforce_actual_le_estimate,
    optional_cost,
    optional_str,
    params_fingerprint,
    required_cost,
    required_str,
)

# 错误族注入：429 / 409 / 其余 4xx / 5xx（+连接失败超时）→ 既有 VideoGenError 分型
_ERRORS = HttpErrors(
    rate_limited=RateLimitedError,
    unavailable=UnavailableError,
    invalid=InvalidParamsError,
    conflict=ArtifactNotReadyError,
    anomaly=UnavailableError,  # 账目异常（actual > estimated）：平台侧问题，按不可用收口
)

_STATUS_MAP = {
    "pending": GenJobStatus.SUBMITTED,
    "running": GenJobStatus.GENERATING,
    "succeeded": GenJobStatus.COMPLETED,
    "failed": GenJobStatus.FAILED,
}


def _status_of(value: object, *, where: str) -> GenJobStatus:
    status = _STATUS_MAP.get(str(value)) if isinstance(value, str) else None
    if status is None:
        raise UnavailableError(
            f"{where} 的平台状态未登记（实际 {value!r}）：本协议状态取值为 "
            f"{sorted(_STATUS_MAP)}——不静默猜测未知状态"
        )
    return status


class HttpRealVideoGen:
    """真实生成 API 协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        request_timeout_s: float = 30.0,
    ) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实生成平台缺凭证：需要 VISUAL_GEN_BASE_URL / VISUAL_GEN_API_KEY"
            )
        self._client = PlatformHttpClient(
            base_url, api_key, errors=_ERRORS, timeout_s=request_timeout_s
        )
        self._estimates: dict[str, float] = {}  # external_id → 平台给的预估（对照面）

    @classmethod
    def from_env(cls) -> "HttpRealVideoGen":
        return cls(
            os.environ.get("VISUAL_GEN_BASE_URL", ""),
            os.environ.get("VISUAL_GEN_API_KEY", ""),
        )

    def submit(self, gen_params: dict, *, idempotency_key: str) -> GenJob:
        """提交生成任务：幂等键原样透传，预估花费以响应为准。"""
        payload = self._client.post_json(
            "/jobs", {"params": gen_params, "idempotency_key": idempotency_key}
        )
        where = "POST /jobs 响应"
        external_id = required_str(payload, "external_id", errors=_ERRORS, where=where)
        estimated = required_cost(payload, "estimated_cost_usd", errors=_ERRORS, where=where)
        status = _status_of(
            payload.get("status", "pending"), where=f"{where}（external_id={external_id}）"
        )
        self._estimates[external_id] = estimated
        return GenJob(
            job_id=optional_str(payload, "job_id") or external_id,
            external_id=external_id,
            params_hash=optional_str(payload, "params_hash") or params_fingerprint(gen_params),
            estimated_cost_usd=estimated,
            status=status,
        )

    def get_status(self, external_id: str) -> GenJobStatus:
        """单次状态查询（不轮询、不 sleep）：轮询节奏由调用方决定。"""
        payload = self._job(external_id)
        return _status_of(payload.get("status"), where=f"GET /jobs/{external_id} 响应")

    def fetch_artifact(self, external_id: str) -> bytes:
        """取工件二进制体（mp4 原样）；未就绪（409）→ ArtifactNotReadyError。"""
        raw, _ = self._client.get_bytes(f"/jobs/{external_id}/artifact")
        return raw

    def job_actual_cost(self, external_id: str) -> float:
        """实际扣费查询（对账三方之一）：以平台状态响应为准，超预估即显式报错。"""
        payload = self._job(external_id)
        return enforce_actual_le_estimate(
            self._actual_of(external_id, payload),
            self._estimated_of(external_id, payload),
            errors=_ERRORS,
            where=f"GET /jobs/{external_id} 响应",
        )

    def cancel(self, external_id: str) -> None:
        """取消任务（已完成/已失败为无操作）；未知任务 → 4xx → InvalidParamsError。"""
        self._client.post_json(f"/jobs/{external_id}/cancel", {})

    # ---- 内部 ----

    def _job(self, external_id: str) -> dict:
        return self._client.get_json(f"/jobs/{external_id}")

    def _estimated_of(self, external_id: str, payload: dict) -> float:
        """状态响应给的预估；未给则回落到 submit 响应里的同一平台数字（不本地估算）。"""
        reported = optional_cost(
            payload, "estimated_cost_usd", errors=_ERRORS, where=f"任务 {external_id} 状态响应"
        )
        if reported is not None:
            self._estimates[external_id] = reported
            return reported
        cached = self._estimates.get(external_id)
        if cached is None:
            raise UnavailableError(
                f"任务 {external_id} 的预估花费缺失（状态响应未给且无此提交记录）"
                "——金额一律以平台响应为准，不从本地推断"
            )
        return cached

    def _actual_of(self, external_id: str, payload: dict) -> float:
        """实际扣费：null 表示尚未扣费（未发生即 0，不推断）。"""
        return (
            optional_cost(
                payload,
                "actual_cost_usd",
                errors=_ERRORS,
                where=f"任务 {external_id} 状态响应",
            )
            or 0.0
        )
