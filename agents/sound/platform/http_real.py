"""真实声音生成适配器（B 路径**协议实现**，C11，T614：骨架 → 本批落地）。

## 实现的是什么（诚实登记，宪章原则六）

**协议实现**：实现在 `core/platform_http.py` 定死的请求形态 + 错误映射 + 成本纪律，
**不是"某家真实厂商的对接"**——真实接入仍需按厂商 API 调整路径/鉴权/字段（属配置或
小改）。**"能连本地 stub"不等于"B 路径已验证"**：真实凭证、真实厂商、真实计费均未跑过；
本地端到端验证形态见 `tests/integration/test_http_real_stub.py`。

## 协议（本环节用到的端点；三类型同一套，靠 gen_type 分账）

```
POST {base}/estimate {"params": <生成参数>} → {"estimated_cost_usd"}
POST {base}/jobs {"params": <同上>, "idempotency_key": <key>}
  → 202 {"external_id", "estimated_cost_usd", "status", ["params_hash", "job_id"]}
GET  {base}/jobs/{id} → {"status": "pending|running|succeeded|failed",
                         "estimated_cost_usd", "actual_cost_usd", "error"}
GET  {base}/jobs/{id}/artifact → **wav 原样字节** + `X-Artifact-Meta`（base64(JSON)）
GET  {base}/health（只读探测，ops/check_credentials.py 的 PROBE_PATHS 口径）
```

## 与契约套件一致的语义（同 Simulated*Gen，受 tests/contract 同一套件约束）

- **gen_type 由类属性权威声明**：请求参数 = 调用方 params + 本适配器的 `gen_type`
  （三类型各自 adapter，不让调用方把类型传错，也不做形态判断）；
- **幂等**：幂等键由规范化参数（含 gen_type）确定性派生——同参数重复 generate
  命中**同一平台任务**（0 重复扣费）；external_id 由平台返回，适配器不自行编造；
- **成本**：`estimate` 走平台 `/estimate`（**本地零估算**）；实际扣费取自终态响应，
  `actual > estimated` 显式报错（账目异常，不静默接受）；
- **元数据**：仅透传平台返回的 `X-Artifact-Meta`；**必含键缺失即拒**
  （`loudness_gain_db` + 类型标记：tts→`cer_injected` / sfx→`event_times_ms` /
  music→`emotion_vector`）——四评估器的确定性输入，不静默降级为空元数据；
- **错误分型**：429→RateLimitedError、平台终态 failed→SoundGenError（带平台错误详情）、
  其余 4xx→InvalidParamsError、5xx/连接失败/超时/响应不可解析→UnavailableError；
- **超时**：`request_timeout_s`（单次请求，默认 30s）+ `poll_deadline_s`（等待生成完成，
  默认 300s），两者都归 UnavailableError 并在文案注明超时值。

## 协议假设（真实接入时按厂商 API 校准的字段）

- **音频规格（采样率/声道/位深）由平台侧配置承载**：本环节接口签名（C9 的
  `estimate/generate(params)`）无 cfg 注入点，适配器不本地编造规格；调用方需要指定
  规格时把它放进 `params`（适配器原样透传）。契约套件对"返回 wav 的采样率符合
  SoundConfig"的断言由平台侧配置满足；
- 三类型的**注入标记**（错字率/事件时间/情绪向量）须由平台回传：真实厂商若不提供，
  接入时按其 API 映射（或改走平台的真实质量指标）——属实现变更加版本，路径不变；
- 状态机取值为本协议登记的四档，未登记状态一律 UnavailableError（不静默猜）。

凭证经环境变量注入（`SOUND_TTS_*` / `SOUND_SFX_*` / `SOUND_MUSIC_*`）；
无凭证构造即 UnavailableError——不假装生成（宪章原则六）。
"""

import os

from agents.sound.platform.base import (
    GeneratedAudio,
    InvalidParamsError,
    RateLimitedError,
    SoundGenError,
    UnavailableError,
)
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

# 错误族注入：429 / 其余 4xx / 5xx（+连接失败超时）→ 既有 SoundGenError 分型
_ERRORS = HttpErrors(
    rate_limited=RateLimitedError,
    unavailable=UnavailableError,
    invalid=InvalidParamsError,
    anomaly=SoundGenError,  # 账目异常（actual > estimated）：拒绝入账，不静默接受
)

# 类型标记（C9 评估器输入）：三类型各自的注入属性
_TYPE_MARKERS = {
    "tts": "cer_injected",
    "sfx": "event_times_ms",
    "music": "emotion_vector",
}

