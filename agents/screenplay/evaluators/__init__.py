"""剧本七评估器包（US2）：四 gate（节拍结构/页数换算/场景角色/对白占比）+ 两代理
（实体一致性/时间线冲突）+ judge（戏剧张力，仅大纲阶段）。

`build_screenplay_evaluators` 为**唯一装配点**的委派入口（loop 接线 / CLI / 契约与无偏性
测试共用）：评估器集合与参数**完全由 `configs/*.yaml` 的 `evaluators.plugins.screenplay`
声明驱动**（021 C1/C2：解析与实例化收在 `core/evaluators/plugin.py`，本函数只传业务侧
槽位布局与已解析配置）；评估器集合与 `evaluator_weights.screenplay` 权重键一一对应
（缺项/多项即拒绝装配，防配置漂移）；既有参数仍由 `ScreenplayConfig` 的 dataclass 字段
经 `agent_config` 槽位读取（单一事实源，原则五）。
"""

from agents.screenplay.config import ScreenplayConfig
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
from agents.screenplay.evaluators.plugins import AGENT, SLOT_LAYOUT, _to_return_shape
from agents.screenplay.evaluators.scene_character import SceneCharacterEvaluator
from agents.screenplay.evaluators.timeline_conflict import TimelineConflictEvaluator
from core.evaluators.plugin import assemble, parse_manifest
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
    """按 `evaluators.plugins.screenplay` 声明装配真实七评估器（四 gate + 两 proxy + judge）。

    返回 {"gates", "proxies", "judge", "all"}——gate 先行评估，任一判 0 短路不跑 judge
    （省 LLM 成本；合成编排见 composite.py）。签名与返回形状与改造前逐字一致；
    缺 `evaluators` 段即解声明期报错（不回落硬编码装配）。
    """
    manifest = parse_manifest(config.plugin_declarations, AGENT, slots=SLOT_LAYOUT)
    return _to_return_shape(assemble(manifest, agent_config=config, gateway=gateway))
