"""漂移检测配置解析（configs/*.yaml → DriftConfig，功能 012 / T1106；功能 020：窗口同量纲）。

- 配置即形态：滑动窗口 N / 分桶数 / 双维阈值（PSI + 分位数位移）/ 样本下限 /
  分级处置系数（suspect 降权、confirmed_drift 排除）/ 检测范围 / 双信号规则
  全走 configs（宪章原则五），core 零硬编码；
- 缺段/缺字段/非法值即报错（CalibrationConfigError）——**不允许静默用默认值**
  （误用默认阈值会让判定口径悄悄变化，违背原则一"口径即版本"）；
- **功能 020 新增 `period_days`**（取自 `calibration.period_days`，缺项即报错）：
  `window` 的**单位与 cadence 同量纲**（日级 = 天、周级 = 周），单位经
  `window_unit` / `thresholds_snapshot()` 写进产物并进制 `detector_version`；
- scope_kinds 为 evaluator_id 前缀（judge/proxy/rule/human）：默认仅 judge 类，
  proxy/rule 需显式纳入（其分布变化更可能是输入分布变化的镜像，噪声大）；
- 独立模块（不并入 010 CalibrationConfig）：010 段缺 drift 段仍可用，
  012 段可独立解析与演进，风格对齐 core/calibration/config.py。
"""

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.calibration.errors import CalibrationConfigError
from core.calibration.periods import CADENCE_UNIT, SUPPORTED_CADENCES

_INT_FIELDS = (("window", 1), ("buckets", 2), ("min_samples", 1))
_POSITIVE_FIELDS = ("psi_threshold", "quantile_threshold")
_DOUBLE_SIGNAL_KEYS = ("enabled", "reliability_target", "base_level", "escalated_level")


