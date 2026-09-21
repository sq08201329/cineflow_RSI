"""命中分布与稀释控制（功能 011 US2，contracts/replay-hit.md C4）。

- per-project 与合并口径命中/UNKNOWN **双报告**（合并 = 逐项目合计，双记账一致）；
- **判定口径 = 命中占比**（该项目命中数 / 总命中数）超阈（`dilution_hit_ratio_threshold`，
  默认 0.7）→ `DilutionAlert`；树数占比作**参考维度**同报告（澄清 Q2）；
- 单项目构成（树数占比 1.0）→ 命中占比 1.0 必然超阈，note 如实标注"单项目构成"（SC-004）；
- 冲突（UNKNOWN 的理由）进 `HitDistribution.conflicts`，供 010 校准与 F7 漂移检测消费；
- 告警如实可见、不阻止回放、不自动回退（原则六）；UNKNOWN 率一并如实记录（FR-012）。

归属口径（可重现）：一次命中归属"首个命中树所属项目"（命中清单按池序
(created_at, project_id, tree_id)）；一次 UNKNOWN 归属"被 probe 的树所属项目"
（该项目的树未能提供该结构键）——每次结果恰好归属一个项目，双报告口径自洽。
"""

from dataclasses import dataclass
from typing import Literal

from core.replay.cross_match import MatchResult
from core.replay.errors import ValidationError
from core.replay.pooling_models import (
    DilutionAlert,
    HitDistribution,
    MergedPool,
    PoolingConfig,
    ProjectHitStats,
    ScoreConflict,
)


@dataclass(frozen=True)
class ReplayOutcome:
    """一次池化 probe 的结果归属（命中分布统计的输入，C4）。

    - status = "hit"：tree_id/project_id = 命中树（首个命中，按池序）；conflict 必为 None；
    - status = "unknown"：tree_id/project_id = 被 probe 的树；conflict 非空即
      "冲突即 UNKNOWN" 的那一类（诊断留痕）。
    """

    status: Literal["hit", "unknown"]
    tree_id: str
    project_id: str
    conflict: ScoreConflict | None = None

    def __post_init__(self) -> None:
        if self.status not in ("hit", "unknown"):
            raise ValidationError(f"status 必须为 'hit' 或 'unknown'，实际为 {self.status!r}")
        if not isinstance(self.tree_id, str) or not self.tree_id:
            raise ValidationError(f"tree_id 必须为非空字符串，实际为 {self.tree_id!r}")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ValidationError(f"project_id 必须为非空字符串，实际为 {self.project_id!r}")
        if self.conflict is not None and not isinstance(self.conflict, ScoreConflict):
            raise ValidationError("conflict 必须为 ScoreConflict 或 None")
        if self.status == "hit" and self.conflict is not None:
            raise ValidationError("命中结果不得携带冲突（冲突即 UNKNOWN，不是命中）")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "tree_id": self.tree_id,
            "project_id": self.project_id,
            "conflict": None if self.conflict is None else self.conflict.to_dict(),
        }


def outcome_for_match(
    result: MatchResult, *, probe_tree_id: str, probe_project_id: str | None = None
) -> ReplayOutcome:
    """由匹配结果构造回放结果归属（命中 → 首个命中树；UNKNOWN → 被 probe 的树）。

    UNKNOWN 必须给出 probe_project_id（被探测树的项目归属，池内索引可查）——
    命中不需要它（归属取自命中清单的池序首个）。
    """
    if not isinstance(result, MatchResult):
        raise ValidationError(f"result 必须为 MatchResult，实际为 {type(result).__name__}")
    if not isinstance(probe_tree_id, str) or not probe_tree_id:
        raise ValidationError(f"probe_tree_id 必须为非空字符串，实际为 {probe_tree_id!r}")
    if result.status == "hit":
        first = result.hits[0]
        return ReplayOutcome(status="hit", tree_id=first.tree_id, project_id=first.project_id)
    if not isinstance(probe_project_id, str) or not probe_project_id:
        raise ValidationError("UNKNOWN 结果必须提供 probe_project_id（被探测树的项目归属）")
    return ReplayOutcome(
        status="unknown",
        tree_id=probe_tree_id,
        project_id=probe_project_id,
        conflict=result.conflict,
    )


