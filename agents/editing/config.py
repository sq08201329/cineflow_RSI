"""剪辑形态配置（configs/*.yaml 的 editing 段 → EditingConfig）。

配置即形态（宪章原则五）：探索预算、时长容差、镜头限制、转场规则库（执行前
校验与门禁共用单一事实源）、分段节奏基准、渲染价目与编码参数、judge 提示词
与锚点 EDL 集全部来自配置；基准曲线缺失即报错（不允许静默无基准打分）、
价目缺失即报错（不允许静默零成本，003 同款纪律）、编码强制单线程确定性档
（SC-002，research 决策 2）。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agents.editing.edl import EditDecisionList


class EditingConfigError(Exception):
    """剪辑配置缺失/非法。"""


def _require(mapping: dict, key: str, where: str):
    value = mapping.get(key)
    if value is None:
        raise EditingConfigError(f"{where} 缺少配置项 {key!r}")
    return value


def _require_pacing_baseline(baseline: dict) -> dict:
    """分段基准曲线：d_cap + 非空 segments，各段 span/mean_ms/var_ms/weight 齐全。"""
    _require(baseline, "d_cap", "editing.pacing_baseline")
    segments = _require(baseline, "segments", "editing.pacing_baseline")
    if not isinstance(segments, list) or not segments:
        raise EditingConfigError("editing.pacing_baseline.segments 必须为非空列表")
    for i, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise EditingConfigError(f"editing.pacing_baseline.segments[{i}] 必须为 dict")
        for key in ("span", "mean_ms", "var_ms", "weight"):
            _require(segment, key, f"editing.pacing_baseline.segments[{i}]")
    return {"d_cap": float(baseline["d_cap"]), "segments": [dict(s) for s in segments]}


def _require_transition_rules(rules: dict) -> dict:
    """转场规则库：allowed 非空列表 + dissolve_max_ms + forbid_jump_cut_within_scene。"""
    allowed = _require(rules, "allowed", "editing.transition_rules")
    if not isinstance(allowed, list) or not allowed:
        raise EditingConfigError("editing.transition_rules.allowed 必须为非空列表")
    _require(rules, "dissolve_max_ms", "editing.transition_rules")
    _require(rules, "forbid_jump_cut_within_scene", "editing.transition_rules")
    return dict(rules)


def _require_render(render: dict) -> dict:
    """渲染参数：价目缺失即报错；编码强制单线程确定性档（004 flake 根因消除）。"""
    _require(render, "price_per_second_usd", "editing.render")
    threads = int(_require(render, "encode_threads", "editing.render"))
    if threads != 1:
        raise EditingConfigError(
            f"editing.render.encode_threads 必须为 1（单线程确定性档），实际为 {threads}"
        )
    for key in ("fps", "width", "height"):
        _require(render, key, "editing.render")
    return dict(render)


def _require_shot_limits(limits: dict) -> dict:
    min_shot = int(_require(limits, "min_shot_ms", "editing.shot_limits"))
    max_shot = int(_require(limits, "max_shot_ms", "editing.shot_limits"))
    if not 0 < min_shot < max_shot:
        raise EditingConfigError(
            f"editing.shot_limits 要求 0 < min_shot_ms < max_shot_ms，实际为 {limits!r}"
        )
    return {"min_shot_ms": min_shot, "max_shot_ms": max_shot}


def _require_judge(judge: dict) -> tuple[dict, tuple[EditDecisionList, ...]]:
    """judge 段：提示词非空列表 + 锚点 EDL 集解析为 EditDecisionList（澄清 Q1）。"""
    prompts = _require(judge, "prompts", "editing.judge")
    if (
        not isinstance(prompts, list)
        or not prompts
        or not all(isinstance(p, str) and p for p in prompts)
    ):
        raise EditingConfigError("editing.judge.prompts 必须为非空字符串列表")
    anchors = _require(judge, "anchor_edls", "editing.judge")
    if not isinstance(anchors, list) or not anchors:
        raise EditingConfigError("editing.judge.anchor_edls 必须为非空列表")
    anchor_edls = tuple(EditDecisionList.from_dict(a) for a in anchors)
    return {"prompts": list(prompts)}, anchor_edls


@dataclass(frozen=True)
class EditingConfig:
    """editing 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    exploration_per_round_usd: float
    edits_per_round: int
    target_duration_s: float
    duration_tolerance_s: float
    shot_limits: dict
    transition_rules: dict
    pacing_baseline: dict
    render: dict
    judge: dict
    anchor_edls: tuple[EditDecisionList, ...]
    evaluator_weights: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> "EditingConfig":
        editing = config.get("editing")
        if not isinstance(editing, dict):
            raise EditingConfigError("形态配置缺少 editing 段")
        evaluator_weights = config.get("evaluator_weights", {}).get("editing")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise EditingConfigError("形态配置缺少 evaluator_weights.editing 段")
        judge, anchor_edls = _require_judge(_require(editing, "judge", "editing"))
        return cls(
            exploration_per_round_usd=float(
                _require(editing, "exploration_per_round_usd", "editing")
            ),
            edits_per_round=int(_require(editing, "edits_per_round", "editing")),
            target_duration_s=float(_require(editing, "target_duration_s", "editing")),
            duration_tolerance_s=float(_require(editing, "duration_tolerance_s", "editing")),
            shot_limits=_require_shot_limits(_require(editing, "shot_limits", "editing")),
            transition_rules=_require_transition_rules(
                _require(editing, "transition_rules", "editing")
            ),
            pacing_baseline=_require_pacing_baseline(
                _require(editing, "pacing_baseline", "editing")
            ),
            render=_require_render(_require(editing, "render", "editing")),
            judge=judge,
            anchor_edls=anchor_edls,
            evaluator_weights=evaluator_weights,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EditingConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
