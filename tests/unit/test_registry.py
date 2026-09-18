"""评估器注册中心单测（US2 / T022）。

契约表（contracts/evaluator-registry.md §2）全行覆盖：
键全局唯一、重复注册拒绝、非确定性拒绝、human 例外、缺字段拒绝、
get 未命中消息含可用版本列表。
"""

import pytest

from core.evaluators import registry
from core.evaluators.errors import RegistrationError
from core.evaluators.registry import Registry
from tests.stubs import (
    StubHumanEvaluator,
    StubJudgeEvaluator,
    StubNonDeterministicEvaluator,
    StubProxyEvaluator,
    StubRuleEvaluator,
)


class TestRegister:
    def test_注册并可取回(self):
        reg = Registry()
        ev = StubRuleEvaluator()
        reg.register(ev)
        assert reg.get("rule.stub", "1.0.0") is ev

    def test_重复注册同键报_RegistrationError_并附冲突键(self):
        reg = Registry()
        reg.register(StubProxyEvaluator())
        with pytest.raises(RegistrationError, match=r"proxy\.stub@1\.0\.0"):
            reg.register(StubProxyEvaluator())

    def test_同_id_不同版本可共存(self):
        reg = Registry()
        reg.register(StubProxyEvaluator(version="1.0.0"))
        reg.register(StubProxyEvaluator(version="2.0.0"))
        assert reg.get("proxy.stub", "1.0.0") is not reg.get("proxy.stub", "2.0.0")

    def test_非确定性非人类评估器拒注册(self):
        reg = Registry()
        with pytest.raises(RegistrationError, match="确定"):
            reg.register(StubNonDeterministicEvaluator())

    def test_人类锚点非确定性例外放行(self):
        reg = Registry()
        ev = StubHumanEvaluator()
        reg.register(ev)
        assert reg.get("human.stub", "1.0.0") is ev

    @pytest.mark.parametrize("field", ["evaluator_id", "version"])
    def test_缺失_spec_必填字段报_RegistrationError(self, field):
        reg = Registry()
        ev = StubJudgeEvaluator()
        # 绕过 frozen 构造校验，模拟不规范实现传入残缺 spec（注册中心为第二道防线）
        object.__setattr__(ev.spec, field, "")
        with pytest.raises(RegistrationError):
            reg.register(ev)

    def test_缺失_kind_报_RegistrationError(self):
        reg = Registry()
        ev = StubJudgeEvaluator()
        object.__setattr__(ev.spec, "kind", None)
        with pytest.raises(RegistrationError):
            reg.register(ev)


class TestGet:
    def test_get_未命中消息含可用版本列表(self):
        reg = Registry()
        reg.register(StubProxyEvaluator(version="1.0.0"))
        reg.register(StubProxyEvaluator(version="2.0.0"))
        with pytest.raises(RegistrationError) as exc_info:
            reg.get("proxy.stub", "3.0.0")
        message = str(exc_info.value)
        assert "proxy.stub" in message
        assert "1.0.0" in message and "2.0.0" in message

    def test_get_未知_id_报_RegistrationError(self):
        reg = Registry()
        with pytest.raises(RegistrationError):
            reg.get("ghost", "1.0.0")


class TestListAll:
    def test_list_all_返回全部_spec(self):
        reg = Registry()
        reg.register(StubRuleEvaluator())
        reg.register(StubProxyEvaluator())
        keys = {spec.key for spec in reg.list_all()}
        assert keys == {"rule.stub@1.0.0", "proxy.stub@1.0.0"}

    def test_空注册中心返回空列表(self):
        assert Registry().list_all() == []


class Test模块级默认注册中心:
    def test_模块函数委托默认实例(self):
        """契约形态：register/get/list_all 模块级函数可用（测试后清理避免串扰）。"""
        ev = StubRuleEvaluator(evaluator_id="rule.module_level")
        registry.register(ev)
        try:
            assert registry.get("rule.module_level", "1.0.0") is ev
            assert any(s.evaluator_id == "rule.module_level" for s in registry.list_all())
        finally:
            registry.reset()
