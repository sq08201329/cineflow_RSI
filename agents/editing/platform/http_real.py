"""真实渲染服务适配器（B 路径**协议实现**，C12，T718：骨架 → 本批落地）。

## 实现的是什么（诚实登记，宪章原则六）

**协议实现**：实现在 `core/platform_http.py` 定死的请求形态 + 错误映射 + 成本纪律，
**不是"某家真实厂商的对接"**——真实接入仍需按厂商 API 调整路径/鉴权/字段（属配置或
小改）。**"能连本地 stub"不等于"B 路径已验证"**：真实凭证、真实厂商、真实计费均未跑过；
本地端到端验证形态见 `tests/integration/test_http_real_stub.py`。

## 协议（本环节用到的端点）

```
POST {base}/estimate {"params": <规范化渲染参数>} → {"estimated_cost_usd"}
POST {base}/jobs {"params": <同上>, "idempotency_key": <key>}
  → 202 {"external_id", "estimated_cost_usd", ["params_hash", "job_id"]}
GET  {base}/jobs/{id} → {"status": "pending|running|succeeded|failed",
                         "estimated_cost_usd", "actual_cost_usd", "error"}
GET  {base}/jobs/{id}/artifact → 成片 mp4 原样字节 + `X-Artifact-Meta`（base64(JSON)）
GET  {base}/health（只读探测，ops/check_credentials.py 的 PROBE_PATHS 口径）
```

**渲染规格（分辨率/帧率/价目）由平台侧配置承载**：本环节接口签名（C10）只给
`edl` 与 `shots`，无 cfg 注入点——适配器不本地编造规格，真实接入时以平台侧配置为准
（若要"规格随调用注入"，属接口变更而非适配器内部改法）。规范化参数（`render_params`）
含 EDL + 镜头库（镜头 id/素材哈希/时长/分区 + 音轨引用）。

## 与契约套件一致的语义（同 SimulatedEditRenderer）

- **幂等**：幂等键由规范化参数确定性派生（同 EDL 重复 render 命中**同一平台任务**，
  0 重复扣费）；同键的 external_id 由平台返回，适配器不自行编造；
- **成本**：`estimate` 走平台 `/estimate`（**本地零估算**）；实际扣费取自终态响应，
  `actual > estimated` 显式报错（账目异常，不静默接受）；
- **元数据**：仅透传平台返回的 `X-Artifact-Meta`；四键（duration_ms /
  shot_durations_ms / transitions / has_audio）缺任一即 unavailable；
- **错误分型**：429→RateLimitedError、平台终态 failed→RenderError（带平台错误详情）、
  其余 4xx→RenderError、5xx/连接失败/超时/响应不可解析→UnavailableError；
- **超时**：`request_timeout_s`（单次请求，默认 30s）+ `poll_deadline_s`（等待渲染完成，
  默认 300s），两者都归 UnavailableError 并在文案注明；超时不当作"任务失败"。

## 协议假设（真实接入时按厂商 API 校准的字段）

- 状态机取值为本协议登记的四档，未登记状态一律 UnavailableError（不静默猜）；
- 元数据走响应头 `X-Artifact-Meta`（base64(JSON)）：真实厂商多把元数据放 JSON 体或
  另立查询端点，属路径调整（协议实现不为此新增本地推断）。

凭证经环境变量注入（EDIT_RENDER_BASE_URL / EDIT_RENDER_API_KEY）；
无凭证构造即 UnavailableError——不假装渲染（宪章原则六）。
"""

import os

from agents.editing.edl import EditDecisionList
from agents.editing.platform.base import (
    RateLimitedError,
    RenderedFilm,
    RenderError,
    UnavailableError,
)
from agents.editing.shots import ShotLibrary
from core.platform_http import (
    HttpErrors,
    PlatformHttpClient,
    await_succeeded,
    decode_artifact_meta,
    enforce_actual_le_estimate,
    idempotency_key,
    optional_cost,
    required_cost,
    required_str,
)

# 错误族注入：429 / 其余 4xx / 5xx（+连接失败超时）→ 既有 RenderError 分型
_ERRORS = HttpErrors(
    rate_limited=RateLimitedError,
    unavailable=UnavailableError,
    invalid=RenderError,
    anomaly=RenderError,  # 账目异常（actual > estimated）：拒绝入账，不静默接受
)

# 元数据必含四键（C10 评估器输入；缺键即拒，不静默降级）
_REQUIRED_META_KEYS = ("duration_ms", "shot_durations_ms", "transitions", "has_audio")

