"""档案解析与校验单测（功能 016 / T1603，先于实现编写）：契约 C1。

先写测试确认失败，再实现 `core/llm_gateway/profiles.py`。覆盖：

- 单档案合法（notes 空 + 快照无密钥）；多档案各自独立（价目/端点/凭证名互不串用）；
- **三类缺项各报错**：缺端点 / 缺凭证变量名 / 缺价目（不静默零成本、不回落默认价）；
- 价目全 0 必须显式 `zero_marginal: true`（否则报错；声明后通过且快照标注零边际成本）；
- `price_note` 缺省可通过但 notes 含告警；端点为 URL 时快照**只记 host**（env 形态记变量名）。
"""

import pytest

from core.llm_gateway.profiles import (
    ModelProfile,
    ProfileConfigError,
    host_of_url,
    load_profiles,
)
from core.llm_gateway.routing import Role

SECRET = "sk-绝密档案密钥不应出现在快照里"


class Test合法档案:
    def test_单档案_解析成功且无告警(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        profiles, routing, notes = load_profiles(config)
        assert sorted(profiles) == ["deepseek-flash"]
        assert notes == []
        assert routing.default_profile == "deepseek-flash"
        assert routing.default_reason == "single_profile"  # 单档案自动认定
        assert "自动认定" in "".join(routing.notes)

    def test_多档案_价目端点凭证名互不串用(self, llm_profiles_config_factory):
        profiles, routing, _ = load_profiles(llm_profiles_config_factory("multi"))
        assert sorted(profiles) == ["deepseek-flash", "local-qwen"]
        cloud = profiles["deepseek-flash"]
        local = profiles["local-qwen"]
        assert isinstance(cloud, ModelProfile)
        assert cloud.prices["completion_per_1k"] == 0.0012
        assert cloud.api_key_env == "DEEPSEEK_API_KEY"
        assert cloud.endpoint_ref == "https://api.deepseek.com"  # URL → 只记 host
        assert local.prices == {"prompt_per_1k": 0.0, "completion_per_1k": 0.0}
        assert local.api_key_env == "LOCAL_LLM_API_KEY"
        assert local.endpoint_ref == "LOCAL_LLM_BASE_URL"  # env 形态 → 记变量名
        assert local.zero_marginal is True and cloud.zero_marginal is False
        assert routing.profile_for(Role.JUDGE) == "deepseek-flash"  # 路由细节见 C2 单测
        assert routing.default_profile == "deepseek-flash"

    def test_内网_http_端点只记_host_含端口(self):
        assert host_of_url("http://127.0.0.1:8080/v1/chat?k=1") == "http://127.0.0.1:8080"
        with pytest.raises(ProfileConfigError, match="端点 URL 形态非法"):
            host_of_url("api.deepseek.com")

    def test_档案按价目折算成本(self, llm_profiles_config_factory):
        profiles, _, _ = load_profiles(llm_profiles_config_factory("single"))
        profile = profiles["deepseek-flash"]
        assert profile.cost_usd(prompt_tokens=1000, completion_tokens=500) == pytest.approx(
            0.0003 + 0.0006  # prompt 1000/1k×0.0003 + completion 500/1k×0.0012
        )


class Test缺项即报错:
    def test_缺价目(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="缺价目"):
            load_profiles(llm_profiles_config_factory("missing_prices"))

    def test_缺端点(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="缺端点"):
            load_profiles(llm_profiles_config_factory("missing_endpoint"))

    def test_缺凭证变量名(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="缺 api_key_env"):
            load_profiles(llm_profiles_config_factory("missing_key_env"))

    def test_端点双声明即拒(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"]["base_url_env"] = "ANOTHER_URL_ENV"
        with pytest.raises(ProfileConfigError, match="端点来源必须唯一"):
            load_profiles(config)

    def test_缺_llm_段即拒(self):
        with pytest.raises(ProfileConfigError, match="缺少 llm 段"):
            load_profiles({"form": "movie"})

    def test_价目字段类型非法即拒(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"]["prices"]["prompt_per_1k"] = "免费"
        with pytest.raises(ProfileConfigError, match="非合法数值"):
            load_profiles(config)


class Test零价目:
    def test_全零未声明_zero_marginal_即拒(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="zero_marginal"):
            load_profiles(llm_profiles_config_factory("zero_not_declared"))

    def test_声明_zero_marginal_后通过且快照标注(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("zero_not_declared")
        config["llm"]["profiles"]["deepseek-flash"]["zero_marginal"] = True
        profiles, _, notes = load_profiles(config)
        assert profiles["deepseek-flash"].zero_marginal is True
        assert any("零边际成本" in note for note in notes)
        cost = profiles["deepseek-flash"].cost_usd(prompt_tokens=10_000, completion_tokens=5_000)
        assert cost == 0.0

    def test_非零价目却声明_zero_marginal_矛盾即拒(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"]["zero_marginal"] = True
        with pytest.raises(ProfileConfigError, match="矛盾"):
            load_profiles(config)


class Test口径备注与密钥隔离:
    def test_缺_price_note_通过但记告警(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"].pop("price_note")
        profiles, _, notes = load_profiles(config)
        assert profiles["deepseek-flash"].price_note == ""
        assert any("price_note" in note for note in notes)

    def test_快照不含密钥且只记端点引用(self, llm_profiles_config_factory):
        profiles, _, _ = load_profiles(llm_profiles_config_factory("multi"))
        for profile in profiles.values():
            snapshot = profile.to_snapshot()
            assert snapshot["api_key_env"].isupper()  # 只记变量名，不记值
            assert SECRET not in str(snapshot)
            assert set(snapshot["prices"]) == {"prompt_per_1k", "completion_per_1k"}
