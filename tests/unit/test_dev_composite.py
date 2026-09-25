"""开发 Agent 合成评分与装配单测（功能 017 / US2 / T1728，先于实现编写）。

契约 C10：合成 = **gate 短路**（`rule.*` 判 0 → 总分 0、两代理不跑也不计费，省调用）
+ **适用权重归一**（缺席分量跳过、分母不含其权重——不伪造 0 分拖底）+ quantize 定点 6 位；
无适用连续分量 → 0.0（确定性退化口径）。阈值与权重取自 `evaluator_weights.dev`，
装配的评估器集合与权重键必须一一对应（缺项/多项即拒绝装配，防配置漂移）；
同 `evaluator_id@version` 重复注册被拒（原则一）。
"""

import pytest

from agents.dev.config import DevConfig
from agents.dev.evaluators import build_dev_evaluators
from agents.dev.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_dev,
    evaluate_dev,
)
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.errors import PluginAssemblyError, RegistrationError
from core.evaluators.quantize import quantize_score
from core.evaluators.registry import Registry
from tests.stubs import StubProxyEvaluator

_GATE_IDS = ("rule.slate_structure", "rule.slate_combination")
_PROXY_IDS = ("proxy.genre_regression", "proxy.buzz_heat")


def _ref() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


def _assembly(dev_config):
    """真实两门禁 + 计次代理桩：真实门禁给判定，桩给"跑没跑"的可观测计数。"""
    assembly = build_dev_evaluators(dev_config)
    proxies = [StubProxyEvaluator(evaluator_id, score=0.6) for evaluator_id in _PROXY_IDS]
    return {"gates": assembly["gates"], "proxies": proxies, "all": [*assembly["gates"], *proxies]}


class Test合成函数:
    def test_gate判零总分零(self, dev_config):
        """C10：任一 `rule.*` 判 0 → 总分 0（不可行解，无视代理得分）。"""
        breakdown = {
            "rule.slate_combination@1.0.0+x": {"score": 0.0, "diagnostics": {}},
            "proxy.genre_regression@1.0.0+x": {"score": 0.9, "diagnostics": {}},
            "proxy.buzz_heat@1.0.0+x": {"score": 0.9, "diagnostics": {}},
        }
        assert composite_dev(breakdown, dev_config.evaluator_weights) == 0.0

    def test_适用权重归一(self, dev_config):
        """(0.6×0.8 + 0.4×0.6) / 1.0 = 0.72；gate 分量不计入加权和。"""
        breakdown = {
            "rule.slate_structure@1.0.0+x": {"score": 1.0, "diagnostics": {}},
            "rule.slate_combination@1.0.0+x": {"score": 1.0, "diagnostics": {}},
            "proxy.genre_regression@1.0.0+x": {"score": 0.8, "diagnostics": {}},
            "proxy.buzz_heat@1.0.0+x": {"score": 0.6, "diagnostics": {}},
        }
        assert composite_dev(breakdown, dev_config.evaluator_weights) == pytest.approx(0.72)

    def test_缺席分量跳过且分母不含其权重(self, dev_config):
        """只跑了 genre_regression：分母不含 buzz_heat 的权重 → 0.8（不按 0.6×0.8 打折）。"""
        breakdown = {
            "rule.slate_structure@1.0.0+x": {"score": 1.0, "diagnostics": {}},
            "proxy.genre_regression@1.0.0+x": {"score": 0.8, "diagnostics": {}},
        }
        assert composite_dev(breakdown, dev_config.evaluator_weights) == pytest.approx(0.8)

    def test_不适用分量跳过(self, dev_config):
        """诊断标 `applicable=False` 的分量跳过（键仍在 breakdown 落盘供审计）。"""
        breakdown = {
            "proxy.genre_regression@1.0.0+x": {"score": 0.8, "diagnostics": {"applicable": True}},
            "proxy.buzz_heat@1.0.0+x": {"score": 0.1, "diagnostics": {"applicable": False}},
        }
        assert composite_dev(breakdown, dev_config.evaluator_weights) == pytest.approx(0.8)

    def test_无适用连续分量得零(self, dev_config):
        """确定性退化口径：只有门禁分量（判分 1.0）时总分为 0.0，不给"门禁即满分"的假分。"""
        breakdown = {
            "rule.slate_structure@1.0.0+x": {"score": 1.0, "diagnostics": {}},
            "rule.slate_combination@1.0.0+x": {"score": 1.0, "diagnostics": {}},
        }
        assert composite_dev(breakdown, dev_config.evaluator_weights) == 0.0

    def test_定点六位(self, dev_config):
        breakdown = {
            "proxy.genre_regression@1.0.0+x": {"score": 1 / 3, "diagnostics": {}},
            "proxy.buzz_heat@1.0.0+x": {"score": 2 / 3, "diagnostics": {}},
        }
        total = quantize_score(composite_dev(breakdown, dev_config.evaluator_weights))
        assert total == round(total, 6) == 0.466667