def hit_stats(
    pool: MergedPool,
    outcomes,
    cfg: PoolingConfig,
) -> tuple[HitDistribution, list[DilutionAlert]]:
    """命中分布双报告 + 稀释告警（C4）。

    - per_project：池内每个项目一行（零命中也列出——"没信号"同样如实可见）；
    - 判定：project 命中占比 > cfg.dilution_hit_ratio_threshold → DilutionAlert
      （树数占比仅作参考，不参与判定）；
    - conflicts：去重后按结构键排序的冲突清单 + 频次如实记录在分布附注。
    """
    if not isinstance(pool, MergedPool):
        raise ValidationError(f"pool 必须为 MergedPool，实际为 {type(pool).__name__}")
    if not isinstance(cfg, PoolingConfig):
        raise ValidationError(f"cfg 必须为 PoolingConfig，实际为 {type(cfg).__name__}")
    pool_tree_ids = {ref.tree_id for ref in pool.trees}
    for outcome in outcomes:
        if not isinstance(outcome, ReplayOutcome):
            raise ValidationError(f"outcomes 的元素必须为 ReplayOutcome，实际为 {outcome!r}")
        if outcome.tree_id not in pool_tree_ids:
            raise ValidationError(f"结果的归属树必须属于池：{outcome.tree_id}")

    tree_count_by_project: dict[str, int] = {}
    for ref in pool.trees:
        tree_count_by_project[ref.project_id] = tree_count_by_project.get(ref.project_id, 0) + 1
    total_trees = sum(tree_count_by_project.values())

    hits: dict[str, int] = dict.fromkeys(tree_count_by_project, 0)
    unknowns: dict[str, int] = dict.fromkeys(tree_count_by_project, 0)
    conflicts: list[ScoreConflict] = []
    conflict_occurrences = 0
    for outcome in outcomes:
        if outcome.status == "hit":
            hits[outcome.project_id] += 1
        else:
            unknowns[outcome.project_id] += 1
        if outcome.conflict is not None:
            conflict_occurrences += 1
            if outcome.conflict not in conflicts:
                conflicts.append(outcome.conflict)

    merged_hits = sum(hits.values())
    merged_unknowns = sum(unknowns.values())
    per_project = tuple(
        ProjectHitStats(
            project_id=project_id,
            hits=hits[project_id],
            unknowns=unknowns[project_id],
            hit_ratio=(hits[project_id] / merged_hits) if merged_hits else 0.0,
            tree_count=tree_count_by_project[project_id],
            tree_ratio=tree_count_by_project[project_id] / total_trees,
        )
        for project_id in sorted(tree_count_by_project)
    )

    alerts = [
        DilutionAlert(
            project_id=stats.project_id,
            hit_ratio=stats.hit_ratio,
            tree_ratio=stats.tree_ratio,
            threshold=cfg.dilution_hit_ratio_threshold,
            note=(
                f"命中占比 {stats.hit_ratio:.4f} > 阈值 {cfg.dilution_hit_ratio_threshold}"
                "（判定口径 = 命中占比；树数占比 "
                f"{stats.tree_ratio:.4f} 仅作参考）"
                + ("；单项目构成" if stats.tree_ratio == 1.0 else "")
            ),
        )
        for stats in per_project
        if stats.hit_ratio > cfg.dilution_hit_ratio_threshold
    ]
    alerts.sort(key=lambda alert: (-alert.hit_ratio, alert.project_id))

    distribution = HitDistribution(
        per_project=per_project,
        merged_hits=merged_hits,
        merged_unknowns=merged_unknowns,
        conflicts=tuple(sorted(conflicts, key=lambda conflict: conflict.structure_key)),
        note=_distribution_note(merged_hits, merged_unknowns, conflict_occurrences, len(conflicts)),
    )
    return distribution, alerts


def _distribution_note(
    merged_hits: int, merged_unknowns: int, occurrences: int, distinct: int
) -> str:
    """分布附注：UNKNOWN 率与冲突频次如实记录（不自动回退，供人工判断，FR-012）。"""
    total = merged_hits + merged_unknowns
    unknown_ratio = (merged_unknowns / total) if total else 0.0
    parts = [f"命中 {merged_hits} / UNKNOWN {merged_unknowns}（UNKNOWN 率 {unknown_ratio:.4f}）"]
    if occurrences:
        parts.append(f"冲突 {occurrences} 次（去重后 {distinct} 项，如实留痕供 010/F7 消费）")
    return "；".join(parts)
