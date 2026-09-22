"""旧扁平配置迁移单测（功能 016 / T1605，先于实现编写）：契约 C3。

覆盖：仅旧写法 → 单档案 + 映射说明（档案 id = 模型名）；**新旧并存 → 以新写法为准** +
notes"旧键被忽略"；旧写法缺价目 → 报错（与 C1 同纪律）；两侧都没有 → 报错（不静默给空档案）；
旧档案如实标注"沿用旧变量名"（legacy_env=true，端点/凭证名登记为旧变量名）。
"""

import pytest

from core.llm_gateway.profiles import (
    LEGACY_API_KEY_ENV,
    LEGACY_BASE_URL_ENV,
    ProfileConfigError,
    load_or_migrate,
    migrate_legacy,
)


class Test仅旧写法:
    def test_单档案与映射说明(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("legacy_flat")
        result = migrate_legacy(config)
        assert result is not None
        profiles, routing, migration_notes = result
        assert list(profiles) == ["mock-copy-v1"]  # 档案 id = 模型名
        profile = profiles["mock-copy-v1"]
        assert profile.prices == {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}
        assert profile.legacy_env is True
        assert profile.api_key_env == LEGACY_API_KEY_ENV
        assert profile.base_url_env == LEGACY_BASE_URL_ENV
        assert profile.endpoint_ref == LEGACY_BASE_URL_ENV  # 快照记变量名（不记值）
        assert any("沿用旧变量名" in note for note in migration_notes)
        assert routing.default_reason == "legacy_migration"
        assert routing.default_profile == "mock-copy-v1"

    def test_无旧写法返回_None(self):
        assert migrate_legacy({"form": "movie", "visual": {"clips_per_round": 3}}) is None

    def test_load_or_migrate_走旧写法并标注来源(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("legacy_flat"))
        assert loaded.source == "legacy"
        assert sorted(loaded.profiles) == ["mock-copy-v1"]
        assert loaded.migration_notes and "model_prices" in loaded.migration_notes[0]
        assert loaded.snapshot().source == "legacy"

    def test_旧写法缺价目即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="缺少模型"):
            migrate_legacy(llm_profiles_config_factory("legacy_missing_prices"))


class Test新旧并存:
    def test_以新写法为准且旧键被忽略(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("legacy_and_new")
        loaded = load_or_migrate(config)
        assert loaded.source == "llm_section"
        assert sorted(loaded.profiles) == ["deepseek-flash"]  # 旧键（mock-copy-v1）未被采用
        assert any("旧扁平键被忽略" in note for note in loaded.notes)
        # 新档案如实生效：旧价目（0.001/0.002）不生效
        assert loaded.profiles["deepseek-flash"].prices["completion_per_1k"] == 0.0012


class Test两侧都缺:
    def test_既无_llm_段也无旧写法即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="既无 llm 段"):
            load_or_migrate(llm_profiles_config_factory("no_llm_section"))
