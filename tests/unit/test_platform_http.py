"""协议层单测（core/platform_http.py）：纯函数与映射分支，**零网络**。

集成面（真起 stub 服务端、端到端）见 tests/integration/test_http_real_stub.py；
本文件只钉协议层的解析/映射/纪律细节，便于快速回归：

- 响应片段截断、规范化参数与指纹/幂等键派生（确定性、命名空间隔离）；
- 金额字段解析（缺失/负数/bool/字符串一律拒）、actual ≤ estimated 纪律（超出显式报错）；
- HTTP 状态 → 错误族映射（429/409/400/503，构造 HTTPError 即可，不发请求）；
- 响应不可解析（JSON 体/工件元数据头）→ 可诊断错误且含片段；
- await_succeeded 的轮询语义（终态/失败/未登记状态/超时），sleep 注入不真等。
"""

import base64
import io
import json
import urllib.error

import pytest

from core.platform_http import (
    HttpErrors,
    PlatformHttpClient,
    await_succeeded,
    canonical_params,
    decode_artifact_meta,
    enforce_actual_le_estimate,
    idempotency_key,
    optional_cost,
    optional_str,
    params_fingerprint,
    required_cost,
    required_str,
    snippet,
)


class _RateLimited(Exception):
    pass


class _Unavailable(Exception):
    pass


class _Invalid(Exception):
    pass


class _NotReady(Exception):
    pass


class _Anomaly(Exception):
    pass


ERRORS = HttpErrors(
    rate_limited=_RateLimited,
    unavailable=_Unavailable,
    invalid=_Invalid,
    conflict=_NotReady,
    anomaly=_Anomaly,
)


def _client(**kwargs) -> PlatformHttpClient:
    return PlatformHttpClient(
        "http://127.0.0.1:1", "key", errors=kwargs.pop("errors", ERRORS), **kwargs
    )


class Test片段与指纹:
    def test_响应片段折叠空白并截断(self):
        assert snippet(b"  a\n b\tc ") == "a b c"
        long = snippet(b"x" * 500)
        assert long.startswith("x" * 200) and "截断" in long

    def test_规范化参数与指纹确定(self):
        assert canonical_params({"b": 1, "a": "中"}) == '{"a": "中", "b": 1}'
        assert params_fingerprint({"a": 1, "b": 2}) == params_fingerprint({"b": 2, "a": 1})
        assert len(params_fingerprint({"a": 1})) == 64

    def test_幂等键带命名空间且同参数同键(self):
        first = idempotency_key("storyboard-render", {"a": 1})
        assert first.startswith("storyboard-render:")
        assert first == idempotency_key("storyboard-render", {"a": 1})
        assert first != idempotency_key("edit-render", {"a": 1})
        assert first != idempotency_key("storyboard-render", {"a": 2})


class Test字段解析:
    def test_必填字符串_缺失或类型不符即拒(self):
        assert required_str({"k": "v"}, "k", errors=ERRORS, where="W") == "v"
        for payload in ({}, {"k": ""}, {"k": 3}, {"k": None}):
            with pytest.raises(_Unavailable, match="缺必填字段"):
                required_str(payload, "k", errors=ERRORS, where="W")

    def test_可选字符串(self):
        assert optional_str({"k": "v"}, "k") == "v"
        assert optional_str({"k": ""}, "k") is None
        assert optional_str({}, "k") is None

    def test_必填金额_非数值负数布尔即拒(self):
        assert required_cost({"c": 1.5}, "c", errors=ERRORS, where="W") == 1.5
        for payload in ({}, {"c": "1.5"}, {"c": True}, {"c": -0.1}, {"c": None}):
            with pytest.raises(_Unavailable, match="金额字段"):
                required_cost(payload, "c", errors=ERRORS, where="W")

    def test_可选金额_null_为尚未扣费_非法值即拒(self):
        assert optional_cost({"c": None}, "c", errors=ERRORS, where="W") is None
        assert optional_cost({}, "c", errors=ERRORS, where="W") is None
        assert optional_cost({"c": 0.0}, "c", errors=ERRORS, where="W") == 0.0
        with pytest.raises(_Unavailable, match="金额字段"):
            optional_cost({"c": "x"}, "c", errors=ERRORS, where="W")

    def test_actual_不超预估_超出显式报错(self):
        assert enforce_actual_le_estimate(0.5, 0.5, errors=ERRORS, where="W") == 0.5
        with pytest.raises(_Anomaly, match="超过预估"):
            enforce_actual_le_estimate(0.51, 0.5, errors=ERRORS, where="W")

    def test_错误族缺省回落(self):
        fallback = HttpErrors(rate_limited=_RateLimited, unavailable=_Unavailable, invalid=_Invalid)
        assert fallback.for_conflict() is _Invalid
        assert fallback.for_anomaly() is _Unavailable


