"""网关成本折算与分解单测（功能 016 / T1614，先于实现编写）：契约 C6 + FR-009。

覆盖：两档案各一次调用 → 分解两条目且金额与各自价目相符；同档案多角色**按角色分开**
（可合并展示但来源可追溯）；零价目 → 0.0 且标注"零边际成本"；**mock 与 http 两路口径一致**
（都按档案价目折算，而非"mock 免费"）；报告层含**价目口径备注**与**"记账 ≠ 厂商账单"显式声明**；
缓存命中不重复计费也不计入分解的 token。
"""

import pytest

from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import BackendResult, LLMGateway
from core.llm_gateway.profiles import load_or_migrate
from core.llm_gateway.routing import Role


class _CountingBackend:
    """假后端：固定 token 数（可分别验算两条档案的折算值）。"""

    def __init__(self, *, prompt_tokens=1000, completion_tokens=500) -> None:
        self.call_count = 0
        self.models: list[str] = []
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        self.models.append(model)
        return BackendResult(
            text="伪文本",
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )


def _gateway(config_factory, *, backend=None) -> tuple[LLMGateway, object, object]:
    loaded = load_or_migrate(config_factory("multi"))
    backend = backend or _CountingBackend()
    return (
        LLMGateway(
            backend,
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        ),
        backend,
        loaded,
    )


class Test分解金额:
    def test_两档案各一次调用_两条目且金额各按自己价目(self, llm_profiles_config_factory):
        gateway, _, _ = _gateway(llm_profiles_config_factory)
        judged = gateway.chat("评审大纲", role=Role.JUDGE)  # deepseek-flash：0.0003/0.0012
        dreamed = gateway.chat("生成候选", role=Role.DREAMING_CANDIDATES)  # local-qwen：零价目

        breakdown = gateway.cost_breakdown()
        assert set(breakdown) == {"judge", "dreaming_candidates"}
        judge_entry = breakdown["judge"]["deepseek-flash"]
        assert judge_entry == {
            "calls": 1,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "cost_usd": pytest.approx(1000 / 1000 * 0.0003 + 500 / 1000 * 0.0012),
            "zero_marginal": False,
        }
        zero_entry = breakdown["dreaming_candidates"]["local-qwen"]
        assert zero_entry["cost_usd"] == 0.0 and zero_entry["zero_marginal"] is True
        assert judged.cost_usd > 0 and dreamed.cost_usd == 0.0

    def test_同档案多角色分开条目且可合并(self, llm_profiles_config_factory):
        gateway, _, _ = _gateway(llm_profiles_config_factory)
        gateway.chat("生成", role=Role.GENERATION)
        gateway.chat("评审", role=Role.JUDGE)
        breakdown = gateway.cost_breakdown()
        assert set(breakdown) == {"generation", "judge"}
        assert breakdown["generation"]["deepseek-flash"]["calls"] == 1
        assert breakdown["judge"]["deepseek-flash"]["calls"] == 1
        # 同档案可合并展示（报告层 by_profile 视图），来源仍可追溯（by_role 两侧都在）
        report = gateway.cost_report()
        assert report["by_profile"]["deepseek-flash"]["calls"] == 2
        assert report["by_profile"]["deepseek-flash"]["cost_usd"] == pytest.approx(
            breakdown["generation"]["deepseek-flash"]["cost_usd"]
            + breakdown["judge"]["deepseek-flash"]["cost_usd"]
        )

    def test_多次调用累积(self, llm_profiles_config_factory):
        gateway, _, _ = _gateway(llm_profiles_config_factory)
        gateway.chat("一", role=Role.JUDGE)
        gateway.chat("二", role=Role.JUDGE)
        entry = gateway.cost_breakdown()["judge"]["deepseek-flash"]
        assert entry["calls"] == 2 and entry["prompt_tokens"] == 2000
        assert entry["cost_usd"] == pytest.approx(2 * (0.0003 + 0.0006))


class Test两路口径一致:
    def test_mock_路径同样按档案价目折算(self, llm_profiles_config_factory):
        """mock 与 http 只差"谁产出 token"：**折算口径都来自档案价目**（费用非零、可对账）。"""
        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        mock_gateway = LLMGateway(
            MockBackend(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        http_like_gateway = LLMGateway(
            _CountingBackend(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        mock_result = mock_gateway.chat("同一段提示词", role=Role.GENERATION)
        http_result = http_like_gateway.chat("同一段提示词", role=Role.GENERATION)
        assert mock_result.cost_usd > 0 and http_result.cost_usd > 0
        for gateway, result in ((mock_gateway, mock_result), (http_like_gateway, http_result)):
            entry = gateway.cost_breakdown()["generation"]["deepseek-flash"]
            price = loaded.profiles["deepseek-flash"].prices
            expected = (
                result.usage["prompt_tokens"] / 1000 * price["prompt_per_1k"]
                + result.usage["completion_tokens"] / 1000 * price["completion_per_1k"]
            )
            assert entry["cost_usd"] == pytest.approx(expected)

    def test_缓存命中不入分解_token(self, llm_profiles_config_factory):
        gateway, backend, _ = _gateway(llm_profiles_config_factory)
        first = gateway.chat("同一段提示词", role=Role.JUDGE)
        cached = gateway.chat("同一段提示词", role=Role.JUDGE)
        assert first.cached is False and cached.cached is True and cached.cost_usd == 0.0
        assert backend.call_count == 1  # 缓存命中不再调后端
        entry = gateway.cost_breakdown()["judge"]["deepseek-flash"]
        assert entry["calls"] == 1  # 命中不重复计费、不重复计 token


class Test报告层:
    def test_报告含价目口径备注与记账非账单声明(self, llm_profiles_config_factory):
        gateway, _, loaded = _gateway(llm_profiles_config_factory)
        gateway.chat("评审", role=Role.JUDGE)
        report = gateway.cost_report()

        # FR-009 断言①：分条目带该档案的价目口径备注（可追溯"这钱是怎么算出来的"）
        judge = next(e for e in report["entries"] if e["role"] == "judge")
        assert judge["price_note"] == loaded.profiles["deepseek-flash"].price_note
        assert "峰时缓存未命中上限" in judge["price_note"]
        assert judge["prices"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        # FR-009 断言②：显式声明"记账 ≠ 厂商账单"（不把折算值冒充账单）
        assert "记账 ≠ 厂商账单" in report["accounting_note"]
        assert report["total_usd"] == pytest.approx(judge["cost_usd"])
        assert report["profile_snapshot_ref"] == gateway.profile_snapshot().ref

    def test_报告可序列化且不含密钥(self, llm_profiles_config_factory, llm_env):
        import json

        llm_env(DEEPSEEK_API_KEY="sk-should-never-appear")
        gateway, _, _ = _gateway(llm_profiles_config_factory)
        gateway.chat("评审", role=Role.JUDGE)
        text = json.dumps(gateway.cost_report(), ensure_ascii=False)
        assert "sk-should-never-appear" not in text
        assert "DEEPSEEK_API_KEY" in text
