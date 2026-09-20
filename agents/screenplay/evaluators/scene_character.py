"""场景-角色一致性门禁评估器：rule.scene_character（硬规则门禁，C6）。

逐场景两项机检（均确定性、零成本）：
- **幽灵角色**：场景出场角色清单 ∈ 登记写法集合（工件角色表 name ∪ aliases 与配置
  `character_aliases` 别名表的并集）——出现未登记角色即判 0（人物表与场景表脱节）；
- **地点一致**：场景头（`内景|外景 - 地点 - 时间描述`）的地点段与 `location` 字段
  一致（场景头与结构化标记互不矛盾）——不一致即判 0。

角色表缺失即拒绝启动（配置纪律）。行级归属指称（对白行说话人写法）**不在本门禁**：
名称变体/未登记写法由 `proxy.entity_consistency` 连续打分并诊断（C6 判场景-角色结构，
C8 判指称口径），避免同一缺陷被门禁与代理重复计。
"""

import json

from agents.screenplay.artifact import heading_parts
from agents.screenplay.config import ScreenplayConfigError
from agents.screenplay.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.scene_character"


class SceneCharacterEvaluator(Evaluator):
    """场景-角色一致性门禁（确定性、零成本；别名表 = 登记口径）。"""

    def __init__(self, character_aliases: dict) -> None:
        if not isinstance(character_aliases, dict) or not character_aliases:
            raise ScreenplayConfigError(
                "rule.scene_character 缺角色表（character_aliases），拒绝启动"
                "——不允许静默放过门禁（原则五）"
            )
        self._registered = frozenset(
            spelling for name, aliases in character_aliases.items() for spelling in (name, *aliases)
        )
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(character_aliases, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        script = context["artifact"]
        # 登记写法 = 配置别名表 ∪ 工件角色表（两处同源口径；任一登记即非幽灵）
        registered = self._registered | script.registered_names()
        ghost_characters: list[str] = []
        for scene in script.scenes:
            for name in scene.characters:
                if name not in registered:
                    ghost_characters.append(f"{scene.scene_id}:{name}")
        location_mismatches = []
        for scene in script.scenes:
            heading_location = heading_parts(scene.heading)[1]
            if heading_location != scene.location:
                location_mismatches.append(
                    {
                        "scene_id": scene.scene_id,
                        "heading_location": heading_location,
                        "location": scene.location,
                    }
                )
        violations: list[str] = []
        if ghost_characters:
            violations.append(f"幽灵角色（未登记出场角色）：{ghost_characters}")
        for item in location_mismatches:
            violations.append(
                f"场景 {item['scene_id']} 地点不一致：场景头 {item['heading_location']!r} "
                f"≠ location {item['location']!r}"
            )
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "scene_count": len(script.scenes),
                "registered_names": sorted(registered),
                "ghost_characters": ghost_characters,
                "location_mismatches": location_mismatches,
                "violations": violations,
            },
        )
