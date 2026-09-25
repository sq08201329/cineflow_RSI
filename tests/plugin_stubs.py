"""插件装配面的**合成反例**目标（021 T2109；仅测试用，不进任何形态配置）。

`test_evaluator_plugin_assembly.py` 用这些目标构造"必须被拒"的声明：参数严格性双向反例
（少声明 / 多声明 / 可变参数）、三项一致性反例（`evaluator_id` / `kind` 前缀 / `version`）、
注入槽位越界。目标本身**不含**形态名，只暴露可被 `impl` 引用的可调用对象。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.evaluators.base import ArtifactRef, EvalResult, Evaluator, EvaluatorKind, EvaluatorSpec


class StubEvaluator(Evaluator):
    def __init__(
        self, evaluator_id: str, *, version: str = "1.0.0+stub00000000", kind=EvaluatorKind.RULE
    ):
        self.spec = EvaluatorSpec(
            evaluator_id=evaluator_id,
            version=version,
            kind=kind,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        return EvalResult(score=1.0)


@dataclass
class StubAgentConfig:
    """最小 `agent_config`：声明面反例不依赖任何真实 Agent 配置。"""

    evaluator_weights: dict


# --- 参数严格性（C2）：required(impl) 必须恰为 params ∪ 注入槽位 ---
def declared_only(*, agent_config) -> Evaluator:
    """合规：无默认值参数恰为注入槽位。"""
    return StubEvaluator("rule.declared_only")


def missing_declaration(*, agent_config, threshold) -> Evaluator:
    """少声明：`threshold` 无默认值却既不在 `params` 也不属注入槽位。"""
    return StubEvaluator("rule.missing_declaration")


def defaulted_parameter(*, agent_config, threshold=1) -> Evaluator:
    """多声明：`threshold` 有默认值 ⇒ 不在 required，注入/声明面却要覆盖它。"""
    return StubEvaluator("rule.defaulted_parameter")


def variadic(**kwargs) -> Evaluator:
    """可变参数吸收未知声明 ⇒ 报错。"""
    return StubEvaluator("rule.variadic")


def out_of_bounds_slot(*, agent_config, config_path) -> Evaluator:
    """注入槽位越界（`config_path` ∉ INJECTION_SLOTS）⇒ 报错。"""
    return StubEvaluator("rule.out_of_bounds_slot")


def takes_params(*, agent_config, threshold, label) -> Evaluator:
    """`params` 非空且键名与签名一致 ⇒ 合规（用于 params 通道的正例）。"""
    del threshold, label
    return StubEvaluator("rule.takes_params")


# --- 三项一致性（C2/C4） ---
def foreign_id(*, agent_config) -> Evaluator:
    """产出实例的 `evaluator_id` 与声明键不一致。"""
    return StubEvaluator("rule.foreign_id")


def wrong_kind(*, agent_config) -> Evaluator:
    """产出实例的 kind 与声明键前缀不一致（`rule.` 却是 proxy）。"""
    return StubEvaluator("rule.wrong_kind", kind=EvaluatorKind.PROXY_MODEL)


def wrong_version(*, agent_config) -> Evaluator:
    """产出实例的 version 与声明值不一致。"""
    return StubEvaluator("rule.wrong_version", version="1.0.0+other1234567")


def declared_only_proxy(*, agent_config) -> Evaluator:
    """`proxy.` 前缀的合规目标（保序用例的第二槽位）。"""
    return StubEvaluator("proxy.declared_only", kind=EvaluatorKind.PROXY_MODEL)


def mismatched_id(*, agent_config) -> Evaluator:
    """产出实例的 `evaluator_id` 与声明键不一致（声明键另取名字）。"""
    return StubEvaluator("rule.actually_other")


def declares_gateway(*, agent_config, gateway) -> Evaluator:
    """声明了 `gateway` 注入槽位（用于"槽位取值为 None 即报错"）。"""
    del gateway
    return StubEvaluator("rule.declares_gateway")


def nondeterministic(*, agent_config) -> Evaluator:
    """非确定性（`deterministic=False`）⇒ 注册被拒（human 类例外）。"""
    evaluator = StubEvaluator("rule.nondeterministic")
    evaluator.spec = EvaluatorSpec(
        evaluator_id="rule.nondeterministic",
        version="1.0.0+stub00000000",
        kind=EvaluatorKind.RULE,
        deterministic=False,
    )
    return evaluator


NOT_CALLABLE = 3


class NotAnEvaluator:
    """可调用但产出非评估器实例（缺 `spec`）。"""

    def __init__(self, **kwargs) -> None:
        del kwargs


def not_an_evaluator(*, agent_config) -> object:
    """可调用但返回非评估器实例 ⇒ 报错。"""
    return object()
