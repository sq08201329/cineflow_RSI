"""网关按角色路由单测（功能 016 / T1613，先于实现编写）：契约 C4 + C5 的路由面。

覆盖：`judge` 命中映射（端点与价目都取该档案）；未映射的**枚举内**角色回落显式默认档案
（`reason=default_fallback`，且**不静默回落到其它档案**）；**枚举外角色在构造期报错**
（不落到运行期）；`RouteDecision.profile_snapshot_ref` 可追溯（指向当次快照指纹）；
接档案后**必须声明角色**（否则配置错误，不猜测）；档案端点/价目由路由层注入后端（C5）。
"""

import json

import pytest

from core.llm_gateway.gateway import BackendResult, LLMGateway
from core.llm_gateway.profiles import ProfileConfigError, ProfileLoad, load_or_migrate
from core.llm_gateway.routing import Role, RoleRouting


class _RecordingBackend:
    """记录型假后端：不联网，回固定 token 数并记下收到的 model（验证端点/模型由档案注入）。"""

    def __init__(self, *, prompt_tokens=100, completion_tokens=50) -> None:
        self.call_count = 0
        self.models: list[str] = []
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        self.models.append(model)
        return BackendResult(
            text=f"[{model}] 伪文本",
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )


def _gateway(config_factory, variant="multi", *, backend=None) -> tuple[LLMGateway, object]:
    loaded = load_or_migrate(config_factory(variant))
    backend = backend or _RecordingBackend()
    gateway = LLMGateway(
        backend,
        price_book=loaded.snapshot().price_book(),
        sleep=lambda _: None,
        profiles=loaded,
    )
    return gateway, backend


class Test按角色取档案:
    def test_judge_命中映射_模型与价目都取该档案(self, llm_profiles_config_factory):
        gateway, backend = _gateway(llm_profiles_config_factory)
        result = gateway.chat("评审这份大纲", role=Role.JUDGE)

        assert backend.models == ["deepseek-flash"]  # 模型名由档案决定（业务代码零厂商字面量）
        assert result.profile_id == "deepseek-flash"
        assert result.role == "judge"
        # 价目取该档案：1000×0.0003 + 500×0.0012（用固定 token 数直接验算）
        gateway_2, _ = _gateway(
            llm_profiles_config_factory,
            backend=_RecordingBackend(prompt_tokens=1000, completion_tokens=500),
        )
        assert gateway_2.chat("评审", role=Role.JUDGE).cost_usd == pytest.approx(0.0003 + 0.0006)
        assert result.cost_usd > 0

    def test_未映射枚举内角色回落默认档案(self, llm_profiles_config_factory):
        """multi 变体只映射 generation/judge/dreaming_candidates → copywriting 走默认回落。"""
        gateway, backend = _gateway(llm_profiles_config_factory)
        decision = gateway.route(Role.COPYWRITING)
        assert (decision.profile_id, decision.reason) == ("deepseek-flash", "default_fallback")
        gateway.chat("写一条文案", role=Role.COPYWRITING)
        assert backend.models == ["deepseek-flash"]

    def test_梦候选角色命中本地档案(self, llm_profiles_config_factory):
        gateway, backend = _gateway(llm_profiles_config_factory)
        decision = gateway.route(Role.DREAMING_CANDIDATES)
        assert decision.profile_id == "local-qwen"
        result = gateway.chat("候选策略", role=Role.DREAMING_CANDIDATES)
        assert backend.models == ["local-qwen"]
        assert result.cost_usd == 0.0  # 零边际成本档案

    def test_决策可追溯到快照指纹(self, llm_profiles_config_factory):
        gateway, _ = _gateway(llm_profiles_config_factory)
        decision = gateway.route(Role.JUDGE)
        snapshot = gateway.profile_snapshot()
        assert decision.profile_snapshot_ref == snapshot.ref
        assert decision.profile_snapshot_ref.startswith("llm_profiles@")
        assert decision.to_dict()["reason"] == "role_mapping"

    def test_不静默回落到其它档案(self, llm_profiles_config_factory):
        """未映射角色只能落到**显式默认**档案；档案集合里其它档案不得被"猜"到。"""
        gateway, backend = _gateway(llm_profiles_config_factory)
        gateway.chat("写一条文案", role=Role.COPYWRITING)
        assert backend.models == ["deepseek-flash"]  # 不是 local-qwen


class Test错误拦截:
    def test_枚举外角色在构造期报错(self, llm_profiles_config_factory):
        gateway, backend = _gateway(llm_profiles_config_factory)
        with pytest.raises(ProfileConfigError, match="必须是 Role 枚举成员"):
            gateway.chat("跑一下", role="judeg")  # 角色不在枚举 → 立刻拒绝
        assert backend.call_count == 0  # 未落到运行期、未产生任何调用

    def test_接档案后必须声明角色(self, llm_profiles_config_factory):
        gateway, backend = _gateway(llm_profiles_config_factory)
        with pytest.raises(ProfileConfigError, match="必须声明角色"):
            gateway.chat("没有角色的调用")
        assert backend.call_count == 0

    def test_路由指向不存在档案即拒(self, llm_profiles_config_factory):
        """解析期已拦（`resolve_routing`）；此处守住第二道闸：运行时取档案仍必须存在。"""
        loaded = load_or_migrate(llm_profiles_config_factory("single"))
        broken = ProfileLoad(
            profiles=dict(loaded.profiles),
            routing=RoleRouting(roles={Role.JUDGE: "ghost"}, default_profile="ghost"),
            source="llm_section",
        )
        gateway = LLMGateway(
            _RecordingBackend(),
            price_book=broken.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=broken,
        )
        with pytest.raises(ProfileConfigError, match="档案不存在"):
            gateway.chat("x", role=Role.JUDGE)


class Test旧调用形态不受影响:
    def test_未接档案时按_price_book_折算(self):
        backend = _RecordingBackend(prompt_tokens=1000, completion_tokens=500)
        gateway = LLMGateway(
            backend,
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        result = gateway.chat("旧路径", model="mock-copy-v1")
        assert backend.models == ["mock-copy-v1"]
        assert result.cost_usd == pytest.approx(0.001 + 0.001)
        assert result.profile_id == "" and result.role == ""  # 未接档案：不伪造角色/档案

    def test_缺价目仍报错(self):
        gateway = LLMGateway(_RecordingBackend(), price_book={}, sleep=lambda _: None)
        from core.llm_gateway.gateway import PricingNotFoundError

        with pytest.raises(PricingNotFoundError):
            gateway.chat("旧路径", model="ghost-model")


class Test快照与报告口径:
    def test_零边际成本档案的备注进报告(self, llm_profiles_config_factory):
        gateway, _ = _gateway(llm_profiles_config_factory)
        gateway.chat("候选策略", role=Role.DREAMING_CANDIDATES)
        report = gateway.cost_report()
        local = next(e for e in report["entries"] if e["profile_id"] == "local-qwen")
        assert local["cost_usd"] == 0.0 and local["zero_marginal"] is True
        assert "零边际成本" in local["price_note"]

    def test_report_含记账非账单声明(self, llm_profiles_config_factory):
        gateway, _ = _gateway(llm_profiles_config_factory)
        gateway.chat("评审", role=Role.JUDGE)
        report = gateway.cost_report()
        assert "记账 ≠ 厂商账单" in report["accounting_note"]
        assert json.dumps(report, ensure_ascii=False)  # 报告可序列化（供 CLI/账目呈现）
