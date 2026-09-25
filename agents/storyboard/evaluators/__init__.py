"""分镜五评估器包（US2）：三 gate（shot_grammar/coverage/axis_rule）+ alignment + judge。

`build_storyboard_evaluators` 为**唯一装配点**的委派入口（loop 接线 / 契约与无偏性测试
共用）：评估器集合与参数**完全由 `configs/*.yaml` 的 `evaluators.plugins.storyboard`
声明驱动**（021 C1/C2）；评估器集合与权重键一一对应（`evaluator_weights.storyboard`），
既有参数由 `StoryboardConfig` 的 dataclass 字段经 `agent_config` 槽位读取（原则五：
景别规则库/轴规则/情绪向量/对齐阈值/提示词/锚点集零硬编码）。
"""

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.evaluators.alignment import EmotionAlignmentEvaluator
from agents.storyboard.evaluators.axis_rule import AxisRuleEvaluator
from agents.storyboard.evaluators.coverage import CoverageEvaluator
from agents.storyboard.evaluators.plugins import AGENT, SLOT_LAYOUT, _to_return_shape
from agents.storyboard.evaluators.script_fit import ScriptFitJudgeEvaluator
from agents.storyboard.evaluators.shot_grammar import ShotGrammarEvaluator
from core.evaluators.plugin import assemble, parse_manifest
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
    """按 `evaluators.plugins.storyboard` 声明装配真实五评估器（三 gate + alignment + judge）。

    返回 {"gates", "alignment", "judge", "all"}——gate 先行评估，任一判 0 短路
    不跑 judge（省 LLM 成本，004/007 同款纪律；合成编排见 composite.py）。
    """
    manifest = parse_manifest(config.plugin_declarations, AGENT, slots=SLOT_LAYOUT)
    return _to_return_shape(assemble(manifest, agent_config=config, gateway=gateway))
