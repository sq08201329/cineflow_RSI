"""剧本形态配置（configs/*.yaml 的 screenplay 段 → ScreenplayConfig）。

配置即形态（宪章原则五）：目标时长与页数容差、行-页换算口径、对白行占比区间、节拍表
（rule.beat_structure 门禁与执行前校验共用单一事实源）、角色别名表（proxy.entity_
consistency 的规范化依据）、升级判据阈值（澄清 Q1：阈值配置化 + 系统自动判定）、
生成与 judge 模型价目、judge 提示词与冻结锚点大纲集（三段哈希版本号之一）全部来自配置。

纪律：节拍表/别名表/判据阈值缺失即报错（不允许静默放过门禁或"无判据"）、价目缺失或
为零即报错（不允许静默零成本，原则三）、模型必须有名有价、judge 锚点集必须是大纲
（outline）阶段工件（judge 仅作用于大纲阶段，research 决策 2）。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agents.screenplay.artifact import ScriptArtifact

# 节拍表 act 归属枚举（三幕结构；节拍归属越界即配置非法）
ACTS = ("act1", "act2", "act3")


class ScreenplayConfigError(Exception):
    """剧本配置缺失/非法。"""


def _require(mapping: dict, key: str, where: str):
    if not isinstance(mapping, dict):
        raise ScreenplayConfigError(f"{where} 必须为 dict，实际为 {mapping!r}")
    value = mapping.get(key)
    if value is None:
        raise ScreenplayConfigError(f"{where} 缺少配置项 {key!r}")
    return value


def _require_str(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScreenplayConfigError(f"{where} 必须为非空字符串，实际为 {value!r}")
    return value


def _require_str_list(value, where: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ScreenplayConfigError(f"{where} 必须为非空字符串列表，实际为 {value!r}")
    for item in value:
        if not isinstance(item, str) or not item:
            raise ScreenplayConfigError(f"{where} 必须为非空字符串列表，实际为 {value!r}")
    return list(value)


def _require_int(value, where: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ScreenplayConfigError(f"{where} 必须为 ≥{minimum} 的整数，实际为 {value!r}")
    return value


def _require_number(value, where: str, *, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not minimum <= float(value) <= maximum
    ):
        raise ScreenplayConfigError(
            f"{where} 必须为 [{minimum}, {maximum}] 内数值，实际为 {value!r}"
        )
    return float(value)


def _require_ratio_range(ratio: dict) -> dict:
    """对白行占比区间：0 ≤ min < max ≤ 1（越界或次序倒置即报错）。"""
    if not isinstance(ratio, dict):
        raise ScreenplayConfigError(
            f"screenplay.dialogue_action_ratio 必须为 dict，实际为 {ratio!r}"
        )
    low = _require_number(
        _require(ratio, "min", "screenplay.dialogue_action_ratio"),
        "screenplay.dialogue_action_ratio.min",
        minimum=0.0,
        maximum=1.0,
    )
    high = _require_number(
        _require(ratio, "max", "screenplay.dialogue_action_ratio"),
        "screenplay.dialogue_action_ratio.max",
        minimum=0.0,
        maximum=1.0,
    )
    if low >= high:
        raise ScreenplayConfigError(
            f"screenplay.dialogue_action_ratio.min 必须严格小于 max，实际为 "
            f"min={low!r}, max={high!r}"
        )
    return {"min": low, "max": high}


def _require_beat_sheet(beats) -> tuple[dict, ...]:
    """节拍表：beat_id 唯一 + act ∈ 三幕 + required 显式布尔 + 至少一个关键节拍。

    缺表 / 缺字段 / 全 optional 即报错——门禁的存在性判定必须有机检依据（原则五）。
    """
    if not isinstance(beats, list) or not beats:
        raise ScreenplayConfigError(f"screenplay.beat_sheet 必须为非空列表，实际为 {beats!r}")
    normalized: list[dict] = []
    seen: set[str] = set()
    for index, beat in enumerate(beats):
        where = f"screenplay.beat_sheet[{index}]"
        if not isinstance(beat, dict):
            raise ScreenplayConfigError(f"{where} 必须为 dict，实际为 {beat!r}")
        beat_id = _require_str(_require(beat, "beat_id", where), f"{where}.beat_id")
        if beat_id in seen:
            raise ScreenplayConfigError(f"screenplay.beat_sheet.beat_id 重复：{beat_id!r}")
        seen.add(beat_id)
        act = _require_str(_require(beat, "act", where), f"{where}.act")
        if act not in ACTS:
            raise ScreenplayConfigError(
                f"{where}.act 必须 ∈ {list(ACTS)}（三幕结构），实际为 {act!r}"
            )
        required = _require(beat, "required", where)
        if not isinstance(required, bool):
            raise ScreenplayConfigError(f"{where}.required 必须为布尔，实际为 {required!r}")
        description = _require_str(_require(beat, "description", where), f"{where}.description")
        normalized.append(
            {
                "beat_id": beat_id,
                "act": act,
                "required": required,
                "description": description,
            }
        )
    if not any(beat["required"] for beat in normalized):
        raise ScreenplayConfigError(
            "screenplay.beat_sheet 必须至少含一个 required=true 的关键节拍"
            "（全 optional 等于门禁恒过）"
        )
    return tuple(normalized)


def _require_character_aliases(aliases) -> dict[str, tuple[str, ...]]:
    """角色别名表：非空；同一写法（含规范名）不得归属两个角色（规范化禁多义）。"""
    if not isinstance(aliases, dict) or not aliases:
        raise ScreenplayConfigError(
            f"screenplay.character_aliases 必须为非空字典，实际为 {aliases!r}"
        )
    normalized: dict[str, tuple[str, ...]] = {}
    owner: dict[str, str] = {}
    for name, alias_list in aliases.items():
        _require_str(name, "screenplay.character_aliases 键（角色规范名）")
        if not isinstance(alias_list, list):
            raise ScreenplayConfigError(
                f"screenplay.character_aliases.{name} 必须为列表（可为空），实际为 {alias_list!r}"
            )
        for alias in alias_list:
            _require_str(alias, f"screenplay.character_aliases.{name}[]")
        for spelling in (name, *alias_list):
            if spelling in owner and owner[spelling] != name:
                raise ScreenplayConfigError(
                    f"角色写法 {spelling!r} 同时归属 {owner[spelling]!r} 与 {name!r}"
                    "（同一写法不得多义）"
                )
            owner[spelling] = name
        normalized[name] = tuple(alias_list)
    return normalized


def _require_upgrade_criteria(criteria: dict) -> dict:
    """升级判据阈值：四项齐全即自动判定口径成立；缺任一项即报错（不允许静默"无判据"）。"""
    if not isinstance(criteria, dict):
        raise ScreenplayConfigError(f"screenplay.upgrade_criteria 必须为 dict，实际为 {criteria!r}")
    return {
        "judge_r_target": _require_number(
            _require(criteria, "judge_r_target", "screenplay.upgrade_criteria"),
            "screenplay.upgrade_criteria.judge_r_target",
            minimum=1e-9,
            maximum=1.0,
        ),
        "min_samples": _require_int(
            _require(criteria, "min_samples", "screenplay.upgrade_criteria"),
            "screenplay.upgrade_criteria.min_samples",
            minimum=1,
        ),
        "drift_band": _require_number(
            _require(criteria, "drift_band", "screenplay.upgrade_criteria"),
            "screenplay.upgrade_criteria.drift_band",
            minimum=0.0,
            maximum=float("inf"),
        ),
        "gate_violation_max": _require_number(
            _require(criteria, "gate_violation_max", "screenplay.upgrade_criteria"),
            "screenplay.upgrade_criteria.gate_violation_max",
            minimum=0.0,
            maximum=1.0,
        ),
    }


def _require_model_prices(prices: dict) -> dict:
    """模型价目表：每模型双单价 > 0（缺价目/零价目即报错，不允许静默零成本）。"""
    if not isinstance(prices, dict) or not prices:
        raise ScreenplayConfigError(f"screenplay.model_prices 必须为非空字典，实际为 {prices!r}")
    normalized: dict[str, dict[str, float]] = {}
    for name, price in prices.items():
        _require_str(name, "screenplay.model_prices 键（模型名）")
        where = f"screenplay.model_prices.{name}"
        if not isinstance(price, dict):
            raise ScreenplayConfigError(f"{where} 必须为 dict，实际为 {price!r}")
        normalized[name] = {
            key: _require_number(
                _require(price, key, where), f"{where}.{key}", minimum=1e-12, maximum=float("inf")
            )
            for key in ("prompt_per_1k", "completion_per_1k")
        }
    return normalized


def _require_judge(judge: dict, model_prices: dict) -> tuple[dict, tuple[ScriptArtifact, ...]]:
    """judge 段：模型名（须在价目表内）+ 提示词 + 锚点大纲集（仅 outline 阶段工件）。"""
    if not isinstance(judge, dict):
        raise ScreenplayConfigError(f"screenplay.judge 必须为 dict，实际为 {judge!r}")
    model = _require_str(_require(judge, "model", "screenplay.judge"), "screenplay.judge.model")
    if model not in model_prices:
        raise ScreenplayConfigError(
            f"screenplay.judge.model {model!r} 不在 model_prices 内 "
            f"{sorted(model_prices)}（缺价目即报错，不允许静默零成本）"
        )
    prompts = _require_str_list(
        _require(judge, "prompts", "screenplay.judge"), "screenplay.judge.prompts"
    )
    anchors = _require(judge, "anchor_outlines", "screenplay.judge")
    if not isinstance(anchors, list) or not anchors:
        raise ScreenplayConfigError("screenplay.judge.anchor_outlines 必须为非空列表")
    anchor_outlines = tuple(ScriptArtifact.from_dict(anchor) for anchor in anchors)
    for anchor in anchor_outlines:
        if anchor.stage != "outline":
            raise ScreenplayConfigError(
                f"screenplay.judge.anchor_outlines 必须为 outline 阶段工件，"
                f"实际为 {anchor.stage!r}（judge 仅作用于大纲阶段）"
            )
    return {"model": model, "prompts": prompts}, anchor_outlines


@dataclass(frozen=True)
class ScreenplayConfig:
    """screenplay 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    target_duration_min: int
    page_tolerance: int
    lines_per_page: int
    dialogue_action_ratio: dict
    beat_sheet: tuple[dict, ...]
    character_aliases: dict[str, tuple[str, ...]]
    upgrade_criteria: dict
    model: str
    model_prices: dict
    judge: dict
    anchor_outlines: tuple[ScriptArtifact, ...]
    evaluator_weights: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> "ScreenplayConfig":
        if not isinstance(config, dict):
            raise ScreenplayConfigError(f"形态配置必须为 dict，实际为 {config!r}")
        screenplay = config.get("screenplay")
        if not isinstance(screenplay, dict):
            raise ScreenplayConfigError("形态配置缺少 screenplay 段")
        evaluator_weights = config.get("evaluator_weights", {}).get("screenplay")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise ScreenplayConfigError("形态配置缺少 evaluator_weights.screenplay 段")
        model_prices = _require_model_prices(_require(screenplay, "model_prices", "screenplay"))
        model = _require_str(_require(screenplay, "model", "screenplay"), "screenplay.model")
        if model not in model_prices:
            raise ScreenplayConfigError(
                f"screenplay.model {model!r} 不在 model_prices 内 {sorted(model_prices)}"
                "（缺价目即报错，不允许静默零成本）"
            )
        judge, anchor_outlines = _require_judge(
            _require(screenplay, "judge", "screenplay"), model_prices
        )
        return cls(
            target_duration_min=_require_int(
                _require(screenplay, "target_duration_min", "screenplay"),
                "screenplay.target_duration_min",
                minimum=1,
            ),
            page_tolerance=_require_int(
                _require(screenplay, "page_tolerance", "screenplay"),
                "screenplay.page_tolerance",
            ),
            lines_per_page=_require_int(
                _require(screenplay, "lines_per_page", "screenplay"),
                "screenplay.lines_per_page",
                minimum=1,
            ),
            dialogue_action_ratio=_require_ratio_range(
                _require(screenplay, "dialogue_action_ratio", "screenplay")
            ),
            beat_sheet=_require_beat_sheet(_require(screenplay, "beat_sheet", "screenplay")),
            character_aliases=_require_character_aliases(
                _require(screenplay, "character_aliases", "screenplay")
            ),
            upgrade_criteria=_require_upgrade_criteria(
                _require(screenplay, "upgrade_criteria", "screenplay")
            ),
            model=model,
            model_prices=model_prices,
            judge=judge,
            anchor_outlines=anchor_outlines,
            evaluator_weights=evaluator_weights,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ScreenplayConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    # --- 节拍表访问 ---
    def beat_ids(self) -> tuple[str, ...]:
        return tuple(beat["beat_id"] for beat in self.beat_sheet)

    def required_beat_ids(self) -> tuple[str, ...]:
        """关键节拍清单（rule.beat_structure 的存在性判定依据）。"""
        return tuple(beat["beat_id"] for beat in self.beat_sheet if beat["required"])

    # --- 角色别名表访问（实体一致性规范化口径） ---
    def registered_names(self) -> frozenset[str]:
        return frozenset(
            spelling
            for name, aliases in self.character_aliases.items()
            for spelling in (name, *aliases)
        )

    def canonical_of(self, name: str) -> str | None:
        """写法 → 规范名（未登记返回 None，如实不猜）。"""
        for canonical, aliases in self.character_aliases.items():
            if name == canonical or name in aliases:
                return canonical
        return None

    def is_registered(self, name: str) -> bool:
        return self.canonical_of(name) is not None

    def price_of(self, model: str) -> dict:
        if model not in self.model_prices:
            raise ScreenplayConfigError(f"模型 {model!r} 不在价目表内 {sorted(self.model_prices)}")
        return self.model_prices[model]
