"""厂商缓存命中维度单测（功能 019 / T1915 先于实现编写）：契约 C5 / C7。

- `BackendResult.cached_prompt_tokens` 为**末位追加**的可选字段（既有后端与测试构造零变化）；
- 有值且 ≥ 0 ⇒ 取 `*_hit` 格并在结果上标注命中数；缺失/`None` ⇒ 取 `*_miss` 格 + 口径备注
  「厂商未报告命中 token（按未命中计）」——**不按 0 计**（保守高估，不是漏记）；
- `cached_prompt_tokens > prompt_tokens` ⇒ **报错**（不静默钳制），且本次调用费用照记（原则二）；
- 本地网关缓存命中：零成本、零后端调用、不占额、不进任何格位（与厂商缓存维度正交）。
"""

import pytest

from core.llm_gateway.gateway import BackendResult, LLMGateway, TransientBackendError
from core.llm_gateway.profiles import load_or_migrate
from core.llm_gateway.routing import Role

_CELLS = {
    "peak_miss": {"prompt_per_1k": 0.008, "completion_per_1k": 0.016},
    "peak_hit": {"prompt_per_1k": 0.002, "completion_per_1k": 0.004},
    "off_peak_miss": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
    "off_peak_hit": {"prompt_per_1k": 0.0005, "completion_per_1k": 0.001},
}


class _Backend:
    """假后端：可声明是否携带命中 token 字段（`reported=None` = **旧后端实现**不设该字段）。"""

    def __init__(self, *, reported: int | None = None, with_field: bool = True) -> None:
        self.call_count = 0
        self.prompt_tokens = 1000
        self.completion_tokens = 500
        self._reported = reported
        self._with_field = with_field

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        result = BackendResult(
            text="伪文本",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )
        if self._with_field:
            result = BackendResult(
                text=result.text,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cached_prompt_tokens=self._reported,
            )
        return result


class _Calendar:
    """渠道日历桩：恒判谷时（命中维度与峰谷维度互不干扰）。"""

    def __init__(self) -> None:
        self.attribution = "call_start"
        self.timezone = "Asia/Shanghai"
        self.windows = ()

    def is_peak(self, moment) -> bool:
        return False


def _gateway(backend, *, guard=None):
    load = load_or_migrate(
        {
            "llm": {
                "profiles": {
                    "p1": {
                        "base_url": "https://api.example.invalid",
                        "api_key_env": "EXAMPLE_API_KEY",
                        "prices": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
                        "price_note": "夹具",
                        "price_matrix": _CELLS,
                    }
                },
                "roles": {},
                "default_profile": "p1",
            }
        }
    )
    return LLMGateway(
        backend,
        price_book={},
        sleep=lambda _: None,
        profiles=load,
        peak_windows=_Calendar(),
        spend_guard=guard,
        channel_id="channel-fixture" if guard is not None else "",
    )


class _FakeReservation:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def settle(self, actual_usd: float) -> dict:
        self._sink.append(actual_usd)
        return {}


class _RecordingGuard:
    """记录门禁调用（用于证明本地缓存命中**不过门禁**）。"""

    channel_id = "channel-fixture"

    def __init__(self) -> None:
        self.checks: list = []
        self.settled: list[float] = []

    def check(self, request):
        self.checks.append(request)
        return _FakeReservation(self.settled)


class Test命中档次:
    def test_后端报命中token取_hit_格并标注(self):
        backend = _Backend(reported=300)
        gateway = _gateway(backend)
        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert result.cell_key == "off_peak_hit"
        assert result.cached_prompt_tokens == 300
        assert result.cost_usd == pytest.approx(1000 / 1000 * 0.0005 + 500 / 1000 * 0.001)

    def test_未报或_None_取_miss_格且记口径备注_不按_0_计(self):
        for reported, with_field in ((None, True), (None, False)):
            backend = _Backend(reported=reported, with_field=with_field)
            gateway = _gateway(backend)
            result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
            assert result.cell_key == "off_peak_miss"
            assert result.cached_prompt_tokens is None
            assert "厂商未报告命中 token（按未命中计）" in result.pricing_note
            # 不按 0 计：命中格价目更低，取 miss 格即"标了价"的保守高估
            assert result.cost_usd == pytest.approx(1000 / 1000 * 0.001 + 500 / 1000 * 0.002)
            assert result.cost_usd > 1000 / 1000 * _CELLS["off_peak_hit"]["prompt_per_1k"]

    def test_命中数为_0_仍属已报告_取_hit_格(self):
        backend = _Backend(reported=0)
        gateway = _gateway(backend)
        result = gateway.chat("提示词", role=Role.GENERATION)
        assert result.cell_key == "off_peak_hit" and result.cached_prompt_tokens == 0
        assert "未报告" not in result.pricing_note

    def test_命中数大于_prompt_数即报错且不静默钳制(self):
        backend = _Backend(reported=1001)  # prompt_tokens=1000
        gateway = _gateway(backend)
        with pytest.raises(TransientBackendError, match="命中"):
            gateway.chat("提示词", role=Role.GENERATION)
        # 费用照记（原则二：这次调用确实发生过）；缓存不写（再调用仍打后端）
        assert gateway.total_cost_usd > 0 and gateway.call_count == 1
        with pytest.raises(TransientBackendError):
            gateway.chat("提示词", role=Role.GENERATION)
        assert backend.call_count == 2

    def test_读数非法_负数即报错(self):
        gateway = _gateway(_Backend(reported=-1))
        with pytest.raises(TransientBackendError, match="命中"):
            gateway.chat("提示词", role=Role.GENERATION)

    def test_旧后端实现不设该字段_行为与既有单测一致(self):
        """旧后端（不设 `cached_prompt_tokens`）⇒ 未报告口径，既有调用形态零改动。"""
        backend = _Backend(with_field=False)
        gateway = _gateway(backend)
        result = gateway.chat("提示词", role=Role.GENERATION)
        assert result.cached_prompt_tokens is None
        assert result.usage == {"prompt_tokens": 1000, "completion_tokens": 500}
        assert result.cost_usd == pytest.approx(1000 / 1000 * 0.001 + 500 / 1000 * 0.002)


class Test本地缓存与厂商缓存正交:
    def test_本地缓存命中零成本零后端调用且不占额(self):
        guard = _RecordingGuard()
        backend = _Backend(reported=300)
        gateway = _gateway(backend, guard=guard)
        first = gateway.chat("同一提示词", role=Role.GENERATION, stage="screenplay")
        assert len(guard.checks) == 1  # 真实调用过一次门禁
        second = gateway.chat("同一提示词", role=Role.GENERATION, stage="screenplay")
        assert second.cached is True and second.cost_usd == 0.0
        assert second.cell_key == ""  # 不进任何格位
        assert second.cached_prompt_tokens is None  # 本地命中与厂商命中不混算
        assert len(guard.checks) == 1  # 本地命中**不过门禁**（零成本 ⇒ 不占额）
        assert guard.settled == [pytest.approx(first.cost_usd)]  # 只结算真实调用那一次
        assert backend.call_count == 1 and gateway.call_count == 1

    def test_本地缓存命中备注标注零成本(self):
        gateway = _gateway(_Backend(reported=None))
        gateway.chat("同一提示词", role=Role.GENERATION)
        cached = gateway.chat("同一提示词", role=Role.GENERATION)
        assert "本地" in cached.pricing_note and "零成本" in cached.pricing_note