class Test编排短路:
    def test_gate判零不跑代理也不计费(self, dev_config, make_topic_slate):
        """gate 短路：违规组合只跑两门禁——代理 zero 调用（省调用，不伪造其分量）。"""
        assembly = _assembly(dev_config)
        breakdown, score, usage = evaluate_dev(
            assembly,
            _ref(),
            {"artifact": make_topic_slate("duplicate_direction")},
            dev_config.evaluator_weights,
        )
        assert score == 0.0
        assert len(breakdown) == len(_GATE_IDS)
        assert all(proxy.calls == 0 for proxy in assembly["proxies"])
        assert usage == {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
        violations = [fragment["diagnostics"]["violations"] for fragment in breakdown.values()]
        assert all(item for item in violations)  # 诊断点名具体违规项

    def test_门禁判零时不写入代理分量(self, dev_config, make_topic_slate):
        assembly = _assembly(dev_config)
        breakdown, _, _ = evaluate_dev(
            assembly,
            _ref(),
            {"artifact": make_topic_slate("marks_out_of_range")},
            dev_config.evaluator_weights,
        )
        assert not any(key.startswith("proxy.") for key in breakdown)

    def test_合规组合跑满四分量(self, dev_config, topic_slate):
        assembly = _assembly(dev_config)
        breakdown, score, usage = evaluate_dev(
            assembly, _ref(), {"artifact": topic_slate}, dev_config.evaluator_weights
        )
        assert {key.rsplit("@", 1)[0] for key in breakdown} == set(dev_config.evaluator_weights)
        assert all(proxy.calls == 1 for proxy in assembly["proxies"])
        assert score == pytest.approx(0.6)  # 两桩代理 0.6 → 适用权重归一点定
        assert usage == {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}  # 确定性代理零成本

    def test_合成口径常量(self):
        assert "gate" in COMPOSITE_POLICY and "归一" in COMPOSITE_POLICY


class Test装配:
    def test_四评估器与权重键一一对应(self, dev_config):
        assembly = build_dev_evaluators(dev_config)
        assert [evaluator.spec.evaluator_id for evaluator in assembly["gates"]] == list(_GATE_IDS)
        assert [evaluator.spec.evaluator_id for evaluator in assembly["proxies"]] == list(
            _PROXY_IDS
        )
        assert {evaluator.spec.evaluator_id for evaluator in assembly["all"]} == set(
            dev_config.evaluator_weights
        )
        for evaluator in assembly["all"]:
            assert evaluator.spec.deterministic is True
            assert evaluator.spec.cost_per_call == 0.0
            assert evaluator.spec.version.startswith("1.0.0+")
            assert len(evaluator.spec.version.rsplit("+", 1)[1]) == 12
        assert all(e.spec.kind is EvaluatorKind.RULE for e in assembly["gates"])
        assert all(e.spec.kind is EvaluatorKind.PROXY_MODEL for e in assembly["proxies"])

    def test_装配随形态配置变化(self, dev_config_fragment, dev_config):
        """形态参数进装配：条目数区间/标记区间/重复率上限不同 ⇒ 门禁版本或判定面随之变化。"""
        movie = build_dev_evaluators(dev_config)
        short = build_dev_evaluators(
            DevConfig.from_dict(dev_config_fragment(slate={"min": 5, "max": 10}))
        )
        assert [e.spec.version for e in short["gates"]] != [e.spec.version for e in movie["gates"]]
        assert short["proxies"][0].spec.version == movie["proxies"][0].spec.version

    def test_权重键漂移即拒绝装配(self, dev_config_fragment):
        """021（C2）：一一对应校验由唯一装配点承担 ⇒ 报错类型为 `PluginAssemblyError`
        （文案口径沿用既有"缺权重键/多余权重键"），断言面不削弱。"""
        payload = dev_config_fragment()
        payload["evaluator_weights"]["dev"]["proxy.ghost"] = 0.1
        with pytest.raises(PluginAssemblyError, match="evaluator_weights.dev"):
            build_dev_evaluators(DevConfig.from_dict(payload))

    def test_同键重复注册被拒(self, dev_config):
        registry = Registry()
        build_dev_evaluators(dev_config, registry=registry)
        assert len(registry.list_all()) == 4
        with pytest.raises(RegistrationError):
            build_dev_evaluators(dev_config, registry=registry)
