"""开发（选题 / IP 评估 / 立项组合）形态配置（configs/*.yaml 的 dev 段 → DevConfig）。

配置即形态（宪章原则五）：立项组合的条目数区间、进入生产标记数区间、组合约束、回放对比
最小可比对树数、模拟数据源参数、升级判据阈值全部来自配置。

纪律：缺任一必需项即报错（不允许静默取码内默认——那会让"形态可配置"变成空话）；
升级判据阈值**必须全量声明**（缺失即报错），无数据来源的项由判据材料标"无法评价（来源缺失）"
（原则六）；模拟数据源参数是行为口径，变更即新评估器版本（原则一）。
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

# 升级判据阈值：全量声明（缺任一项即报错）——无来源者由判据材料标"无法评价（来源缺失）"
THRESHOLD_KEYS = ("correlation_target", "min_samples", "drift_band", "gate_violation_max")

# 模拟数据源必需参数（`fixtures` 为可选：夹具/演示用于钉住取值）
SIGNAL_KEYS = ("baseline_usd_million", "sensitivity", "buzz_baseline")


class DevConfigError(Exception):
    """开发形态配置缺失/非法。"""


def _require(mapping: dict, key: str, where: str):
    value = mapping.get(key)
    if value is None:
        raise DevConfigError(f"{where} 缺少配置项 {key!r}")
    return value


def _require_section(value, where: str) -> dict:
    if not isinstance(value, dict) or not value:
        raise DevConfigError(f"{where} 必须为非空 mapping，实际为 {value!r}")
    return dict(value)


def _require_int(value, where: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise DevConfigError(f"{where} 必须为 ≥{minimum} 的整数，实际为 {value!r}")
    return value


def _require_number(value, where: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DevConfigError(f"{where} 必须为数值，实际为 {value!r}")
    number = float(value)
    if not minimum <= number <= maximum:
        raise DevConfigError(f"{where} 必须落在 [{minimum}, {maximum}]，实际为 {number!r}")
    return number


def _require_interval(section: dict, where: str, *, minimum_lower: int = 0) -> tuple[int, int]:
    """区间项：`{min, max}` 两键必需；min ≤ max（下界可另行要求 ≥ minimum_lower）。"""
    section = _require_section(section, where)
    lower = _require_int(_require(section, "min", where), f"{where}.min", minimum=minimum_lower)
    upper = _require_int(_require(section, "max", where), f"{where}.max", minimum=minimum_lower)
    if lower > upper:
        raise DevConfigError(f"{where} 的 min({lower}) 不得大于 max({upper})")
    return lower, upper


def _require_signals(section: dict) -> dict:
    section = _require_section(section, "dev.signals")
    signals: dict = {
        "baseline_usd_million": _require_number(
            _require(section, "baseline_usd_million", "dev.signals"),
            "dev.signals.baseline_usd_million",
            minimum=0.0,
            maximum=1e9,
        ),
        "sensitivity": _require_number(
            _require(section, "sensitivity", "dev.signals"),
            "dev.signals.sensitivity",
            minimum=0.0,
            maximum=100.0,
        ),
        "buzz_baseline": _require_number(
            _require(section, "buzz_baseline", "dev.signals"),
            "dev.signals.buzz_baseline",
            minimum=0.0,
            maximum=1.0,
        ),
    }
    fixtures = section.get("fixtures")
    if fixtures is not None:
        signals["fixtures"] = _require_section(fixtures, "dev.signals.fixtures")
    return signals


def _require_thresholds(section: dict) -> dict:
    """升级判据阈值：全量声明——缺任一项即报错（不留"无判据"的口子）。"""
    section = _require_section(section, "dev.upgrade_criteria")
    for key in THRESHOLD_KEYS:
        if key not in section:
            raise DevConfigError(f"dev.upgrade_criteria 缺少配置项 {key!r}")
    return {
        "correlation_target": _require_number(
            section["correlation_target"], "dev.upgrade_criteria.correlation_target",
            minimum=0.0, maximum=1.0,
        ),
        "min_samples": _require_int(
            section["min_samples"], "dev.upgrade_criteria.min_samples", minimum=1
        ),
        "drift_band": _require_number(
            section["drift_band"], "dev.upgrade_criteria.drift_band", minimum=0.0, maximum=1.0
        ),
        "gate_violation_max": _require_number(
            section["gate_violation_max"], "dev.upgrade_criteria.gate_violation_max",
            minimum=0.0, maximum=1.0,
        ),
    }


@dataclass(frozen=True)
class DevConfig:
    """dev 段配置（冻结快照随轮次树 config_snapshot 落盘）。"""

    slate_entries: tuple[int, int]
    production_marks: tuple[int, int]
    max_direction_repeat_rate: float
    min_comparable_trees: int
    signals: dict
    upgrade_criteria: dict
    evaluator_weights: dict

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DevConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DevConfigError(f"形态配置根必须为 mapping：{path}")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, config: dict) -> "DevConfig":
        if not isinstance(config, dict):
            raise DevConfigError(f"形态配置必须为 dict，实际为 {config!r}")
        if not isinstance(config.get("dev"), dict):
            raise DevConfigError("形态配置缺少 dev 段")
        dev = dict(config["dev"])
        evaluator_weights = config.get("evaluator_weights", {}).get("dev")
        if not isinstance(evaluator_weights, dict) or not evaluator_weights:
            raise DevConfigError("形态配置缺少 evaluator_weights.dev 段")
        return cls(
            slate_entries=_require_interval(
                _require(dev, "slate", "dev"), "dev.slate", minimum_lower=1
            ),
            production_marks=_require_interval(
                _require(dev, "production_marks", "dev"), "dev.production_marks"
            ),
            max_direction_repeat_rate=_require_number(
                _require(
                    _require_section(_require(dev, "combination", "dev"), "dev.combination"),
                    "max_direction_repeat_rate",
                    "dev.combination",
                ),
                "dev.combination.max_direction_repeat_rate",
                minimum=0.0,
                maximum=1.0,
            ),
            min_comparable_trees=_require_int(
                _require(dev, "min_comparable_trees", "dev"),
                "dev.min_comparable_trees",
                minimum=1,
            ),
            signals=_require_signals(_require(dev, "signals", "dev")),
            upgrade_criteria=_require_thresholds(_require(dev, "upgrade_criteria", "dev")),
            evaluator_weights=dict(evaluator_weights),
        )
