"""形态配置 calibration 段解析（configs/*.yaml → CalibrationConfig，功能 010）。

- 配置即形态：top_k/最小样本量/偏差阈值/信度目标/λ_ridge/自循环排除全走 configs
  （宪章原则五），core 零硬编码；
- plan 外增补的独立模块：避免 core/evaluators/weights.py 承担非权重配置；
- self_pairing_exclusions 以防自循环配对的显式声明（research 决策 4），
  读出后归一为 {来源: 排除分量元组} 的不可变形态。

功能 020 新增两件**必需读取**（缺项即报错、不取码内默认，FR-014）：

- `calibration.window_semantics` / `window_semantics_change_date`（半开窗口口径与生效日）；
- `calibration.transfer.{basis, source_forms, target_forms, conditions, storage, adoption}`
  （校准结论迁移的可比性条件配置化，契约 C15）——`conditions` 的**键集 = 判定项清单**，
  键必须 ∈ `TRANSFER_CONDITION_IDS`（判定实现单点在 `core/calibration/transfer.py`）。

功能 021（C11 / T2149）在本加载器**收口 cadence 取值域**：`calibration.period_days` 必须 ∈
`core/calibration/periods.py` 的 `SUPPORTED_CADENCES`（`(1, 7)`），越界即**显式报错并点名取值域**；
`periods.py` **一字不改**，也不发明第三档量纲（"双周/月"须另立特性）。
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from core.calibration.errors import CalibrationConfigError
from core.calibration.periods import SUPPORTED_CADENCES, WINDOW_SEMANTICS

_INT_FIELDS = ("period_days", "top_k", "min_samples")

#: 迁移口径取值域（单元素：只迁结论，不迁权重）
TRANSFER_BASIS = "conclusion_only"

#: 采纳方式取值域（单元素：人工两键，无自动采纳路径）
TRANSFER_ADOPTION = "manual"

#: 可比性条件 id 取值域（**键集 = 判定项清单**；实现单点在 `core/calibration/transfer.py`）。
#: 五类覆盖：评估器登记（版本冻结）/ 样本量下限 / 判定口径哈希 / 周期量纲明确 / 真实来源。
TRANSFER_CONDITION_IDS = (
    "evaluator_registered",
    "min_samples",
    "detector_version_match",
    "cadence_conversion",
    "real_coverage_days",
    "reliability_floor",
    "max_abs_mean_shift",
    "require_drift_pass",
)


@dataclass(frozen=True)
class TransferConfig:
    """`calibration.transfer` 段配置（迁移口径 + 可比性条件 + 存储与采纳方式）。"""

    basis: str
    source_forms: tuple[str, ...]
    target_forms: tuple[str, ...]
    conditions: dict
    storage_dir: str
    adoption: str

    @classmethod
    def from_dict(cls, section: dict) -> "TransferConfig":
        if not isinstance(section, dict) or not isinstance(section.get("transfer"), dict):
            raise CalibrationConfigError("calibration 缺少配置项 'transfer'（映射）")
        transfer = section["transfer"]

        def req(key):
            if key not in transfer:
                raise CalibrationConfigError(f"calibration.transfer 缺少配置项 {key!r}")
            return transfer[key]

        basis = req("basis")
        if basis != TRANSFER_BASIS:
            raise CalibrationConfigError(
                "calibration.transfer.basis 取值域单元素"
                f"（唯一取值 {TRANSFER_BASIS!r}：只迁结论、不迁权重），实际为 {basis!r}"
            )
        adoption = req("adoption")
        if adoption != TRANSFER_ADOPTION:
            raise CalibrationConfigError(
                "calibration.transfer.adoption 取值域单元素"
                f"（唯一取值 {TRANSFER_ADOPTION!r}：人工两键，无自动采纳路径），实际为 {adoption!r}"
            )
        forms = {}
        for key in ("source_forms", "target_forms"):
            raw = req(key)
            if (
                not isinstance(raw, list)
                or not raw
                or any(not isinstance(item, str) or not item for item in raw)
            ):
                raise CalibrationConfigError(
                    f"calibration.transfer.{key} 必须为非空字符串列表，实际为 {raw!r}"
                )
            forms[key] = tuple(raw)

        conditions = req("conditions")
        if not isinstance(conditions, dict) or not conditions:
            raise CalibrationConfigError(
                "calibration.transfer.conditions 必须为非空映射"
                "（键集 = 判定项清单，缺项即报错、不取码内默认）"
            )
        unknown = [key for key in conditions if key not in TRANSFER_CONDITION_IDS]
        if unknown:
            raise CalibrationConfigError(
                f"calibration.transfer.conditions 含未实现的判定项 {sorted(unknown)}"
                f"（取值域 {TRANSFER_CONDITION_IDS}）"
            )
        for key, value in conditions.items():
            if value is None or isinstance(value, (dict, list)):
                raise CalibrationConfigError(
                    f"calibration.transfer.conditions.{key} 必须为标量（数值/布尔/字符串），"
                    f"实际为 {value!r}"
                )

        storage = req("storage")
        if not isinstance(storage, dict) or "dir" not in storage:
            raise CalibrationConfigError(
                "calibration.transfer.storage 必须为映射且带 dir（append-only 目录）"
            )
        storage_dir = storage["dir"]
        if not isinstance(storage_dir, str) or not storage_dir.strip():
            raise CalibrationConfigError(
                f"calibration.transfer.storage.dir 必须为非空字符串，实际为 {storage_dir!r}"
            )
        if Path(storage_dir).is_absolute() or ".." in Path(storage_dir).parts:
            raise CalibrationConfigError(
                f"calibration.transfer.storage.dir 必须是 data_dir 下的相对目录名，"
                f"实际为 {storage_dir!r}"
            )
        if Path(storage_dir).name != storage_dir:
            raise CalibrationConfigError(
                f"calibration.transfer.storage.dir 必须是单一目录名（不含路径分隔符），"
                f"实际为 {storage_dir!r}"
            )

        return cls(
            basis=basis,
            source_forms=forms["source_forms"],
            target_forms=forms["target_forms"],
            conditions=dict(conditions),
            storage_dir=storage_dir,
            adoption=adoption,
        )


@dataclass(frozen=True)
class CalibrationConfig:
    """calibration 段配置（周期/盲评 top-k/样本量/阈值/信度目标/收缩强度/自循环排除/窗口口径）。"""

    period_days: int
    top_k: int
    min_samples: int
    bias_threshold: float
    reliability_target: float
    ridge_lambda: float
    window_semantics: str  # 窗口口径（取值域单元素 half_open；缺项即报错，不取码内默认）
    window_semantics_change_date: str  # 口径生效日（ISO 日期；缺项即报错）
    transfer: TransferConfig  # 校准结论迁移口径与可比性条件（020；缺项即报错）
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

        # cadence 取值域收口（021 C11 / T2149）：取值域取自 `core/calibration/periods.py` 的
        # `SUPPORTED_CADENCES`（该文件一字不改）——越界即显式报错并**点名取值域**，
        # **不回落**日级/周级、不发明"双周/月"等第三档量纲。既有
        # `core/calibration/drift_config.py` 与 `agents/promo/config.py` 两处同取值域校验
        # 原样保留（同一口径的多个检查点，不是三套口径）。
        period_days = req("period_days")
        if isinstance(period_days, bool) or period_days not in SUPPORTED_CADENCES:
            raise CalibrationConfigError(
                f"calibration.period_days 必须 ∈ {SUPPORTED_CADENCES}"
                f"（取值域 {SUPPORTED_CADENCES}），实际为 {period_days!r}"
                "——越界即拒绝启动，不回落日级/周级"
            )

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

        window_semantics = req("window_semantics")
        if window_semantics != WINDOW_SEMANTICS:
            raise CalibrationConfigError(
                "calibration.window_semantics 取值域单元素"
                f"（唯一取值 {WINDOW_SEMANTICS!r}），实际为 {window_semantics!r}"
            )
        change_date = req("window_semantics_change_date")
        normalized_change_date = (
            change_date.isoformat() if isinstance(change_date, date) else change_date
        )
        if not isinstance(normalized_change_date, str) or not normalized_change_date:
            raise CalibrationConfigError(
                "calibration.window_semantics_change_date 必须为 ISO 日期（YYYY-MM-DD），"
                f"实际为 {change_date!r}"
            )
        try:
            date.fromisoformat(normalized_change_date)
        except ValueError as exc:
            raise CalibrationConfigError(
                "calibration.window_semantics_change_date 必须为 ISO 日期（YYYY-MM-DD），"
                f"实际为 {change_date!r}"
            ) from exc

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
            window_semantics=window_semantics,
            window_semantics_change_date=normalized_change_date,
            transfer=TransferConfig.from_dict(section),
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
