"""分镜 Agent 的绑定薄工厂与槽位布局（021 C1/C3；注意槽位名是 `alignment` 而非 `proxies`）。

一评估器一函数、**纯关键字签名**：既有参数从 `agent_config` 槽位读取（**零拷贝**——
单一事实源仍是 `StoryboardConfig` 的 dataclass 字段与配置原段），故声明面 `params: {}`。
`SLOT_LAYOUT` 是槽位名的**唯一映射声明**，与 `build_storyboard_evaluators` 的返回形状
一一对齐（`all` 是装配点按本顺序拼接的派生汇总键，不可声明）。

本模块的模块级公开函数**必须**全部被 `configs/*.yaml` 的 `impl` 以
`agents.storyboard.evaluators.plugins:<attr>` 引用（孤立插件即不可用）。
"""

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.evaluators.alignment import EmotionAlignmentEvaluator
from agents.storyboard.evaluators.axis_rule import AxisRuleEvaluator
from agents.storyboard.evaluators.coverage import CoverageEvaluator
from agents.storyboard.evaluators.script_fit import ScriptFitJudgeEvaluator
from agents.storyboard.evaluators.shot_grammar import ShotGrammarEvaluator
from core.evaluators.base import Evaluator
from core.llm_gateway.gateway import LLMGateway

AGENT = "storyboard"
SLOT_LAYOUT = ("gates", "alignment", "judge")
SINGLE_EVALUATOR_SLOTS = ("alignment", "judge")


def shot_grammar(*, agent_config: StoryboardConfig) -> Evaluator:
    return ShotGrammarEvaluator(agent_config.shot_grammar)


def coverage(*, agent_config: StoryboardConfig) -> Evaluator:
    return CoverageEvaluator(agent_config.axis_rules)


def axis_rule(*, agent_config: StoryboardConfig) -> Evaluator:
    return AxisRuleEvaluator(agent_config.axis_rules)


def emotion_alignment(*, agent_config: StoryboardConfig) -> Evaluator:
    return EmotionAlignmentEvaluator(
        agent_config.alignment,
        agent_config.render,
        agent_config.shot_grammar,
        agent_config.emotion_vectors,
    )


def script_fit(*, agent_config: StoryboardConfig, gateway: LLMGateway) -> Evaluator:
    return ScriptFitJudgeEvaluator(
        gateway,
        model=agent_config.judge["model"],  # 价目表必须覆盖（缺价目网关即报错，不允许零成本）
        prompts=list(agent_config.judge["prompts"]),
        anchor_shotlists=agent_config.anchor_shotlists,
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
