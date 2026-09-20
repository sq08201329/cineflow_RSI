"""分镜形态配置（configs/*.yaml 的 storyboard 段 → StoryboardConfig）。

配置即形态（宪章原则五）：探索预算、单轮分镜组数、镜头语法规则库（执行前校验
第③层与 rule.shot_grammar 门禁共用单一事实源）、轴规则（机位侧别机检口径）、
情绪基调向量表（proxy.emotion_alignment 对照分镜卡帧像素特征）、渲染价目与编码
参数、judge 提示词与锚点 ShotList 集全部来自配置。

纪律：规则库缺失即报错（不允许静默放过门禁）、价目缺失/为零即报错（不允许静默
零成本，原则三）、编码强制单线程确定性档（SC-002）、情绪向量表维度与取值域校验、
judge 锚点集解析为 ShotList（与候选摘要做成对比较）。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agents.storyboard.shotlist import ShotList


class StoryboardConfigError(Exception):
    """分镜配置缺失/非法。"""


def _require(mapping: dict, key: str, where: str):
    value = mapping.get(key)
    if value is None:
        raise StoryboardConfigError(f"{where} 缺少配置项 {key!r}")
    return value


def _require_str_list(value, where: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise StoryboardConfigError(f"{where} 必须为非空字符串列表，实际为 {value!r}")
    for item in value:
        if not isinstance(item, str) or not item:
            raise StoryboardConfigError(f"{where} 必须为非空字符串列表，实际为 {value!r}")
    return list(value)


def _require_int(value, where: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise StoryboardConfigError(f"{where} 必须为 ≥{minimum} 的整数，实际为 {value!r}")
    return value


def _require_shot_grammar(grammar: dict) -> dict:
    """镜头语法规则库：景别档位枚举与序 + 跳跃/同景别连续上限 + 机位/运动档位。

    缺任一字段即报错——不允许静默放过门禁（执行前校验第③层与 rule.shot_grammar
    共用本规则库）。
    """
    if not isinstance(grammar, dict):
        raise StoryboardConfigError(f"storyboard.shot_grammar 必须为 dict，实际为 {grammar!r}")
    shot_sizes = _require_str_list(
        _require(grammar, "shot_sizes", "storyboard.shot_grammar"),
        "storyboard.shot_grammar.shot_sizes",
    )
    if len(set(shot_sizes)) != len(shot_sizes):
        raise StoryboardConfigError(
            "storyboard.shot_grammar.shot_sizes 档位必须唯一（有序枚举，跳跃上限按序号差判定）"
        )
    max_size_jump = _require_int(
        _require(grammar, "max_size_jump", "storyboard.shot_grammar"),
        "storyboard.shot_grammar.max_size_jump",
    )
    max_same_size_run = _require_int(
        _require(grammar, "max_same_size_run", "storyboard.shot_grammar"),
        "storyboard.shot_grammar.max_same_size_run",
        minimum=1,
    )
    camera_positions = _require_str_list(
        _require(grammar, "camera_positions", "storyboard.shot_grammar"),
        "storyboard.shot_grammar.camera_positions",
    )
    if "side" not in camera_positions:
        raise StoryboardConfigError(
            "storyboard.shot_grammar.camera_positions 必须含 'side' 侧机位"
            "（轴规则（180° 线）机检依赖侧别档位）"
        )
    movements = _require_str_list(
        _require(grammar, "movements", "storyboard.shot_grammar"),
        "storyboard.shot_grammar.movements",
    )
    return {
        "shot_sizes": shot_sizes,
        "max_size_jump": max_size_jump,
        "max_same_size_run": max_same_size_run,
        "camera_positions": camera_positions,
        "movements": movements,
    }


def _require_axis_rules(rules: dict) -> dict:
    """轴规则：侧别跳变需过渡镜头（require_transition_on_cross + 过渡额度 + 侧别字段）。"""
    if not isinstance(rules, dict):
        raise StoryboardConfigError(f"storyboard.axis_rules 必须为 dict，实际为 {rules!r}")
    require_transition = _require(rules, "require_transition_on_cross", "storyboard.axis_rules")
    if not isinstance(require_transition, bool):
        raise StoryboardConfigError(
            f"storyboard.axis_rules.require_transition_on_cross 必须为布尔，实际为 "
            f"{require_transition!r}"
        )
    allowed = _require_int(
        _require(rules, "allowed_transition_shots", "storyboard.axis_rules"),
        "storyboard.axis_rules.allowed_transition_shots",
    )
    side_field = _require(rules, "side_field", "storyboard.axis_rules")
    if not isinstance(side_field, str) or not side_field:
        raise StoryboardConfigError(
            f"storyboard.axis_rules.side_field 必须为非空字符串，实际为 {side_field!r}"
        )
    return {
        "require_transition_on_cross": require_transition,
        "allowed_transition_shots": allowed,
        "side_field": side_field,
    }


def _require_emotion_vectors(vectors: dict) -> dict:
    """情绪基调向量表：非空；每项 3 维归一化 RGB ∈ [0, 1]（色板与余弦对照口径）。"""
    if not isinstance(vectors, dict) or not vectors:
        raise StoryboardConfigError(
            f"storyboard.emotion_vectors 必须为非空字典，实际为 {vectors!r}"
        )
    normalized: dict[str, list[float]] = {}
    for name, vector in vectors.items():
        if not isinstance(name, str) or not name:
            raise StoryboardConfigError(f"storyboard.emotion_vectors 键必须为非空字符串：{name!r}")
        if not isinstance(vector, (list, tuple)) or len(vector) != 3:
            raise StoryboardConfigError(
                f"storyboard.emotion_vectors.{name} 必须为 3 维 RGB 向量，实际为 {vector!r}"
            )
        for component in vector:
            if not isinstance(component, (int, float)) or isinstance(component, bool):
                raise StoryboardConfigError(
                    f"storyboard.emotion_vectors.{name} 分量必须为数值，实际为 {component!r}"
                )
            if not 0.0 <= float(component) <= 1.0:
                raise StoryboardConfigError(
                    f"storyboard.emotion_vectors.{name} 分量必须 ∈ [0, 1]，实际为 {component!r}"
                )
        normalized[name] = [float(component) for component in vector]
    return normalized


def _require_render(render: dict) -> dict:
    """渲染参数：价目缺失/为零即报错；编码强制单线程确定性档（SC-002）。"""
    if not isinstance(render, dict):
        raise StoryboardConfigError(f"storyboard.render 必须为 dict，实际为 {render!r}")
    price = _require(render, "price_per_shot_usd", "storyboard.render")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or float(price) <= 0:
        raise StoryboardConfigError(
            f"storyboard.render.price_per_shot_usd 必须为正数，实际为 {price!r}"
            "（缺价目即报错，不允许静默零成本）"
        )
    threads = _require_int(
        _require(render, "encode_threads", "storyboard.render"),
        "storyboard.render.encode_threads",
        minimum=1,
    )
    if threads != 1:
        raise StoryboardConfigError(
            f"storyboard.render.encode_threads 必须为 1（单线程确定性档），实际为 {threads}"
        )
    for key in ("fps", "width", "height"):
        _require_int(
            _require(render, key, "storyboard.render"), f"storyboard.render.{key}", minimum=1
        )
    return dict(render)


def _require_judge(judge: dict) -> tuple[dict, tuple[ShotList, ...]]:
    """judge 段：提示词非空列表 + 锚点 ShotList 集解析为 ShotList（C8 版号三段之一）。"""
    if not isinstance(judge, dict):
        raise StoryboardConfigError(f"storyboard.judge 必须为 dict，实际为 {judge!r}")
    prompts = _require(judge, "prompts", "storyboard.judge")
    prompts = _require_str_list(prompts, "storyboard.judge.prompts")
    anchors = _require(judge, "anchor_shotlists", "storyboard.judge")
    if not isinstance(anchors, list) or not anchors:
        raise StoryboardConfigError("storyboard.judge.anchor_shotlists 必须为非空列表")
    anchor_shotlists = tuple(ShotList.from_dict(anchor) for anchor in anchors)
    return {"prompts": prompts}, anchor_shotlists


@dataclass(frozen=True)
class StoryboardConfig:
    """storyboard 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    exploration_per_round_usd: float
    boards_per_round: int
    shot_grammar: dict
    axis_rules: dict
    emotion_vectors: dict
    render: dict
    judge: dict
    anchor_shotlists: tuple[ShotList, ...]
    evaluator_weights: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> "StoryboardConfig":
        if not isinstance(config, dict):
            raise StoryboardConfigError(f"形态配置必须为 dict，实际为 {config!r}")
        storyboard = config.get("storyboard")
        if not isinstance(storyboard, dict):
            raise StoryboardConfigError("形态配置缺少 storyboard 段")
        evaluator_weights = config.get("evaluator_weights", {}).get("storyboard")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise StoryboardConfigError("形态配置缺少 evaluator_weights.storyboard 段")
        judge, anchor_shotlists = _require_judge(_require(storyboard, "judge", "storyboard"))
        return cls(
            exploration_per_round_usd=float(
                _require(storyboard, "exploration_per_round_usd", "storyboard")
            ),
            boards_per_round=int(_require(storyboard, "boards_per_round", "storyboard")),
            shot_grammar=_require_shot_grammar(_require(storyboard, "shot_grammar", "storyboard")),
            axis_rules=_require_axis_rules(_require(storyboard, "axis_rules", "storyboard")),
            emotion_vectors=_require_emotion_vectors(
                _require(storyboard, "emotion_vectors", "storyboard")
            ),
            render=_require_render(_require(storyboard, "render", "storyboard")),
            judge=judge,
            anchor_shotlists=anchor_shotlists,
            evaluator_weights=evaluator_weights,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StoryboardConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
