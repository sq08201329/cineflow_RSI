"""档案快照与单档案等价现状单测（功能 016 / T1609，先于实现编写）：契约 C7 的快照面。

覆盖：`profile_snapshot()` 字段齐全（档案 / 价目 / 价目口径备注 / 端点 host / 迁移说明，
**无密钥**）；快照指纹随价目变化（可追溯引用）；**单档案行为与现状等价**
（既有 `price_book=` 调用形态 → 快照来源 `legacy_price_book`，折算口径不变）。
"""

import json

import pytest

from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.llm_gateway.profiles import load_or_migrate

SNAPSHOT_KEYS = {
    "source",
    "profiles",
    "default_profile",
    "default_reason",
    "roles",
    "notes",
    "migration_notes",
}
PROFILE_KEYS = {
    "profile_id",
    "endpoint",
    "endpoint_source",
    "api_key_env",
    "prices",
    "price_note",
    "zero_marginal",
    "legacy_env",
}


def _gateway(*, profiles=None, price_book=None) -> LLMGateway:
    return LLMGateway(
        MockBackend(),
        price_book=price_book
        or {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        profiles=profiles,
    )


class Test快照字段:
    def test_字段齐全且含价目与备注(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        snapshot = _gateway(profiles=loaded).profile_snapshot().to_dict()

        assert SNAPSHOT_KEYS <= set(snapshot)
        assert snapshot["source"] == "llm_section"
        assert sorted(entry["profile_id"] for entry in snapshot["profiles"]) == [
            "deepseek-flash",
            "local-qwen",
        ]
        cloud = next(e for e in snapshot["profiles"] if e["profile_id"] == "deepseek-flash")
        assert PROFILE_KEYS <= set(cloud)
        assert cloud["prices"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        assert "峰时缓存未命中上限" in cloud["price_note"]  # 价目口径备注（FR-009 依据）
        assert cloud["endpoint"] == "https://api.deepseek.com"  # 端点只记 host
        assert cloud["legacy_env"] is False
        local = next(e for e in snapshot["profiles"] if e["profile_id"] == "local-qwen")
        assert local["endpoint"] == "LOCAL_LLM_BASE_URL" and local["zero_marginal"] is True
        assert snapshot["roles"]["dreaming_candidates"] == "local-qwen"
        assert snapshot["default_profile"] == "deepseek-flash"

    def test_快照不含密钥(self, llm_profiles_config_factory, llm_env):
        llm_env(DEEPSEEK_API_KEY="sk-super-secret-value")
        loaded = load_or_migrate(llm_profiles_config_factory("single"))
        snapshot = _gateway(profiles=loaded).profile_snapshot().to_dict()
        text = json.dumps(snapshot, ensure_ascii=False)
        assert "sk-super-secret-value" not in text
        assert "DEEPSEEK_API_KEY" in text  # 只记变量名

    def test_迁移说明随快照冻结(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("legacy_flat"))
        snapshot = loaded.snapshot()
        assert snapshot.source == "legacy"
        assert snapshot.migration_notes and "model_prices" in snapshot.migration_notes[0]
        assert snapshot.to_dict()["profiles"][0]["legacy_env"] is True

    def test_指纹随价目变化(self, llm_profiles_config_factory):
        before = load_or_migrate(llm_profiles_config_factory("single")).snapshot()
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"]["prices"]["completion_per_1k"] = 9.99
        after = load_or_migrate(config).snapshot()
        assert before.fingerprint != after.fingerprint
        assert before.ref.startswith("llm_profiles@") and before.ref != after.ref


class Test单档案等价现状:
    def test_既有_price_book_调用形态照旧(self):
        """SC-005：未声明档案时，折算口径与快照来源都保持现状（不改变既有测试语义）。"""
        gateway = _gateway()
        result = gateway.chat("同一个提示词", model="mock-copy-v1")
        price = {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}
        expected = (
            result.usage["prompt_tokens"] / 1000 * price["prompt_per_1k"]
            + result.usage["completion_tokens"] / 1000 * price["completion_per_1k"]
        )
        assert result.cost_usd == pytest.approx(expected)
        snapshot = gateway.profile_snapshot().to_dict()
        assert snapshot["source"] == "legacy_price_book"
        assert snapshot["profiles"][0]["profile_id"] == "mock-copy-v1"
        assert snapshot["profiles"][0]["legacy_env"] is True  # 如实标注"沿用旧变量名"
        assert snapshot["default_profile"] == "mock-copy-v1"  # 单档案自动认定

    def test_快照方法存在于既有网关实例(self):
        assert callable(_gateway().profile_snapshot)