_IDEMPOTENCY_NAMESPACE = "sound-gen"

DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_POLL_DEADLINE_S = 300.0


class _HttpRealSoundGenBase:
    """三类型真实适配器共用实现：ENV_PREFIX/gen_type 由子类声明（成本分账粒度）。"""

    ENV_PREFIX: str = ""
    gen_type: str = ""

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
                f"真实声音平台缺凭证：需要 {self.ENV_PREFIX}_BASE_URL / {self.ENV_PREFIX}_API_KEY"
            )
        self._client = PlatformHttpClient(
            base_url, api_key, errors=_ERRORS, timeout_s=request_timeout_s
        )
        self._poll_interval_s = float(poll_interval_s)
        self._poll_deadline_s = float(poll_deadline_s)

    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get(f"{cls.ENV_PREFIX}_BASE_URL", ""),
            os.environ.get(f"{cls.ENV_PREFIX}_API_KEY", ""),
        )

    def estimate(self, params: dict) -> float:
        """预估成本（USD）：**平台估价为准**，本地零估算（预算门禁的校验依据）。"""
        payload = self._client.post_json("/estimate", {"params": self._request_params(params)})
        return required_cost(
            payload, "estimated_cost_usd", errors=_ERRORS, where="POST /estimate 响应"
        )

    def generate(self, params: dict) -> GeneratedAudio:
        """提交 → 轮询至终态 → 取 wav 与元数据（昂贵动作仅经此调用，原则三）。"""
        request_params = self._request_params(params)
        submitted = self._client.post_json(
            "/jobs",
            {
                "params": request_params,
                "idempotency_key": idempotency_key(_IDEMPOTENCY_NAMESPACE, request_params),
            },
        )
        where = "POST /jobs 响应"
        external_id = required_str(submitted, "external_id", errors=_ERRORS, where=where)
        estimated = required_cost(submitted, "estimated_cost_usd", errors=_ERRORS, where=where)

        # 轮询至终态（failed 抛 SoundGenError）；实际扣费在取件后查询——取件即入账
        await_succeeded(
            self._client,
            external_id,
            errors=_ERRORS,
            failed_error=SoundGenError,
            poll_interval_s=self._poll_interval_s,
            poll_deadline_s=self._poll_deadline_s,
        )
        raw, headers = self._client.get_bytes(f"/jobs/{external_id}/artifact")
        metadata = decode_artifact_meta(
            headers, errors=_ERRORS, where=f"GET /jobs/{external_id}/artifact 响应"
        )
        missing = sorted(self._required_keys() - set(metadata))
        if missing:
            raise UnavailableError(
                f"平台生成元数据缺必含键（任务 {external_id}）：{missing}——"
                "注入标记是评估器的确定性输入，不静默降级为空元数据"
            )
        return GeneratedAudio(
            wav_bytes=raw,
            metadata=metadata,
            actual_cost_usd=self._booked_cost(external_id, estimated),
        )

    # ---- 内部 ----

    def _request_params(self, params: dict) -> dict:
        """请求参数 = 调用方参数 + 本适配器的 gen_type（类属性是类型权威，防调用方传错）。"""
        return {**params, "gen_type": self.gen_type}

    def _required_keys(self) -> set[str]:
        return {"loudness_gain_db", _TYPE_MARKERS[self.gen_type]}

    def _booked_cost(self, external_id: str, estimated: float) -> float:
        """取件后查询实际扣费（"取件即入账"时序）：actual > estimated 显式报错。"""
        payload = self._client.get_json(f"/jobs/{external_id}")
        where = f"GET /jobs/{external_id} 响应"
        return enforce_actual_le_estimate(
            optional_cost(payload, "actual_cost_usd", errors=_ERRORS, where=where) or 0.0,
            optional_cost(payload, "estimated_cost_usd", errors=_ERRORS, where=where) or estimated,
            errors=_ERRORS,
            where=where,
        )


class HttpRealTTSGen(_HttpRealSoundGenBase):
    """真实 TTS 协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    ENV_PREFIX = "SOUND_TTS"
    gen_type = "tts"


class HttpRealSFXGen(_HttpRealSoundGenBase):
    """真实音效协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    ENV_PREFIX = "SOUND_SFX"
    gen_type = "sfx"


class HttpRealMusicGen(_HttpRealSoundGenBase):
    """真实配乐协议适配器（凭证缺失即不可用；本地 stub 端到端已验证）。"""

    ENV_PREFIX = "SOUND_MUSIC"
    gen_type = "music"
