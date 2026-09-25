"""剪辑五评估器包（US2）：三 gate（duration/shot_distribution/transitions）
+ proxy.pacing_curve + judge.narrative_flow。

build_editing_evaluators 为**唯一装配点**的委派入口（loop 接线 / 契约与无偏性测试共用）：
评估器集合与参数**完全由 `configs/*.yaml` 的 `evaluators.plugins.editing` 声明驱动**
（021 C1/C2）；集合与 `evaluator_weights.editing` 权重键一一对应，既有参数由
`EditingConfig` 的 dataclass 字段经 `agent_config` 槽位读取（原则五：阈值/规则库/
基准曲线/提示词/锚点集零硬编码）。
"""

from agents.editing.config import EditingConfig
from agents.editing.evaluators.duration import DurationComplianceEvaluator
from agents.editing.evaluators.narrative import NarrativeFlowJudgeEvaluator
from agents.editing.evaluators.pacing import PacingCurveEvaluator
from agents.editing.evaluators.plugins import AGENT, SLOT_LAYOUT, _to_return_shape
from agents.editing.evaluators.shot_distribution import ShotDistributionEvaluator
from agents.editing.evaluators.transitions import TransitionRulesEvaluator
from core.evaluators.plugin import assemble, parse_manifest
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
    """按 `evaluators.plugins.editing` 声明装配真实五评估器（三 gate + proxy + judge）。

    返回 {"gates", "pacing", "judge", "all"}——gate 先行评估，任一判 0 短路
    不跑 judge（省 LLM 成本，004 同款纪律；合成编排见 composite.py）。
    """
    manifest = parse_manifest(config.plugin_declarations, AGENT, slots=SLOT_LAYOUT)
    return _to_return_shape(assemble(manifest, agent_config=config, gateway=gateway))
