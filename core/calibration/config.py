"""形态配置 calibration 段解析（configs/*.yaml → CalibrationConfig，功能 010）。

- 配置即形态：top_k/最小样本量/偏差阈值/信度目标/λ_ridge/自循环排除全走 configs
  （宪章原则五），core 零硬编码；
- plan 外增补的独立模块：避免 core/evaluators/weights.py 承担非权重配置；
- self_pairing_exclusions 以防自循环配对的显式声明（research 决策 4），
  读出后归一为 {来源: 排除分量元组} 的不可变形态。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.calibration.errors import CalibrationConfigError

_INT_FIELDS = ("period_days", "top_k", "min_samples")


@dataclass(frozen=True)
class CalibrationConfig:
    """calibration 段配置（周期/盲评 top-k/样本量/阈值/信度目标/收缩强度/自循环排除）。"""

    period_days: int
    top_k: int
    min_samples: int
    bias_threshold: float
    reliability_target: float
    ridge_lambda: float
    self_pairing_exclusions: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> "CalibrationConfig":
        if not isinstance(config, dict) or not isinstance(config.get("calibration"), dict):
            raise CalibrationConfigError("形态配置缺少 calibration 段（映射）")
        section = config["calibration"]

        def req(key):
            if key not in section:
                raise CalibrationConfigError(f"calibration 缺少配置项 {key!r}")
            return section[key]

        ints = {}
        for key in _INT_FIELDS:
            value = req(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise CalibrationConfigError(
                    f"calibration.{key} 必须为 ≥ 1 的整数，实际为 {value!r}"
                )
            ints[key] = value

        bias_threshold = req("bias_threshold")
        if (
            not isinstance(bias_threshold, (int, float))
            or isinstance(bias_threshold, bool)
            or bias_threshold < 0
        ):
            raise CalibrationConfigError(
                f"calibration.bias_threshold 必须为 ≥ 0 的数值，实际为 {bias_threshold!r}"
            )

        reliability_target = req("reliability_target")
        if (
            not isinstance(reliability_target, (int, float))
            or isinstance(reliability_target, bool)
            or not 0.0 <= reliability_target <= 1.0
        ):
            raise CalibrationConfigError(
                f"calibration.reliability_target 必须 ∈ [0,1]，实际为 {reliability_target!r}"
            )

        ridge_lambda = req("ridge_lambda")
        if (
            not isinstance(ridge_lambda, (int, float))
            or isinstance(ridge_lambda, bool)
            or ridge_lambda < 0
        ):
            raise CalibrationConfigError(
                f"calibration.ridge_lambda 必须为 ≥ 0 的数值，实际为 {ridge_lambda!r}"
            )

        raw_exclusions = req("self_pairing_exclusions")
        if not isinstance(raw_exclusions, dict):
            raise CalibrationConfigError(
                "calibration.self_pairing_exclusions 必须为映射：锚点来源 → 排除分量列表"
            )
        exclusions: dict[str, tuple[str, ...]] = {}
        for source, components in raw_exclusions.items():
            if (
                not isinstance(source, str)
                or not source
                or not isinstance(components, list)
                or any(not isinstance(c, str) or not c for c in components)
            ):
                raise CalibrationConfigError(
                    "calibration.self_pairing_exclusions 的键必须为非空字符串、"
                    f"值必须为非空字符串列表，实际为 {source!r}: {components!r}"
                )
            exclusions[source] = tuple(components)

        return cls(
            **ints,
            bias_threshold=float(bias_threshold),
            reliability_target=float(reliability_target),
            ridge_lambda=float(ridge_lambda),
            self_pairing_exclusions=exclusions,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CalibrationConfig":
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CalibrationConfigError(f"形态配置文件不可读：{path}（{exc}）") from exc
        return cls.from_dict(data)
