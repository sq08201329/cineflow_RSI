"""剧本覆盖率门禁评估器：rule.coverage（硬规则门禁，C5，澄清 Q1 口径）。

判定标准（只有两条，不被其他门禁改写——覆盖率口径优先）：
① **场景级**（硬要求）：剧本每个场景至少一镜承接；
② **必覆盖清单**：剧本 `key=True` 行逐条被 shots 的 `covers` 承接。
普通台词行的合并（一镜多行）/拆分（一行多镜）不违规（逐条全覆盖会误杀合法分镜手法）。

诚实边界（原则六）：
- 必覆盖清单为空（剧本未标注关键行）→ 降级纯场景级判定并在 diagnostics 注明
  `must_cover_degraded`（不伪造关键行要求）；
- 未承接的行**逐条**列入 `uncovered_lines`（含非关键行）——单场景行数超镜头数上限时
  如实暴露缺口，不静默截断剧本、不悄悄补镜；
- 覆盖率达成依赖越轴（超过渡额度）→ `conflicts` 如实注明与轴规则的冲突，
  但**不自动豁免**轴规则门禁（其仍判 0，冲突由合成体现为总分 0）。
"""

import json

from agents.storyboard.config import StoryboardConfigError
from agents.storyboard.evaluators._versioning import implementation_version
from agents.storyboard.evaluators.axis_rule import axis_excursions
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.coverage"


class CoverageEvaluator(Evaluator):
    """场景级 + 必覆盖清单门禁（确定性、零成本；轴规则参数来自配置）。"""

    def __init__(self, axis_rules: dict) -> None:
        if not isinstance(axis_rules, dict) or not axis_rules:
            raise StoryboardConfigError(
                "rule.coverage 缺轴规则配置（冲突说明依赖同一轴口径），拒绝启动"
            )
        self._axis_rules = dict(axis_rules)
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(axis_rules, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        shotlist: ShotList = context["shotlist"]
        script: ScriptSegment = context["script"]
        covered = set(shotlist.covered_line_ids())
        known_lines = set(script.line_ids())
        uncovered_scenes = [
            scene_id for scene_id in script.scene_ids() if not shotlist.shots_of_scene(scene_id)
        ]
        uncovered_key_lines = [
            line_id for line_id in script.key_line_ids() if line_id not in covered
        ]
        uncovered_lines = [line_id for line_id in script.line_ids() if line_id not in covered]
        unknown_lines = sorted(covered - known_lines)
        degraded = not script.key_line_ids()

        # 冲突说明：覆盖率达成依赖越轴且超过渡额度 → 与轴规则冲突如实暴露（不自动豁免）
        conflicts = [
            f"场景 {e['scene_id']} 覆盖率达成依赖侧 {e['side']} 越轴 {e['length']} 镜"
            f"(镜头 {e['shot_ids']})，超过渡额度 {e['allowed']}（轴线基准 {e['base']}）"
            f"——覆盖率与轴规则冲突，如实暴露不自动豁免"
            for e in axis_excursions(shotlist, script, self._axis_rules)
            if e["over_limit"]
        ]

        violations: list[str] = []
        if uncovered_scenes:
            violations.append(f"场景无镜头承接（场景级硬要求）：{uncovered_scenes}")
        if uncovered_key_lines:
            violations.append(f"必覆盖清单未逐条承接：{uncovered_key_lines}")
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "scene_count": len(script.scene_ids()),
                "covered_scene_count": len(script.scene_ids()) - len(uncovered_scenes),
                "uncovered_scenes": uncovered_scenes,
                "key_line_count": len(script.key_line_ids()),
                "uncovered_key_lines": uncovered_key_lines,
                "uncovered_lines": uncovered_lines,  # 逐行缺口：如实暴露不静默截断
                "unknown_lines": unknown_lines,  # 引用存在性由执行前第①层保证，此处如实记录
                "must_cover_degraded": degraded,
                "note": (
                    "必覆盖清单为空，coverage 降级纯场景级判定（不伪造关键行要求）"
                    if degraded
                    else "场景级 + 必覆盖清单判定"
                ),
                "conflicts": conflicts,
                "violations": violations,
            },
        )
