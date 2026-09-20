"""分镜五评估器包（US2）：三 gate（shot_grammar/coverage/axis_rule）+ alignment + judge。

`build_storyboard_evaluators` 为唯一装配点（loop 接线 / 契约与无偏性测试共用）：
评估器组合与权重键一一对应（evaluator_weights.storyboard），参数全来自 StoryboardConfig
（原则五：景别规则库/轴规则/情绪向量/对齐阈值/提示词/锚点集零硬编码）。
"""

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.evaluators.alignment import EmotionAlignmentEvaluator
from agents.storyboard.evaluators.axis_rule import AxisRuleEvaluator
from agents.storyboard.evaluators.coverage import CoverageEvaluator
from agents.storyboard.evaluators.script_fit import ScriptFitJudgeEvaluator
from agents.storyboard.evaluators.shot_grammar import ShotGrammarEvaluator
from core.llm_gateway.gateway import LLMGateway

__all__ = [
    "AxisRuleEvaluator",
    "CoverageEvaluator",
    "EmotionAlignmentEvaluator",
    "ScriptFitJudgeEvaluator",
    "ShotGrammarEvaluator",
    "build_storyboard_evaluators",
]


def build_storyboard_evaluators(config: StoryboardConfig, gateway: LLMGateway) -> dict:
    """按 evaluator_weights.storyboard 装配真实五评估器（三 gate + alignment + judge）。

    返回 {"gates", "alignment", "judge", "all"}——gate 先行评估，任一判 0 短路
    不跑 judge（省 LLM 成本，004/007 同款纪律；合成编排见 composite.py）。
    """
    gates = [
        ShotGrammarEvaluator(config.shot_grammar),
        CoverageEvaluator(config.axis_rules),
        AxisRuleEvaluator(config.axis_rules),
    ]
    alignment = EmotionAlignmentEvaluator(
        config.alignment, config.render, config.shot_grammar, config.emotion_vectors
    )
    judge = ScriptFitJudgeEvaluator(
        gateway,
        model=config.judge["model"],  # 价目表必须覆盖（缺价目网关即报错，不允许零成本）
        prompts=list(config.judge["prompts"]),
        anchor_shotlists=config.anchor_shotlists,
    )
    return {
        "gates": gates,
        "alignment": alignment,
        "judge": judge,
        "all": [*gates, alignment, judge],
    }
