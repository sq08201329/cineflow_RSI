"""镜头语法门禁评估器：rule.shot_grammar（硬规则门禁，C4）。

景别档位是**有序枚举**（由近及远），相邻镜头景别跳跃 = 档位序号差：
- 跳跃 > `max_size_jump` → 违规（跳切式景别突变，观众空间感断裂）；
- 同景别连续 > `max_same_size_run` → 违规（镜头语言单调重复）。
规则库 = `storyboard.shot_grammar` 段（与执行前校验第③层共用同一配置，单一事实源）；
缺规则库即拒绝启动（不允许静默放过门禁，原则五）。违规 → gate 判 0。
"""

import json

from agents.storyboard.config import StoryboardConfigError
from agents.storyboard.evaluators._versioning import implementation_version
from agents.storyboard.shotlist import ShotList
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.shot_grammar"


def _require(grammar: dict, key: str):
    value = grammar.get(key) if isinstance(grammar, dict) else None
    if value is None:
        raise StoryboardConfigError(
            f"rule.shot_grammar 缺规则库配置项 {key!r}，拒绝启动——不允许静默放过门禁（原则五）"
        )
    return value


class ShotGrammarEvaluator(Evaluator):
    """景别语法门禁（确定性、零成本；规则库全配置驱动）。"""

    def __init__(self, shot_grammar: dict) -> None:
        self._sizes = list(_require(shot_grammar, "shot_sizes"))
        self._max_size_jump = int(_require(shot_grammar, "max_size_jump"))
        self._max_same_size_run = int(_require(shot_grammar, "max_same_size_run"))
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(shot_grammar, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def _rank(self, shot_size: str) -> int | None:
        return self._sizes.index(shot_size) if shot_size in self._sizes else None

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        shotlist: ShotList = context["shotlist"]
        violations: list[str] = []
        run_size: str | None = None
        run_shots: list[str] = []
        max_run = 0
        for index, shot in enumerate(shotlist.shots):
            if index > 0:
                prev = shotlist.shots[index - 1]
                rank_prev, rank_cur = self._rank(prev.shot_size), self._rank(shot.shot_size)
                if rank_prev is not None and rank_cur is not None:
                    jump = abs(rank_cur - rank_prev)
                    if jump > self._max_size_jump:
                        violations.append(
                            f"景别跳跃超限：{prev.shot_id}({prev.shot_size}) → "
                            f"{shot.shot_id}({shot.shot_size}) 序号差 {jump} > "
                            f"max_size_jump {self._max_size_jump}"
                        )
            if shot.shot_size == run_size:
                run_shots.append(shot.shot_id)
            else:
                run_size, run_shots = shot.shot_size, [shot.shot_id]
            max_run = max(max_run, len(run_shots))
            if len(run_shots) > self._max_same_size_run:
                violations.append(
                    f"同景别连续超限：{run_shots} 连续 {len(run_shots)} 镜同景别 "
                    f"{run_size} > max_same_size_run {self._max_same_size_run}"
                )
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "shot_count": len(shotlist.shots),
                "shot_sizes": [shot.shot_size for shot in shotlist.shots],
                "max_size_jump": self._max_size_jump,
                "max_same_size_run": self._max_same_size_run,
                "max_same_size_run_observed": max_run,
                "violations": violations,
            },
        )
