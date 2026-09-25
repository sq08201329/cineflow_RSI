"""剧本 Agent 的绑定薄工厂与槽位布局（021 C1/C3）。

一评估器一函数、**纯关键字签名**：既有参数从 `agent_config` 槽位读取（**零拷贝**——
单一事实源仍是 `ScreenplayConfig` 的 dataclass 字段与配置原段），故声明面 `params: {}`。
`SLOT_LAYOUT` 是槽位名的**唯一映射声明**，与 `build_screenplay_evaluators` 的返回形状
一一对齐（`all` 是装配点按本顺序拼接的派生汇总键，不可声明）。

本模块的模块级公开函数**必须**全部被 `configs/*.yaml` 的 `impl` 以
`agents.screenplay.evaluators.plugins:<attr>` 引用（孤立插件即不可用）。
"""

from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.evaluators.beat_structure import BeatStructureEvaluator
from agents.screenplay.evaluators.dialogue_action_ratio import DialogueActionRatioEvaluator
from agents.screenplay.evaluators.dramatic_tension import DramaticTensionJudgeEvaluator
from agents.screenplay.evaluators.entity_consistency import EntityConsistencyEvaluator
from agents.screenplay.evaluators.page_minutes import PageMinutesEvaluator
from agents.screenplay.evaluators.scene_character import SceneCharacterEvaluator
from agents.screenplay.evaluators.timeline_conflict import TimelineConflictEvaluator
from core.evaluators.base import Evaluator
from core.llm_gateway.gateway import LLMGateway

AGENT = "screenplay"
SLOT_LAYOUT = ("gates", "proxies", "judge")
SINGLE_EVALUATOR_SLOTS = ("judge",)


def beat_structure(*, agent_config: ScreenplayConfig) -> Evaluator:
    return BeatStructureEvaluator(agent_config.beat_sheet)


def page_minutes(*, agent_config: ScreenplayConfig) -> Evaluator:
    return PageMinutesEvaluator(agent_config.page_minutes_slice)


def scene_character(*, agent_config: ScreenplayConfig) -> Evaluator:
    return SceneCharacterEvaluator(agent_config.character_aliases)


def dialogue_action_ratio(*, agent_config: ScreenplayConfig) -> Evaluator:
    return DialogueActionRatioEvaluator(agent_config.dialogue_action_ratio)


def entity_consistency(*, agent_config: ScreenplayConfig) -> Evaluator:
    return EntityConsistencyEvaluator(agent_config.character_aliases)


def timeline_conflict(*, agent_config: ScreenplayConfig) -> Evaluator:
    return TimelineConflictEvaluator()


def dramatic_tension(*, agent_config: ScreenplayConfig, gateway: LLMGateway) -> Evaluator:
    return DramaticTensionJudgeEvaluator(
        gateway,
        model=agent_config.judge["model"],  # 价目表必须覆盖（缺价目网关即报错，不允许零成本）
        prompts=list(agent_config.judge["prompts"]),
        anchor_outlines=agent_config.anchor_outlines,
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