def _require_number(name: str, value: object, *, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise CalibrationConfigError(
            f"calibration.drift.{name} 必须 ∈ [{minimum},{maximum}]，实际为 {value!r}"
        )
    return float(value)


@dataclass(frozen=True)
class DoubleSignalRule:
    """双信号强化告警规则：漂移告警 ∧ 信度低于 target → 级别升级 + "双信号"标注。

    仅作用于报表告警级别（US3），不参与漂移阈值判定（避免把语义不同的两个信号
    混进同一条判定口径——判定口径变更必须走新 detector_version）。
    """

    enabled: bool
    reliability_target: float
    base_level: str
    escalated_level: str

    @classmethod
    def from_dict(cls, section: object) -> "DoubleSignalRule":
        if not isinstance(section, dict):
            raise CalibrationConfigError(
                "calibration.drift.double_signal 必须为映射"
                "（enabled/reliability_target/base_level/escalated_level）"
            )
        for key in _DOUBLE_SIGNAL_KEYS:
            if key not in section:
                raise CalibrationConfigError(
                    f"calibration.drift.double_signal.{key} 缺少配置项（规则必须完整声明）"
                )

        enabled = section["enabled"]
        if not isinstance(enabled, bool):
            raise CalibrationConfigError(
                f"calibration.drift.double_signal.enabled 必须为布尔值，实际为 {enabled!r}"
            )
        base_level = section["base_level"]
        escalated_level = section["escalated_level"]
        for name, value in (("base_level", base_level), ("escalated_level", escalated_level)):
            if not isinstance(value, str) or not value:
                raise CalibrationConfigError(
                    f"calibration.drift.double_signal.{name} 必须为非空字符串，实际为 {value!r}"
                )
        if base_level == escalated_level:
            raise CalibrationConfigError(
                "calibration.drift.double_signal 的强化级别必须与常规级别不同"
                f"（双信号必须体现级别升级，实际均为 {base_level!r}）"
            )
        return cls(
            enabled=enabled,
            reliability_target=_require_number(
                "double_signal.reliability_target",
                section["reliability_target"],
                minimum=0.0,
                maximum=1.0,
            ),
            base_level=base_level,
            escalated_level=escalated_level,
        )


@dataclass(frozen=True)
class DriftConfig:
    """calibration.drift 段配置（滑动窗口/分桶/双维阈值/样本下限/分级处置/范围/双信号）。

    `period_days`（功能 020）取自 `calibration.period_days`——**窗口的单位与 cadence 同量纲**
    （日级 `window: 3` = **3 天**、周级 = 3 周），缺项即报错、不取码内默认。
    """

    window: int
    buckets: int
    psi_threshold: float
    quantile_threshold: float
    min_samples: int
    suspect_weight: float
    confirmed_exclude: bool
    scope_kinds: tuple
    double_signal: DoubleSignalRule
    period_days: int

    @property
    def window_unit(self) -> str:
        """窗口单位（由 cadence 派生，取值域 `{1: "day", 7: "week"}`）。"""
        return CADENCE_UNIT[self.period_days]

    @classmethod
    def from_dict(cls, config: dict) -> "DriftConfig":
        calibration = config.get("calibration") if isinstance(config, dict) else None
        if not isinstance(calibration, dict):
            raise CalibrationConfigError("形态配置缺少 calibration 段（映射）")
        if not isinstance(calibration.get("drift"), dict):
            raise CalibrationConfigError("形态配置缺少 calibration.drift 段（映射）")
        section = calibration["drift"]

        # 量纲**必需读取**（取自 calibration.period_days；缺项即报错、不取码内默认）
        period_days = calibration.get("period_days")
        if (
            not isinstance(period_days, int)
            or isinstance(period_days, bool)
            or period_days not in SUPPORTED_CADENCES
        ):
            raise CalibrationConfigError(
                "calibration.period_days 缺失或取值域外"
                f"（漂移窗口单位与 cadence 同量纲，取值域 {SUPPORTED_CADENCES}），"
                f"实际为 {period_days!r}"
            )

        def req(key):
            if key not in section:
                raise CalibrationConfigError(f"calibration.drift 缺少配置项 {key!r}")
            return section[key]

        ints = {}
        for key, minimum in _INT_FIELDS:
            value = req(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise CalibrationConfigError(
                    f"calibration.drift.{key} 必须为 ≥ {minimum} 的整数，实际为 {value!r}"
                )
            ints[key] = value

        thresholds = {}
        for key in _POSITIVE_FIELDS:
            value = req(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise CalibrationConfigError(
                    f"calibration.drift.{key} 必须为 > 0 的数值，实际为 {value!r}"
                )
            thresholds[key] = float(value)

        suspect_weight = _require_number(
            "suspect_weight", req("suspect_weight"), minimum=0.0, maximum=1.0
        )

        confirmed_exclude = req("confirmed_exclude")
        if not isinstance(confirmed_exclude, bool):
            raise CalibrationConfigError(
                f"calibration.drift.confirmed_exclude 必须为布尔值，实际为 {confirmed_exclude!r}"
            )

        raw_kinds = req("scope_kinds")
        if (
            not isinstance(raw_kinds, list)
            or not raw_kinds
            or any(not isinstance(kind, str) or not kind for kind in raw_kinds)
        ):
            raise CalibrationConfigError(
                "calibration.drift.scope_kinds 必须为非空的非空字符串列表"
                f"（evaluator_id 前缀，如 [judge]），实际为 {raw_kinds!r}"
            )

        return cls(
            **ints,
            **thresholds,
            suspect_weight=suspect_weight,
            confirmed_exclude=confirmed_exclude,
            scope_kinds=tuple(raw_kinds),
            double_signal=DoubleSignalRule.from_dict(req("double_signal")),
            period_days=period_days,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DriftConfig":
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CalibrationConfigError(f"形态配置文件不可读：{path}（{exc}）") from exc
        return cls.from_dict(data)

    def thresholds_snapshot(self) -> dict:
        """判定阈值快照（进检测记录 thresholds，供报表与审计自描述口径）。

        `window_unit` / `period_days`（功能 020）使**窗口单位与 cadence 同量纲**可见：
        `window: 3` 在日级形态下是 3 天、周级形态下是 3 周——单位不写清则读者无法判断。
        """
        return {
            "psi": self.psi_threshold,
            "quantile": self.quantile_threshold,
            "min_samples": self.min_samples,
            "window": self.window,
            "window_unit": self.window_unit,
            "period_days": self.period_days,
        }
