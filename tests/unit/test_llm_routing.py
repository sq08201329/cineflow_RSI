"""角色路由解析与校验单测（功能 016 / T1607，先于实现编写）：契约 C2 + C4 的路由面。

覆盖：单档案自动认定默认 + notes 标注；多档案缺默认报错；**枚举外角色报错并列出合法枚举**；
映射指向不存在档案 / 多层 / 自指报错；未引用档案入 notes；`route` 的命中与默认回落
（`reason` 标注），以及**不静默回落到其它档案**。
"""

import pytest

from core.llm_gateway.profiles import ProfileConfigError, load_or_migrate, load_profiles
from core.llm_gateway.routing import (
    Role,
    RoleRouting,
    RouteDecision,
    resolve_routing,
    role_values,
    route,
)


class Test角色枚举:
    def test_枚举按调用点盘点定稿(self):
        """盘点结论（2026-09-22）：共 7 个调用点归为 4 个角色。"""
        assert role_values() == (
            "generation",
            "judge",
            "dreaming_candidates",
            "copywriting",
        )

    def test_枚举成员可作字符串使用(self):
        assert Role.GENERATION == "generation"
        assert str(Role.DREAMING_CANDIDATES) == "dreaming_candidates"


class Test默认档案:
    def test_单档案自动认定(self):
        routing = resolve_routing({"only": object()}, None, None)
        assert routing.default_profile == "only"
        assert routing.default_reason == "single_profile"
        assert any("自动认定" in note for note in routing.notes)

    def test_多档案缺默认即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="default_profile 缺失"):
            load_profiles(llm_profiles_config_factory("multi_no_default"))

    def test_默认指向不存在档案即报错(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("multi")
        config["llm"]["default_profile"] = "ghost-default"
        with pytest.raises(ProfileConfigError, match="指向不存在的档案"):
            load_profiles(config)

    def test_空档案表即报错(self):
        with pytest.raises(ProfileConfigError, match="至少需要一条档案"):
            resolve_routing({}, None, None)


class Test角色映射校验:
    def test_枚举外角色报错并列出合法枚举(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError) as excinfo:
            load_profiles(llm_profiles_config_factory("unknown_role"))
        message = str(excinfo.value)
        assert "judeg" in message
        for legal in role_values():
            assert legal in message

    def test_映射指向不存在档案即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="指向不存在的档案"):
            load_profiles(llm_profiles_config_factory("unknown_profile"))

    def test_多层映射即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="嵌套映射"):
            load_profiles(llm_profiles_config_factory("multi_level"))

    def test_自指即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="自指"):
            load_profiles(llm_profiles_config_factory("self_reference"))

    def test_roles_非映射即报错(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["roles"] = ["generation"]
        with pytest.raises(ProfileConfigError, match="必须是单层映射"):
            load_profiles(config)

    def test_未引用档案入_notes(self, llm_profiles_config_factory):
        """C2：孤档案允许存在，但必须提示（合并视图 = ProfileLoad.notes，含路由侧 notes）。"""
        config = llm_profiles_config_factory("orphan")
        _, routing, _ = load_profiles(config)
        assert any("未被任何角色引用" in note for note in routing.notes)
        assert routing.default_profile == "deepseek-flash"
        assert any("未被任何角色引用" in note for note in load_or_migrate(config).notes)


class Test路由决策:
    def _routing(self) -> RoleRouting:
        return RoleRouting(
            roles={Role.JUDGE: "deepseek-pro"},
            default_profile="deepseek-flash",
            default_reason="declared",
        )

    def test_命中角色映射(self):
        decision = route(Role.JUDGE, self._routing(), profile_snapshot_ref="llm_profiles@abcdef")
        assert isinstance(decision, RouteDecision)
        assert decision.profile_id == "deepseek-pro"
        assert decision.reason == "role_mapping"
        assert decision.profile_snapshot_ref == "llm_profiles@abcdef"
        assert decision.to_dict()["role"] == "judge"

    def test_未映射枚举内角色回落默认并标注原因(self):
        decision = route(Role.COPYWRITING, self._routing())
        assert decision.profile_id == "deepseek-flash"
        assert decision.reason == "default_fallback"

    def test_不静默回落到其它档案(self):
        """未映射时只走显式默认档案，绝不"猜"另一条档案（配置里只有两条时即默认那条）。"""
        routing = self._routing()
        assert route(Role.GENERATION, routing).profile_id == routing.default_profile

    def test_非枚举角色即报错(self):
        with pytest.raises(ProfileConfigError, match="必须是 Role 枚举成员"):
            route("judge", self._routing())

    def test_无默认档案时拒绝回落(self):
        routing = RoleRouting(roles={}, default_profile="", default_reason="declared")
        with pytest.raises(ProfileConfigError, match="无默认档案"):
            route(Role.GENERATION, routing)
