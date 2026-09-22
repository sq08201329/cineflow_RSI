"""真实预演渲染服务适配器（B 路径**协议实现**，C12，T818：骨架 → 本批落地）。

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
GET  {base}/jobs/{id}/artifact → animatic mp4 原样字节 + `X-Artifact-Meta`（base64(JSON)）
GET  {base}/health（只读探测，ops/check_credentials.py 的 PROBE_PATHS 口径）
```

渲染规格/价目**随调用注入**（C10 的 `cfg` 参数 = StoryboardConfig）：规范化参数
（`render_params`）含 ShotList + 剧本段落 + `cfg.render` + 镜头语法规则库 + 情绪向量表——
平台侧据此渲染，适配器自身**无形态硬编码、不本地编造规格**。

## 与契约套件一致的语义（同 SimulatedStoryboardRenderer）

- **幂等**：幂等键由规范化参数确定性派生（同 ShotList 重复 render 命中**同一平台任务**，
  0 重复扣费）；同键的 external_id 由平台返回，适配器不自行编造；
- **成本**：`estimate` 走平台 `/estimate`（**本地零估算**）；实际扣费取自终态响应，
  `actual > estimated` 显式报错（账目异常，不静默接受）；
- **元数据**：仅透传平台返回的 `X-Artifact-Meta`；**键缺失即 unavailable**（元数据是
  评估器输入，不静默降级为空元数据）；
- **错误分型**：429→RateLimitedError、平台终态 failed→RenderError（带平台错误详情）、
  其余 4xx→RenderError、5xx/连接失败/超时/响应不可解析→UnavailableError；
- **超时**：`request_timeout_s`（单次请求，默认 30s）+ `poll_deadline_s`（等待渲染完成，
  默认 300s），两者都归 UnavailableError 并在文案注明；超时不当作"任务失败"——
  任务可能仍在平台侧执行（可凭 external_id 复查）。

## 协议假设（真实接入时按厂商 API 校准的字段）

- 状态机取值为本协议登记的四档，未登记状态一律 UnavailableError（不静默猜）；
- `params_hash`（运营表唯一键分量）平台回传则原样采用，未回传时兜底为规范化参数的
  BLAKE3 指纹（纯哈希，无费用/规格估算）；
- `frame_hashes` 的来源纪律（C11：必须来自 `board_render.storyboard_cards`，渲染件与
  评估输入同源）由**平台侧实现**承担——适配器只透传；本地 stub 用 `render_animatic`
  满足该纪律，真实厂商若帧口径不同属实现变更加版本，路径不变。

凭证经环境变量注入（STORYBOARD_RENDER_BASE_URL / STORYBOARD_RENDER_API_KEY）；
无凭证构造即 UnavailableError——不假装渲染（宪章原则六）。
"""

import os

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.platform.base import (
    RateLimitedError,
    RenderedAnimatic,
    RenderError,
    UnavailableError,
)
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList
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

# 元数据必含键（C10/C11 评估器输入；缺键即拒，不静默降级）
_REQUIRED_META_KEYS = (
    "duration_ms",
    "shot_count",
    "shot_sizes",
    "shot_durations_ms",
    "shot_frame_counts",
    "frame_hashes",
    "frames_hash",
    "emotions",
    "has_temp_audio",
    "fps",
)

_IDEMPOTENCY_NAMESPACE = "storyboard-render"

DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_POLL_DEADLINE_S = 300.0


def render_params(
    shotlist: ShotList, cfg: StoryboardConfig, *, script: ScriptSegment | None = None
) -> dict:
    """规范化渲染参数（协议的一部分）：ShotList + 渲染配置 + 规则库 + 情绪向量表（+ 剧本）。

    规格随调用注入（配置单一事实源），适配器不本地编造；幂等键由本参数的规范化
    指纹派生（同输入 → 同键 → 平台侧同一任务）。估价（`estimate`）不带剧本：
    价目只取决于镜头数与渲染价目，且估价端点只读（不创建任务、不计费）。
    """
    params = {
        "shotlist": shotlist.to_dict(),
        "render": dict(cfg.render),
        "shot_grammar": dict(cfg.shot_grammar),
        "emotion_vectors": dict(cfg.emotion_vectors),
    }
    if script is not None:
        params["script"] = script.to_dict()
    return params


class HttpRealStoryboardRender:
    """真实预演渲染服务协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    ENV_PREFIX = "STORYBOARD_RENDER"

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
                "真实预演渲染服务缺凭证：需要 STORYBOARD_RENDER_BASE_URL / "
                "STORYBOARD_RENDER_API_KEY"
            )
        self._client = PlatformHttpClient(
            base_url, api_key, errors=_ERRORS, timeout_s=request_timeout_s
        )
        self._poll_interval_s = float(poll_interval_s)
        self._poll_deadline_s = float(poll_deadline_s)

    @classmethod
    def from_env(cls) -> "HttpRealStoryboardRender":
        return cls(
            os.environ.get(f"{cls.ENV_PREFIX}_BASE_URL", ""),
            os.environ.get(f"{cls.ENV_PREFIX}_API_KEY", ""),
        )

    def estimate(self, shotlist: ShotList, cfg: StoryboardConfig) -> float:
        """预估成本（USD）：**平台估价为准**，本地零估算（预算门禁的校验依据）。"""
        payload = self._client.post_json("/estimate", {"params": render_params(shotlist, cfg)})
        return required_cost(
            payload, "estimated_cost_usd", errors=_ERRORS, where="POST /estimate 响应"
        )

    def render(
        self, shotlist: ShotList, script: ScriptSegment, cfg: StoryboardConfig
    ) -> RenderedAnimatic:
        """提交 → 轮询至终态 → 取工件与元数据（昂贵动作仅经此调用，原则三）。"""
        params = render_params(shotlist, cfg, script=script)
        submitted = self._client.post_json(
            "/jobs",
            {"params": params, "idempotency_key": idempotency_key(_IDEMPOTENCY_NAMESPACE, params)},
        )
        where = "POST /jobs 响应"
        external_id = required_str(submitted, "external_id", errors=_ERRORS, where=where)
        estimated = required_cost(submitted, "estimated_cost_usd", errors=_ERRORS, where=where)

        final = await_succeeded(
            self._client,
            external_id,
            errors=_ERRORS,
            failed_error=RenderError,
            poll_interval_s=self._poll_interval_s,
            poll_deadline_s=self._poll_deadline_s,
        )
        del final  # 终态已确认；实际扣费在取件后查询（取件即入账，见下）
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
        return RenderedAnimatic(
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