class Test错误映射:
    """不发请求：直接构造 HTTPError 走映射分支。"""

    def _error(self, code: int, body: bytes = b'{"error": "boom"}') -> urllib.error.HTTPError:
        return urllib.error.HTTPError("http://x/jobs", code, "Injected", {}, io.BytesIO(body))

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (429, _RateLimited),
            (409, _NotReady),
            (400, _Invalid),
            (401, _Invalid),
            (500, _Unavailable),
        ],
    )
    def test_状态码归族(self, code, expected):
        raised = _client()._http_error(self._error(code), "/jobs")
        assert isinstance(raised, expected)
        assert str(code) in str(raised)
        assert "boom" in str(raised)  # 文案含响应片段（可诊断）

    def test_响应体为空仍给出映射错误(self):
        raised = _client()._http_error(self._error(503, b""), "/jobs")
        assert isinstance(raised, _Unavailable)

    def test_响应不可解析_归不可用且带片段(self):
        with pytest.raises(_Unavailable, match="响应不可解析"):
            _client()._json(b"<html>nope</html>", "/jobs")

    def test_响应非对象_归不可用(self):
        with pytest.raises(_Unavailable, match="期望 JSON 对象"):
            _client()._json(json.dumps([1, 2]).encode(), "/jobs")


class Test工件元数据头:
    def _headers(self, payload) -> dict:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return {"x-artifact-meta": base64.b64encode(raw).decode("ascii")}

    def test_解析成功(self):
        assert decode_artifact_meta(self._headers({"fps": 8}), errors=ERRORS, where="W") == {
            "fps": 8
        }

    def test_缺失头即拒(self):
        with pytest.raises(_Unavailable, match="X-Artifact-Meta"):
            decode_artifact_meta({}, errors=ERRORS, where="W")

    def test_非_base64_或非_JSON_即拒(self):
        with pytest.raises(_Unavailable, match="base64"):
            decode_artifact_meta({"x-artifact-meta": "!!!not-base64!!!"}, errors=ERRORS, where="W")
        with pytest.raises(_Unavailable, match="非合法 JSON"):
            decode_artifact_meta(self._headers(b"\xff\xfe\x00"), errors=ERRORS, where="W")
        with pytest.raises(_Unavailable, match="期望 JSON 对象"):
            decode_artifact_meta(self._headers([1, 2]), errors=ERRORS, where="W")


class _QueueClient:
    """假客户端：按序吐状态响应（不联网），记录请求路径。"""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.paths: list[str] = []

    def get_json(self, path: str) -> dict:
        self.paths.append(path)
        return self._payloads.pop(0)

    def post_json(self, path: str, payload: dict) -> dict:  # pragma: no cover - 协议层不用
        raise AssertionError("await_succeeded 只应 GET")


class Test等待终态:
    def test_推进到_succeeded_即返回末次响应(self):
        client = _QueueClient(
            [{"status": "pending"}, {"status": "running"}, {"status": "succeeded"}]
        )
        payload = await_succeeded(
            client,
            "ext-1",
            errors=ERRORS,
            failed_error=_Invalid,
            poll_interval_s=0,
            poll_deadline_s=10,
            sleep=lambda _: None,
        )
        assert payload["status"] == "succeeded"
        assert client.paths == ["/jobs/ext-1"] * 3

    def test_终态_failed_抛注入的失败类型并带平台详情(self):
        client = _QueueClient([{"status": "failed", "error": "GPU 掉线"}])
        with pytest.raises(_Invalid, match="GPU 掉线"):
            await_succeeded(
                client,
                "ext-2",
                errors=ERRORS,
                failed_error=_Invalid,
                poll_interval_s=0,
                poll_deadline_s=10,
                sleep=lambda _: None,
            )

    def test_未登记状态即拒(self):
        client = _QueueClient([{"status": "queued"}])
        with pytest.raises(_Unavailable, match="未登记"):
            await_succeeded(
                client,
                "ext-3",
                errors=ERRORS,
                failed_error=_Invalid,
                poll_interval_s=0,
                poll_deadline_s=10,
                sleep=lambda _: None,
            )

    def test_超时归不可用并注明(self):
        client = _QueueClient([{"status": "running"}] * 50)
        with pytest.raises(_Unavailable, match="等待平台渲染完成超时"):
            await_succeeded(
                client,
                "ext-4",
                errors=ERRORS,
                failed_error=_Invalid,
                poll_interval_s=0,
                poll_deadline_s=0,  # 立即到期：不真等
                sleep=lambda _: None,
            )