_IDEMPOTENCY_NAMESPACE = "edit-render"

DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_POLL_DEADLINE_S = 300.0


def render_params(edl: EditDecisionList, shots: ShotLibrary) -> dict:
    """规范化渲染参数（协议的一部分）：EDL + 镜头库引用。

    只送**素材引用**（artifact_hash）而不再送素材本体：素材已内容寻址落库，
    平台按哈希取件（同 001 内容寻址口径）；幂等键由本参数的规范化指纹派生。
    """
    return {
        "edl": edl.to_dict(),
        "shots": [
            {
                "shot_id": shot.shot_id,
                "artifact_hash": shot.artifact_hash,
                "duration_ms": shot.duration_ms,
                "scene_id": shot.scene_id,
            }
            for shot in shots.shots
        ],
        "audio_tracks": list(shots.audio_tracks),
    }


class HttpRealEditRender:
    """真实渲染服务协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    ENV_PREFIX = "EDIT_RENDER"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        poll_deadline_s: float = DEFAULT_POLL_DEADLINE_S,
    ) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实渲染服务缺凭证：需要 EDIT_RENDER_BASE_URL / EDIT_RENDER_API_KEY"
            )
        self._client = PlatformHttpClient(
            base_url, api_key, errors=_ERRORS, timeout_s=request_timeout_s
        )
        self._poll_interval_s = float(poll_interval_s)
        self._poll_deadline_s = float(poll_deadline_s)

    @classmethod
    def from_env(cls) -> "HttpRealEditRender":
        return cls(
            os.environ.get(f"{cls.ENV_PREFIX}_BASE_URL", ""),
            os.environ.get(f"{cls.ENV_PREFIX}_API_KEY", ""),
        )

    def estimate(self, edl: EditDecisionList, shots: ShotLibrary) -> float:
        """预估成本（USD）：**平台估价为准**，本地零估算（预算门禁的校验依据）。"""
        payload = self._client.post_json("/estimate", {"params": render_params(edl, shots)})
        return required_cost(
            payload, "estimated_cost_usd", errors=_ERRORS, where="POST /estimate 响应"
        )

    def render(self, edl: EditDecisionList, shots: ShotLibrary) -> RenderedFilm:
        """提交 → 轮询至终态 → 取成片与元数据（昂贵动作仅经此调用，原则三）。"""
        params = render_params(edl, shots)
        submitted = self._client.post_json(
            "/jobs",
            {"params": params, "idempotency_key": idempotency_key(_IDEMPOTENCY_NAMESPACE, params)},
        )
        where = "POST /jobs 响应"
        external_id = required_str(submitted, "external_id", errors=_ERRORS, where=where)
        estimated = required_cost(submitted, "estimated_cost_usd", errors=_ERRORS, where=where)

        # 轮询至终态（failed 抛 RenderError）；实际扣费在取件后查询——取件即入账
        await_succeeded(
            self._client,
            external_id,
            errors=_ERRORS,
            failed_error=RenderError,
            poll_interval_s=self._poll_interval_s,
            poll_deadline_s=self._poll_deadline_s,
        )
        raw, headers = self._client.get_bytes(f"/jobs/{external_id}/artifact")
        metadata = decode_artifact_meta(
            headers,
            errors=_ERRORS,
            where=f"GET /jobs/{external_id}/artifact 响应",
        )
        missing = sorted(set(_REQUIRED_META_KEYS) - set(metadata))
        if missing:
            raise UnavailableError(
                f"平台渲染元数据缺必含键（任务 {external_id}）：{missing}——"
                "元数据是评估器输入，不静默降级为空元数据"
            )
        return RenderedFilm(
            mp4_bytes=raw,
            metadata=metadata,
            actual_cost_usd=self._booked_cost(external_id, estimated),
        )

    def _booked_cost(self, external_id: str, estimated: float) -> float:
        """取件后查询实际扣费（"取件即入账"时序，同视觉环节 job_actual_cost 的调用顺序）。

        实际与预估都取自平台响应（本地零估算）；actual > estimated 显式报错。
        """
        payload = self._client.get_json(f"/jobs/{external_id}")
        where = f"GET /jobs/{external_id} 响应"
        return enforce_actual_le_estimate(
            optional_cost(payload, "actual_cost_usd", errors=_ERRORS, where=where) or 0.0,
            optional_cost(payload, "estimated_cost_usd", errors=_ERRORS, where=where) or estimated,
            errors=_ERRORS,
            where=where,
        )
