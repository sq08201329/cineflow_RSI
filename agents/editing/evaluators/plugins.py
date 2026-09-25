"""剪辑 Agent 的绑定薄工厂与槽位布局（021 C1/C3）。

一评估器一函数、**纯关键字签名**：既有参数从 `agent_config` 槽位读取（**零拷贝**——
单一事实源仍是 `EditingConfig` 的 dataclass 字段与配置原段），故声明面 `params: {}`。
`SLOT_LAYOUT` 是槽位名的**唯一映射声明**，与 `build_editing_evaluators` 的返回形状
一一对齐（`all` 是装配点按本顺序拼接的派生汇总键，不可声明）。

本模块的模块级公开函数**必须**全部被 `configs/*.yaml` 的 `impl` 以
`agents.editing.evaluators.plugins:<attr>` 引用（孤立插件即不可用）。
"""

from agents.editing.config import EditingConfig
from agents.editing.evaluators.duration import DurationComplianceEvaluator
from agents.editing.evaluators.narrative import NarrativeFlowJudgeEvaluator
from agents.editing.evaluators.pacing import PacingCurveEvaluator
from agents.editing.evaluators.shot_distribution import ShotDistributionEvaluator
from agents.editing.evaluators.transitions import TransitionRulesEvaluator
from core.evaluators.base import Evaluator
from core.llm_gateway.gateway import LLMGateway

AGENT = "editing"
SLOT_LAYOUT = ("gates", "pacing", "judge")
SINGLE_EVALUATOR_SLOTS = ("pacing", "judge")


def duration_compliance(*, agent_config: EditingConfig) -> Evaluator:
    return DurationComplianceEvaluator(
        agent_config.target_duration_s, agent_config.duration_tolerance_s
    )


def shot_distribution(*, agent_config: EditingConfig) -> Evaluator:
    return ShotDistributionEvaluator(agent_config.shot_limits)


def transition_rules(*, agent_config: EditingConfig) -> Evaluator:
    return TransitionRulesEvaluator(agent_config.transition_rules)


def pacing_curve(*, agent_config: EditingConfig) -> Evaluator:
    return PacingCurveEvaluator(agent_config.pacing_baseline)


def narrative_flow(*, agent_config: EditingConfig, gateway: LLMGateway) -> Evaluator:
    return NarrativeFlowJudgeEvaluator(
        gateway,
        # 价目表必须覆盖（缺价目网关即报错）
        model=agent_config.judge.get("model", "mock-copy-v1"),
        prompts=list(agent_config.judge["prompts"]),
        anchor_edls=agent_config.anchor_edls,
        max_tokens=agent_config.judge["max_tokens"],
    )


def _to_return_shape(assembled: dict) -> dict:
    """把装配点的槽位映射（每槽位 `list`）还原为该 Agent **既有返回形状**：单评估器槽位取
    首元素，列表槽位（含派生的 `all`）原样——签名与返回形状逐字不变是 021 C2 的兼容承诺。
    """
    return {
        slot: (instances[0] if slot in SINGLE_EVALUATOR_SLOTS else instances)
        for slot, instances in assembled.items()
    }
