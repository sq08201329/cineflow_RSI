"""轴规则门禁评估器：rule.axis_rule（硬规则门禁，C6）。

180° 线机检口径（research 决策 5）：机位侧别（`side` ∈ A|B）跳变必须由过渡镜头完成，
`allowed_transition_shots` 为过渡额度（越轴是合法手法，直接的侧别硬跳才是错误）。

判定（逐场景，场景切换重置轴线——剧本 `axis_base` 即该场景的轴线基准）：
- 场景内镜头侧别 == 轴线基准 → 无越轴；
- 侧别 ≠ 基准的**极大连续段**（越轴段）= 过渡镜头：段长 ≤ 额度 → 合法过渡；
  段长 > 额度 → 硬跳违规（摄影机越过 180° 线且无过渡回切）。
基准缺失时以该场景首镜侧别为基准（如实口径，不臆造）。

`axis_excursions` 为越轴段清单的**唯一事实源**：本门禁与 rule.coverage 的冲突说明
共用同一计算（覆盖率达成依赖越轴时在覆盖率 diagnostics 如实注明，不自动豁免）。
"""

import json

from agents.storyboard.config import StoryboardConfigError
from agents.storyboard.evaluators._versioning import implementation_version
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotEntry, ShotList
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.axis_rule"


def _require(axis_rules: dict, key: str):
    value = axis_rules.get(key) if isinstance(axis_rules, dict) else None
    if value is None:
        raise StoryboardConfigError(
            f"rule.axis_rule 缺规则库配置项 {key!r}，拒绝启动——不允许静默放过门禁（原则五）"
        )
    return value


def _group_by_scene(shotlist: ShotList) -> list[tuple[str, list[ShotEntry]]]:
    """按镜头出现顺序分组（场景切换 = 新组）：轴线随场景重置。"""
    groups: list[tuple[str, list[ShotEntry]]] = []
    for shot in shotlist.shots:
        if not groups or groups[-1][0] != shot.scene_id:
            groups.append((shot.scene_id, [shot]))
        else:
            groups[-1][1].append(shot)
    return groups


def _scene_base(scene_id: str, script: ScriptSegment | None, shots: list[ShotEntry]) -> str:
    """场景轴线基准：剧本 axis_base 优先；缺失时以该场景首镜侧别为准（如实标注）。"""
    if isinstance(script, ScriptSegment):
        for scene in script.scenes:
            if scene.scene_id == scene_id and scene.axis_base:
                return scene.axis_base
    return shots[0].side


def axis_excursions(
    shotlist: ShotList, script: ScriptSegment | None, axis_rules: dict
) -> list[dict]:
    """越轴段清单：逐场景给出侧别 ≠ 轴线基准的极大连续段（含额度与是否超限）。"""
    allowed = int(_require(axis_rules, "allowed_transition_shots"))
    excursions: list[dict] = []
    for scene_id, shots in _group_by_scene(shotlist):
        base = _scene_base(scene_id, script, shots)
        current: list[str] = []
        current_side: str | None = None
        for shot in [*shots, None]:  # 末尾 None 作哨兵，冲刷最后一段
            side = None if shot is None else shot.side
            if side == base:
                if current:
                    excursions.append(_excursion(scene_id, base, current_side, current, allowed))
                    current, current_side = [], None
                continue
            if side is None or side != current_side:
                if current:
                    excursions.append(_excursion(scene_id, base, current_side, current, allowed))
                current, current_side = ([shot.shot_id], side) if shot is not None else ([], None)
            else:
                current.append(shot.shot_id)
    return excursions


def _excursion(
    scene_id: str, base: str, side: str | None, shot_ids: list[str], allowed: int
) -> dict:
    return {
        "scene_id": scene_id,
        "base": base,
        "side": side,
        "shot_ids": list(shot_ids),
        "length": len(shot_ids),
        "allowed": allowed,
        "over_limit": len(shot_ids) > allowed,
    }


class AxisRuleEvaluator(Evaluator):
    """轴规则门禁（确定性、零成本；规则库全配置驱动）。"""

    def __init__(self, axis_rules: dict) -> None:
        self._rules = dict(axis_rules) if isinstance(axis_rules, dict) else {}
        self._require_cross = bool(_require(axis_rules, "require_transition_on_cross"))
        self._allowed = int(_require(axis_rules, "allowed_transition_shots"))
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
        script = context.get("script")
        excursions = axis_excursions(shotlist, script, self._rules)
        violations = [
            f"侧别硬跳无过渡：场景 {e['scene_id']} 侧 {e['side']} 越轴 {e['length']} 镜 "
            f"(镜头 {e['shot_ids']}) 超过渡额度 {e['allowed']}（轴线基准 {e['base']}）"
            for e in excursions
            if e["over_limit"] and self._require_cross
        ]
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "require_transition_on_cross": self._require_cross,
                "allowed_transition_shots": self._allowed,
                "axis_bases": {
                    scene_id: _scene_base(scene_id, script, shots)
                    for scene_id, shots in _group_by_scene(shotlist)
                },
                "excursions": excursions,
                "violations": violations,
            },
        )
