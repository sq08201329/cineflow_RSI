"""LLM 档案契约聚合（功能 016 / T1611）：契约 C1~C3（US1 的 profiles 段）。

同一套契约对"配置片段工厂"的**全部合法/非法变体**执行一遍，把 C1（解析与校验）、
C2（默认档案与角色映射）、C3（旧扁平迁移）逐场景固化——缺项与枚举外**必须 100% 报错**，
且错误文案要能被运维直接照做（列出合法枚举 / 指出缺哪个键）。
"""

import pytest

from core.llm_gateway.profiles import (
    ProfileConfigError,
    load_or_migrate,
    load_profiles,
    migrate_legacy,
)
from core.llm_gateway.routing import role_values


class TestC1档案解析与校验:
    def test_场景1_单档案合法且无档案级告警(self, llm_profiles_config_factory):
        profiles, _, notes = load_profiles(llm_profiles_config_factory("single"))
        assert list(profiles) == ["deepseek-flash"]
        assert notes == []

    def test_场景2_多档案互不串用(self, llm_profiles_config_factory):
        profiles, _, _ = load_profiles(llm_profiles_config_factory("multi"))
        assert profiles["deepseek-flash"].prices["prompt_per_1k"] == 0.0003
        assert profiles["local-qwen"].prices["prompt_per_1k"] == 0.0
        assert profiles["deepseek-flash"].endpoint_ref != profiles["local-qwen"].endpoint_ref

    @pytest.mark.parametrize(
        ("variant", "keyword"),
        [
            ("missing_prices", "缺价目"),
            ("missing_endpoint", "缺端点"),
            ("missing_key_env", "缺 api_key_env"),
        ],
    )
    def test_场景3_三类缺项各报错(self, llm_profiles_config_factory, variant, keyword):
        with pytest.raises(ProfileConfigError, match=keyword):
            load_profiles(llm_profiles_config_factory(variant))

    def test_场景4_零价目必须显式声明(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="zero_marginal"):
            load_profiles(llm_profiles_config_factory("zero_not_declared"))

    def test_场景5_price_note_缺省入告警(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"].pop("price_note")
        profiles, _, notes = load_profiles(config)
        assert profiles["deepseek-flash"].price_note == ""
        assert any("price_note" in note for note in notes)


class TestC2默认档案与角色映射:
    def test_场景1_单档案自动认定并标注(self):
        from core.llm_gateway.routing import resolve_routing

        routing = resolve_routing({"only": object()}, None, None)
        assert (routing.default_profile, routing.default_reason) == ("only", "single_profile")
        assert any("自动认定" in note for note in routing.notes)

    def test_场景2_双档案缺默认即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="default_profile 缺失"):
            load_profiles(llm_profiles_config_factory("multi_no_default"))

    def test_场景3_枚举外角色报错并列出合法枚举(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError) as excinfo:
            load_profiles(llm_profiles_config_factory("unknown_role"))
        for legal in role_values():
            assert legal in str(excinfo.value)

    @pytest.mark.parametrize(
        ("variant", "keyword"),
        [
            ("unknown_profile", "指向不存在的档案"),
            ("multi_level", "嵌套映射"),
            ("self_reference", "自指"),
        ],
    )
    def test_场景4_映射非法各报错(self, llm_profiles_config_factory, variant, keyword):
        with pytest.raises(ProfileConfigError, match=keyword):
            load_profiles(llm_profiles_config_factory(variant))

    def test_场景5_未引用档案入_notes(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("orphan"))
        assert any("未被任何角色引用" in note for note in loaded.notes)


class TestC3旧扁平迁移:
    def test_场景1_旧写法映射为单档案并给说明(self, llm_profiles_config_factory):
        result = migrate_legacy(llm_profiles_config_factory("legacy_flat"))
        assert result is not None
        profiles, routing, migration_notes = result
        assert list(profiles) == ["mock-copy-v1"]
        assert profiles["mock-copy-v1"].legacy_env is True
        assert migration_notes and "沿用旧变量名" in migration_notes[0]
        assert routing.default_profile == "mock-copy-v1"

    def test_场景2_新旧并存以新为准_旧键被忽略(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("legacy_and_new"))
        assert loaded.source == "llm_section"
        assert list(loaded.profiles) == ["deepseek-flash"]
        assert any("旧扁平键被忽略" in note for note in loaded.notes)

    def test_场景3_旧写法缺价目即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="缺少模型"):
            migrate_legacy(llm_profiles_config_factory("legacy_missing_prices"))

    def test_仓库真实配置走新写法且档案齐备(self):
        """形态配置（movie/shortdrama）必须自带 llm 段：档案、角色、默认档案三件齐备。"""
        import pathlib

        import yaml

        repo_root = pathlib.Path(__file__).resolve().parents[2]
        for name in ("movie", "shortdrama"):
            payload = yaml.safe_load(
                (repo_root / "configs" / f"{name}.yaml").read_text(encoding="utf-8")
            )
            loaded = load_or_migrate(payload)
            assert loaded.source == "llm_section"
            assert sorted(loaded.profiles) == ["deepseek-flash", "local-qwen"]
            assert loaded.routing.default_profile == "deepseek-flash"
            assert loaded.routing.profile_for("dreaming_candidates") == "local-qwen"
            snapshot = loaded.snapshot().to_dict()
            assert snapshot["profiles"][0]["prices"]  # 价目随快照冻结
            assert "OPENAI_API_KEY" == loaded.profiles["deepseek-flash"].api_key_env
