"""剧本七评估器包（US2）：四 gate（节拍结构/页数换算/场景角色/对白占比）+ 两代理
（实体一致性/时间线冲突）+ judge（戏剧张力，仅大纲阶段）。

`build_screenplay_evaluators` 为**唯一装配点**（loop 接线 / CLI / 契约与无偏性测试共用）：
评估器组合与 `evaluator_weights.screenplay` 权重键一一对应（缺项/多项即拒绝装配，
防配置漂移）；参数全来自 ScreenplayConfig（节拍表/页数窗口/别名表/比例区间/judge
提示词与锚点大纲集零硬编码，原则五）。
"""

from agents.screenplay.config import ScreenplayConfig, ScreenplayConfigError
from agents.screenplay.evaluators.beat_structure import BeatStructureEvaluator
from agents.screenplay.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_screenplay,
    evaluate_screenplay,
)
from agents.screenplay.evaluators.dialogue_action_ratio import DialogueActionRatioEvaluator
from agents.screenplay.evaluators.dramatic_tension import DramaticTensionJudgeEvaluator
from agents.screenplay.evaluators.entity_consistency import EntityConsistencyEvaluator
from agents.screenplay.evaluators.page_minutes import PageMinutesEvaluator
from agents.screenplay.evaluators.scene_character import SceneCharacterEvaluator
from agents.screenplay.evaluators.timeline_conflict import TimelineConflictEvaluator
from core.llm_gateway.gateway import LLMGateway

__all__ = [
    "BeatStructureEvaluator",
    "DialogueActionRatioEvaluator",
    "DramaticTensionJudgeEvaluator",
    "EntityConsistencyEvaluator",
    "PageMinutesEvaluator",
    "SceneCharacterEvaluator",
    "TimelineConflictEvaluator",
    "COMPOSITE_POLICY",
    "build_screenplay_evaluators",
    "composite_screenplay",
    "evaluate_screenplay",
]


def build_screenplay_evaluators(config: ScreenplayConfig, gateway: LLMGateway) -> dict:
    """按 evaluator_weights.screenplay 装配真实七评估器（四 gate + 两 proxy + judge）。

    返回 {"gates", "proxies", "judge", "all"}——gate 先行评估，任一判 0 短路不跑 judge
    （省 LLM 成本；合成编排见 composite.py）。
    """
    gates = [
        BeatStructureEvaluator(config.beat_sheet),
        PageMinutesEvaluator(config.page_minutes_slice),
        SceneCharacterEvaluator(config.character_aliases),
        DialogueActionRatioEvaluator(config.dialogue_action_ratio),
    ]
    proxies = [
        EntityConsistencyEvaluator(config.character_aliases),
        TimelineConflictEvaluator(),
    ]
    judge = DramaticTensionJudgeEvaluator(
        gateway,
        model=config.judge["model"],  # 价目表必须覆盖（缺价目网关即报错，不允许零成本）
        prompts=list(config.judge["prompts"]),
        anchor_outlines=config.anchor_outlines,
    )
    all_evaluators = [*gates, *proxies, judge]
    # 权重节与评估器集合必须一一对应（缺项/多项即拒绝装配——配置漂移不得静默）
    assembled = {evaluator.spec.evaluator_id for evaluator in all_evaluators}
    weighted = set(config.evaluator_weights)
    if assembled != weighted:
        raise ScreenplayConfigError(
            "evaluator_weights.screenplay 与装配的评估器不一致："
            f"缺权重键 {sorted(assembled - weighted)}、多余权重键 {sorted(weighted - assembled)}"
        )
    return {"gates": gates, "proxies": proxies, "judge": judge, "all": all_evaluators}
