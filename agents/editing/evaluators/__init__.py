"""剪辑五评估器包（US2）：三 gate（duration/shot_distribution/transitions）
+ proxy.pacing_curve + judge.narrative_flow。

build_editing_evaluators 为唯一装配点（loop 接线 / 契约与无偏性测试共用）：
评估器组合与权重键一一对应（evaluator_weights.editing），参数全来自
EditingConfig（原则五：阈值/规则库/基准曲线/提示词/锚点集零硬编码）。
"""

from agents.editing.config import EditingConfig
from agents.editing.evaluators.duration import DurationComplianceEvaluator
from agents.editing.evaluators.narrative import NarrativeFlowJudgeEvaluator
from agents.editing.evaluators.pacing import PacingCurveEvaluator
from agents.editing.evaluators.shot_distribution import ShotDistributionEvaluator
from agents.editing.evaluators.transitions import TransitionRulesEvaluator
from core.llm_gateway.gateway import LLMGateway

__all__ = [
    "DurationComplianceEvaluator",
    "NarrativeFlowJudgeEvaluator",
    "PacingCurveEvaluator",
    "ShotDistributionEvaluator",
    "TransitionRulesEvaluator",
    "build_editing_evaluators",
]


def build_editing_evaluators(config: EditingConfig, gateway: LLMGateway) -> dict:
    """按 evaluator_weights.editing 装配真实五评估器（三 gate + proxy + judge）。

    返回 {"gates", "pacing", "judge", "all"}——gate 先行评估，任一判 0 短路
    不跑 judge（省 LLM 成本，004 同款纪律；合成编排见 composite.py）。
    """
    gates = [
        DurationComplianceEvaluator(config.target_duration_s, config.duration_tolerance_s),
        ShotDistributionEvaluator(config.shot_limits),
        TransitionRulesEvaluator(config.transition_rules),
    ]
    pacing = PacingCurveEvaluator(config.pacing_baseline)
    judge = NarrativeFlowJudgeEvaluator(
        gateway,
        model=config.judge.get("model", "mock-copy-v1"),  # 价目表必须覆盖（缺价目网关即报错）
        prompts=list(config.judge["prompts"]),
        anchor_edls=config.anchor_edls,
    )
    return {"gates": gates, "pacing": pacing, "judge": judge, "all": [*gates, pacing, judge]}
