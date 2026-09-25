"""校准结论迁移件与可比性判定（功能 020 契约 C15；业务无关机制件）。

**只迁结论、不迁权重**（FR-012 / 原则一）：

- 迁移件 = 来源标识（形态 / 评估器 `id@version` / 周期 / 样本量 / 来源件引用）+ 迁移口径 +
  **可比性条件与判定**（`transferable | not_transferable` + 逐条原因）+ 采纳状态与留痕；
- 可比性条件**由形态配置声明**（`calibration.transfer.conditions`，两形态均声明、缺项即报错）；
  **先声明后判定**：条件值来自配置，观测值只来自**只读**产物，代码里既不发明条件也不发明阈值；
- 不可比 ⇒ **拒绝迁移**并如实登记原因（不得静默丢弃、不得降格为"仅供参考"的数字）；
- 采纳走**人工两键**（`transfer` 提案 → `transfer-confirm` / `transfer-shelve`），
  `status` 由 `overrides[]` **末条派生**（`pending → confirmed | shelved`，终态不可逆）；
- 迁移件 append-only：同 `transfer_id` 重产拒绝、系统字段改写即 `system_digest` 校验失败 ⇒ 拒采信
  （摘要与写入语义复用 `core/billing/bill.py` 的 `system_digest` / `write_snapshot` /
  `load_snapshot`，
  **不新造第二套**）。

## 业务无关与"零形态分支"（原则五 / `tests/unit/test_form_switch.py` 常驻守护）

本模块**零形态字面量**（形态名一律经配置的 `form` 值传入，代码里不出现任何形态名）、
**零 `refit` import**
（权重再拟合仍走 010 的 `propose → confirm`，本模块 `confirm_proposal` 调用点数恒 0）、
**零写树/写库入口**（不改任何既有节点的 `eval_breakdown` 与得分）。

## 来源只读（T2059⑤）

来源件 = 010 台账（`ledger/{agent}/{evaluator}.jsonl`）、锚点分布快照
（`snapshots/{agent}/{evaluator}/{period}.json`）、信度报告（`reports/{period}*.json`，
经 `latest_report_path` 单点）与漂移产物（`drift/metrics/{agent}/{evaluator}/{period}.json`）——
**只读，零写入**；迁移件另落 `{data_dir}/transfers/{transfer_id}.json`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import blake3

from core.billing.bill import (
    SnapshotIntegrityError,
    load_snapshot,
    system_digest,
    write_snapshot,
)
from core.billing.runlog import RUN_SOURCES
from core.calibration.config import (
    TRANSFER_CONDITION_IDS,
    CalibrationConfig,
    TransferConfig,
)
from core.calibration.drift_config import DriftConfig
from core.calibration.errors import CalibrationConfigError
from core.calibration.periods import cadence_of
from core.calibration.report import latest_report_path
from core.evaluators.errors import ValidationError

#: 迁移件 schema 版本（形状变更必须升版本，不静默改形状）
SCHEMA_VERSION = 1

#: `status` 取值域（与 `core/calibration/models.py` 的提案状态机同构）
TRANSFER_STATUSES = ("pending", "confirmed", "shelved")

#: 可比性判定取值域
COMPARABILITY_VERDICTS = ("transferable", "not_transferable")

#: `conclusion` **只含结论**（信度 / 偏差 / 样本量 / 漂移状态）
CONCLUSION_KEYS = ("kendall_tau", "pearson_r", "mean_shift", "samples", "drift_status")

#: **禁止**出现在 `conclusion` 内的权重键（010 提案形状见 `core/calibration/refit.py`）
FORBIDDEN_CONCLUSION_KEYS = (
    "candidate_weights",
    "current_weights",
    "fit_objective",
    "ridge_lambda",
)

#: 系统字段（内容摘要覆盖面）：被改写 ⇒ `system_digest` 校验失败 ⇒ 拒采信；
#: `status` / `confirmed_by` / `confirmed_at` / `overrides` 是人工批注面，**不在**摘要内。
TRANSFER_SYSTEM_FIELDS = (
    "schema",
    "transfer_id",
    "source_form",
    "target_form",
    "evaluator_key",
    "period",
    "samples",
    "source_ref",
    "transfer_basis",
    "comparability",
    "conclusion",
    "created_at",
)

#: 来源件引用种类（与契约 C15 的 `source_ref` 逐字一致）
SOURCE_REF_KINDS = ("ledger", "snapshot", "report", "drift")

#: 无可迁移结论时的**如实标注**（FR-012 / T2065①：不得以模拟回流结论充当来源）
NO_SOURCE_CONCLUSION = "无可迁移结论（来源缺失）"

#: 覆盖观测的来源标注取值域（观测由**装配面/运营侧显式声明**，与 019 的 `--cost-source` 同款纪律；
#: `fixture_drill` = 离线演练夹具，**不得**据以宣称真实回流已达成）
COVERAGE_SOURCES = ("operator_reported", "coverage_view", "fixture_drill", "untracked")


class TransferError(Exception):
    """迁移面拒绝（CLI 退出码 1：迁移被拒 / 同键重产被拒 / 来源缺失）。"""


class TransferSourceMissingError(TransferError):
    """无可迁移结论（来源缺失）：如实标注，不编造、不降级为"参考"。"""


class TransferConflictError(TransferError):
    """同 `transfer_id` 重产被拒（append-only：只增不改、不覆盖）。"""


# ---------------------------------------------------------------------------
# 路径与标识
# ---------------------------------------------------------------------------


def transfer_dir(data_dir: str | Path) -> Path:
    """迁移件目录（与 010 的 `proposals/` 并列；目录名由配置声明 `storage.dir`）。"""
    return Path(data_dir) / "transfers"


def transfer_path(data_dir: str | Path, transfer_id: str) -> Path:
    """迁移件路径：`{data_dir}/transfers/{transfer_id}.json`（镜像 `proposals/{id}.json` 先例）。"""
    return transfer_dir(data_dir) / f"{transfer_id}.json"


def transfer_id_of(evaluator_key: str, period: str, source_form: str, target_form: str) -> str:
    """`transfer_id` = `<evaluator_id>-<period>-<source_form>-<target_form>`（确定性派生）。"""
    for name, value in (
        ("evaluator_key", evaluator_key),
        ("period", period),
        ("source_form", source_form),
        ("target_form", target_form),
    ):
        if not isinstance(value, str) or not value:
            raise ValidationError(f"{name} 必须为非空字符串，实际为 {value!r}")
    evaluator_id = evaluator_key.partition("@")[0]
    if not evaluator_id:
        raise ValidationError(f"evaluator_key 必须为 id@version 形态，实际为 {evaluator_key!r}")
    return f"{evaluator_id}-{period}-{source_form}-{target_form}"


# ---------------------------------------------------------------------------
# 来源件（只读）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceConclusion:
    """来源结论（只读读出的汇总；**不落盘**）。"""

    agent_id: str
    evaluator_id: str
    record: dict
    source_ref: tuple[dict, ...]
    snapshot: dict | None = None
    report: dict | None = None
    drift: dict | None = None


def _file_digest(path: Path) -> str:
    return blake3.blake3(path.read_bytes()).hexdigest()


def _relative(path: Path, data_dir: Path) -> str:
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ledger_candidates(data_dir: str | Path, evaluator_id: str) -> tuple[Path, ...]:
    """该评估器在**全部 agent 目录**下的台账路径（升序，确定性；0 个 ⇒ 来源缺失）。"""
    root = Path(data_dir) / "ledger"
    if not root.is_dir():
        return ()
    return tuple(sorted(root.glob(f"*/{evaluator_id}.jsonl")))


def read_source_conclusion(
    data_dir: str | Path, *, evaluator_key: str, period: str
) -> SourceConclusion | None:
    """只读读出某评估器某周期的来源结论；来源缺失 ⇒ `None`（不报错、不编造）。

    来源件：台账行（结论本体）+ 锚点分布快照 + 信度报告 + 漂移产物（存在即引用，缺则如实缺）。
    """
    data_dir = Path(data_dir)
    evaluator_id = evaluator_key.partition("@")[0]
    candidates = ledger_candidates(data_dir, evaluator_id)
    if len(candidates) > 1:
        raise TransferError(
            f"来源台账不唯一（{evaluator_id} 在 "
            f"{[path.parent.name for path in candidates]} 下各有台账）：不得猜测用哪一份"
        )
    if not candidates:
        return None
    ledger_path = candidates[0]
    agent_id = ledger_path.parent.name
    record = None
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("evaluator_key") == evaluator_key and payload.get("period") == period:
            record = payload
    if record is None:
        return None

    snapshot_path = data_dir / "snapshots" / agent_id / evaluator_id / f"{period}.json"
    report_path = latest_report_path(data_dir, period)
    drift_path = data_dir / "drift" / "metrics" / agent_id / evaluator_id / f"{period}.json"

    refs: list[dict] = [{"kind": "ledger", "path": _relative(ledger_path, data_dir), "digest": ""}]
    for kind, path in (
        ("snapshot", snapshot_path),
        ("report", report_path),
        ("drift", drift_path),
    ):
        if path is not None and path.is_file():
            refs.append(
                {"kind": kind, "path": _relative(path, data_dir), "digest": _file_digest(path)}
            )
    refs[0]["digest"] = _file_digest(ledger_path)
    return SourceConclusion(
        agent_id=agent_id,
        evaluator_id=evaluator_id,
        record=record,
        source_ref=tuple(refs),
        snapshot=_read_json(snapshot_path),
        report=_read_json(report_path) if report_path is not None else None,
        drift=_read_json(drift_path),
    )


# ---------------------------------------------------------------------------
# 可比性条件（键集由配置声明；判定实现单点在本模块）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparabilityContext:
    """判定输入（全部来自**配置声明**与**只读产物**，零推断）。"""

    source_form: str
    target_form: str
    evaluator_key: str
    period: str
    source_cadence: int
    target_cadence: int
    registered_ids: frozenset[str]
    target_detector_version: str
    record: Mapping
    drift_record: Mapping | None
    real_coverage_days: int | None
    coverage_source: str
    conversion_basis: str


def _algorithm_of(detector_version: str | None) -> str:
    """`drift_detector@1.0.0+<哈希>` → `drift_detector@1.0.0`（算法标识；哈希随量纲而变）。"""
    if not isinstance(detector_version, str) or not detector_version:
        return ""
    return detector_version.partition("+")[0]


def _correlation_of(record: Mapping) -> float | None:
    for key in ("kendall_tau", "pearson_r"):
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _c_evaluator_registered(
    declared: Any, ctx: ComparabilityContext
) -> tuple[Any, bool, str, None]:
    observed = ctx.evaluator_key.partition("@")[0] in ctx.registered_ids
    reason = (
        "目标形态的 evaluator_weights 声明面未登记该评估器（版本冻结：evaluator_key 逐字携带 "
        "id@version，评估器组合必须一一对应）"
    )
    return observed, bool(declared) and observed, reason, None


def _c_min_samples(declared: Any, ctx: ComparabilityContext) -> tuple[Any, bool, str, None]:
    observed = ctx.record.get("samples")
    satisfied = isinstance(observed, int) and observed >= int(declared)
    reason = f"来源样本量 {observed} < 下限 {declared}"
    return observed, satisfied, reason, None


def _c_detector_version_match(
    declared: Any, ctx: ComparabilityContext
) -> tuple[Any, bool, str, None]:
    observed = (ctx.drift_record or {}).get("detector_version")
    target_algorithm = _algorithm_of(ctx.target_detector_version)
    if not isinstance(observed, str) or not observed:
        return None, False, "来源无漂移产物（缺 detector_version 可核，判定口径不明）", None
    algorithm_ok = _algorithm_of(observed) == target_algorithm
    hash_ok = observed == ctx.target_detector_version
    # 哈希差异（量纲/窗口不同）**必须**由显式换算口径承接：未声明 ⇒ 不可迁移（先声明后判定）
    satisfied = bool(declared) and algorithm_ok and (hash_ok or bool(ctx.conversion_basis.strip()))
    reason = (
        "漂移判定口径哈希不一致（来源 "
        f"{observed} vs 目标 {ctx.target_detector_version}）："
        "口径即版本，量纲差异必须由显式换算口径承接"
        if algorithm_ok
        else f"漂移判定算法不一致（来源 {observed!r} vs 目标 {ctx.target_detector_version!r}）"
    )
    return observed, satisfied, reason, None


def _c_cadence_conversion(declared: Any, ctx: ComparabilityContext) -> tuple[Any, bool, str, None]:
    observed = f"{ctx.source_cadence}->{ctx.target_cadence}"
    basis = str(declared or "").strip()
    if ctx.source_cadence == ctx.target_cadence:
        return observed, True, "", None
    satisfied = bool(basis) or bool(ctx.conversion_basis.strip())
    reason = (
        f"未声明显式换算口径：{observed}（日级 → 周级的结论不得直接比，"
        "要迁移必须在配置里声明显式换算口径）"
    )
    return observed, satisfied, reason, None


def _c_real_coverage_days(declared: Any, ctx: ComparabilityContext) -> tuple[Any, bool, str, str]:
    observed = ctx.real_coverage_days
    if observed is None:
        return (
            None,
            False,
            f"来源真实覆盖天数未标定（未提供观测值；真实回流待运营，≥{declared} 天是迁移前提）",
            ctx.coverage_source,
        )
    satisfied = int(observed) >= int(declared)
    reason = f"来源真实覆盖 {observed} 天 < 下限 {declared} 天"
    return observed, satisfied, reason, ctx.coverage_source


def _c_reliability_floor(declared: Any, ctx: ComparabilityContext) -> tuple[Any, bool, str, None]:
    observed = _correlation_of(ctx.record)
    if observed is None:
        return None, False, "来源未产出信度值（样本不足 ⇒ 不产结论，不得迁移）", None
    return observed, observed >= float(declared), f"来源信度 {observed} < 下限 {declared}", None


def _c_max_abs_mean_shift(declared: Any, ctx: ComparabilityContext) -> tuple[Any, bool, str, None]:
    raw = ctx.record.get("mean_shift")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None, False, "来源未产出偏差值（样本不足 ⇒ 不产结论，不得迁移）", None
    observed = abs(float(raw))
    return observed, observed <= float(declared), f"来源偏差幅度 {observed} > 上限 {declared}", None


def _c_require_drift_pass(declared: Any, ctx: ComparabilityContext) -> tuple[Any, bool, str, None]:
    observed = (ctx.drift_record or {}).get("verdict")
    satisfied = bool(declared) and observed == "normal"
    reason = f"来源漂移状态 {observed!r} 非 pass（口径见 012：normal ⇒ 未漂移）"
    return observed, satisfied, reason, None


#: 条件 id → 判定实现（键集与配置面 `TRANSFER_CONDITION_IDS` **必须**一一对应——
#: 配置可声明而实现缺席即"判定缺项"，故下方模块级断言常驻）
_CONDITION_EVALUATORS = {
    "evaluator_registered": _c_evaluator_registered,
    "min_samples": _c_min_samples,
    "detector_version_match": _c_detector_version_match,
    "cadence_conversion": _c_cadence_conversion,
    "real_coverage_days": _c_real_coverage_days,
    "reliability_floor": _c_reliability_floor,
    "max_abs_mean_shift": _c_max_abs_mean_shift,
    "require_drift_pass": _c_require_drift_pass,
}

if tuple(_CONDITION_EVALUATORS) != tuple(TRANSFER_CONDITION_IDS):  # pragma: no cover - 导入期护栏
    raise RuntimeError(
        "可比性条件 id 与判定实现不一致：配置面 "
        f"{TRANSFER_CONDITION_IDS} vs 实现 {tuple(_CONDITION_EVALUATORS)}"
    )


def evaluate_condition(condition_id: str, declared: Any, ctx: ComparabilityContext) -> dict:
    """单条条件的判定条目：`{id, declared, observed, satisfied}`（+ 未满足时 `reason`）。"""
    if condition_id not in _CONDITION_EVALUATORS:
        raise CalibrationConfigError(
            f"可比性条件 {condition_id!r} 未实现（不得取码内默认；取值域 {TRANSFER_CONDITION_IDS}）"
        )
    observed, satisfied, reason, source = _CONDITION_EVALUATORS[condition_id](declared, ctx)
    entry: dict = {
        "id": condition_id,
        "declared": declared,
        "observed": observed,
        "satisfied": bool(satisfied),
    }
    if not satisfied and reason:
        entry["reason"] = reason
    if source and condition_id == "real_coverage_days":
        entry["source"] = source
    return entry


def comparability_of(transfer_config: TransferConfig, ctx: ComparabilityContext) -> dict:
    """可比性判定（**先声明后判定**）：条件完备 + 判定 + 逐条原因。"""
    conditions = [
        evaluate_condition(condition_id, declared, ctx)
        for condition_id, declared in transfer_config.conditions.items()
    ]
    unsatisfied = [entry for entry in conditions if not entry["satisfied"]]
    verdict = "not_transferable" if unsatisfied else "transferable"
    reasons = [f"{entry['id']}：{entry['reason']}" for entry in unsatisfied if entry.get("reason")]
    return {"conditions": conditions, "verdict": verdict, "reasons": reasons}


# ---------------------------------------------------------------------------
# 迁移件构造与 append-only 存储
# ---------------------------------------------------------------------------


def _form_of(config: Mapping) -> str:
    form = config.get("form")
    if not isinstance(form, str) or not form:
        raise CalibrationConfigError("形态配置缺少 form（迁移的来源/目标形态必须由配置声明）")
    return form


def _load_form_config(path: str | Path) -> tuple[str, CalibrationConfig, dict]:
    config = CalibrationConfig.from_yaml(path)
    raw = _raw_yaml(path)
    return _form_of(raw), config, raw


def _raw_yaml(path: str | Path) -> dict:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CalibrationConfigError(f"形态配置必须是映射：{path}")
    return data


def _registered_ids(raw: Mapping) -> frozenset[str]:
    weights = raw.get("evaluator_weights")
    if not isinstance(weights, dict):
        raise CalibrationConfigError("形态配置缺少 evaluator_weights 段（评估器登记面必须声明）")
    ids: set[str] = set()
    for entries in weights.values():
        if isinstance(entries, dict):
            ids.update(str(key) for key in entries)
    return frozenset(ids)


def _drift_status(drift_record: Mapping | None) -> str | None:
    if not drift_record:
        return None
    verdict = drift_record.get("verdict")
    return "pass" if verdict == "normal" else verdict


def build_transfer(
    data_dir: str | Path,
    *,
    source_config_path: str | Path,
    target_config_path: str | Path,
    evaluator_key: str,
    period: str,
    real_coverage_days: int | None = None,
    coverage_source: str = "untracked",
    created_at: str | None = None,
    source_conclusion: SourceConclusion | None = None,
) -> dict:
    """构造迁移件（**纯构造**，不落盘；落盘走 `save_transfer`）。

    来源缺失 ⇒ `TransferSourceMissingError`（如实标注「无可迁移结论（来源缺失）」，**不编造**）。
    `real_coverage_days` / `coverage_source`：覆盖观测与**其来源标注**由装配面显式声明
    （镜像 019 的 `--measured-usd` / `--cost-source` 口径；未提供 ⇒ 观测为 `None` ⇒ 条件不满足，
    **不猜、不按"跑通了"推断**）。
    """
    source_form, source_config, _ = _load_form_config(source_config_path)
    target_form, target_config, target_raw = _load_form_config(target_config_path)
    transfer_config = source_config.transfer

    if source_form not in transfer_config.source_forms:
        raise CalibrationConfigError(
            f"来源形态 {source_form!r} 未在 {source_config_path} 的 "
            f"calibration.transfer.source_forms {list(transfer_config.source_forms)} 中声明"
        )
    if target_form not in transfer_config.target_forms:
        raise CalibrationConfigError(
            f"目标形态 {target_form!r} 未在 {source_config_path} 的 "
            f"calibration.transfer.target_forms {list(transfer_config.target_forms)} 中声明"
        )
    if coverage_source not in COVERAGE_SOURCES:
        raise ValidationError(
            f"覆盖观测来源取值域外：{coverage_source!r}（取值域 {COVERAGE_SOURCES}）"
        )
    if period and cadence_of(period) != source_config.period_days:
        raise ValidationError(
            f"周期量纲与来源形态不符：period={period!r} 的 cadence {cadence_of(period)} ≠ "
            f"来源 {source_form!r} 声明的 calibration.period_days {source_config.period_days}"
        )

    if source_conclusion is None:
        source_conclusion = read_source_conclusion(
            data_dir, evaluator_key=evaluator_key, period=period
        )
    if source_conclusion is None:
        raise TransferSourceMissingError(
            f"{NO_SOURCE_CONCLUSION}：{source_form} 线无 {evaluator_key} 在 {period} 的台账结论"
            f"（data_dir={data_dir}）——继续观察条件：来源真实覆盖 ≥ "
            f"{transfer_config.conditions.get('real_coverage_days')} 天且样本 ≥ "
            f"{transfer_config.conditions.get('min_samples')}；**不得**以模拟回流的结论充当来源"
        )

    record = source_conclusion.record
    drift_record = source_conclusion.drift
    ctx = ComparabilityContext(
        source_form=source_form,
        target_form=target_form,
        evaluator_key=evaluator_key,
        period=period,
        source_cadence=source_config.period_days,
        target_cadence=target_config.period_days,
        registered_ids=_registered_ids(target_raw),
        target_detector_version=_target_detector_version(target_config_path),
        record=record,
        drift_record=drift_record,
        real_coverage_days=None if real_coverage_days is None else int(real_coverage_days),
        coverage_source=coverage_source,
        conversion_basis=str(transfer_config.conditions.get("cadence_conversion") or ""),
    )
    comparability = comparability_of(transfer_config, ctx)
    conclusion = _conclusion_of(record, drift_record)
    payload: dict = {
        "schema": SCHEMA_VERSION,
        "transfer_id": transfer_id_of(evaluator_key, period, source_form, target_form),
        "source_form": source_form,
        "target_form": target_form,
        "evaluator_key": evaluator_key,
        "period": period,
        "samples": int(record.get("samples") or 0),
        "source_ref": [dict(ref) for ref in source_conclusion.source_ref],
        "transfer_basis": transfer_config.basis,
        "comparability": comparability,
        "conclusion": conclusion,
        "status": "pending",
        "confirmed_by": "",
        "confirmed_at": "",
        "overrides": [],
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }
    payload["system_digest"] = system_digest(payload, TRANSFER_SYSTEM_FIELDS)
    return payload


def _conclusion_of(record: Mapping, drift_record: Mapping | None) -> dict:
    conclusion = {
        "kendall_tau": record.get("kendall_tau"),
        "pearson_r": record.get("pearson_r"),
        "mean_shift": record.get("mean_shift"),
        "samples": record.get("samples"),
        "drift_status": _drift_status(drift_record),
    }
    _assert_conclusion_shape(conclusion)
    return conclusion


def _assert_conclusion_shape(conclusion: Mapping) -> None:
    """`conclusion` **只含结论**：出现的键必须是 `CONCLUSION_KEYS` 的子集（零权重键）。"""
    forbidden = [key for key in FORBIDDEN_CONCLUSION_KEYS if key in conclusion]
    if forbidden:
        raise ValidationError(f"迁移件结论内不得出现权重键：{forbidden}（只迁结论、不迁权重）")
    unknown = [key for key in conclusion if key not in CONCLUSION_KEYS]
    if unknown:
        raise ValidationError(f"迁移件结论键取值域外：{unknown}（只允许 {CONCLUSION_KEYS}）")


def _target_detector_version(target_config_path: str | Path) -> str:
    from core.calibration.drift_metrics import detector_version

    return detector_version(DriftConfig.from_yaml(target_config_path))


def save_transfer(data_dir: str | Path, payload: Mapping) -> Path:
    """落盘迁移件：**已存在即拒绝**（append-only，同 `transfer_id` 不得重产）。"""
    path = transfer_path(data_dir, str(payload.get("transfer_id") or ""))
    if path.exists():
        raise TransferConflictError(
            f"同 transfer_id 的迁移件已存在（append-only：不得重产、不得覆盖）：{path}"
        )
    write_snapshot(path, payload, system_fields=TRANSFER_SYSTEM_FIELDS)
    return path


def load_transfer(data_dir: str | Path, transfer_id: str) -> dict:
    """读取迁移件并机检 `system_digest`：系统字段被改写 ⇒ 拒采信（不静默取篡改值）。"""
    path = transfer_path(data_dir, transfer_id)
    if not path.is_file():
        raise TransferError(f"迁移件不存在：{path}")
    return load_snapshot(path, system_fields=TRANSFER_SYSTEM_FIELDS)


def append_transfer_decision(
    data_dir: str | Path,
    transfer_id: str,
    *,
    decision: str,
    by: str,
    reason: str,
    at: str | None = None,
) -> dict:
    """人工两键：采纳（`confirmed`）/ 搁置（`shelved`）——**只追加** `overrides[]` 一行。

    - 先机检 `system_digest`（改写过的件拒采信）；
    - 仅 `pending` 可迁移（终态不可逆）；
    - **不可迁移 ⇒ 不得采纳**（不变量：`not_transferable ⇒ status != confirmed`，误迁移恒 0）；
    - `status` 由 `overrides[]` **末条派生**；系统字段逐字节不变（摘要因此仍成立）。
    """
    if decision not in ("confirmed", "shelved"):
        raise ValidationError(f"人工两键取值域为 ('confirmed','shelved')，实际为 {decision!r}")
    if not str(by or "").strip() or not str(reason or "").strip():
        raise ValidationError("人工两键必须带操作人与理由（留痕不得无归属）")
    payload = load_transfer(data_dir, transfer_id)
    status = payload.get("status")
    if status != "pending":
        raise TransferError(
            f"迁移件 {transfer_id} 状态为 {status!r}，仅 pending 可采纳/搁置（终态不可逆）"
        )
    verdict = (payload.get("comparability") or {}).get("verdict")
    if decision == "confirmed" and verdict != "transferable":
        raise TransferError(
            f"迁移件 {transfer_id} 的判定为 {verdict!r}：不可比即**拒绝迁移**，"
            "不得采纳（误迁移次数恒 0）——不可迁移件只能走 transfer-shelve"
        )
    stamp = at or datetime.now(UTC).isoformat()
    payload["overrides"] = [
        *payload.get("overrides", []),
        {"decision": decision, "by": str(by), "reason": str(reason), "at": str(stamp)},
    ]
    payload["status"] = payload["overrides"][-1]["decision"]
    if decision == "confirmed":
        payload["confirmed_by"] = str(by)
        payload["confirmed_at"] = str(stamp)
    path = transfer_path(data_dir, transfer_id)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def list_transfers(data_dir: str | Path) -> tuple[dict, ...]:
    """读取全部迁移件（升序）；系统字段被改写者**如实列出并标不可采信**（不抛错、不隐瞒）。"""
    root = transfer_dir(data_dir)
    if not root.is_dir():
        return ()
    listed: list[dict] = []
    for path in sorted(root.glob("*.json")):
        try:
            listed.append(load_snapshot(path, system_fields=TRANSFER_SYSTEM_FIELDS))
        except SnapshotIntegrityError as exc:
            listed.append({"transfer_id": path.stem, "trusted": False, "error": str(exc)})
    return tuple(listed)


def transfer_report(
    data_dir: str | Path,
    *,
    transfer_config: TransferConfig | None = None,
) -> dict:
    """迁移面只读报表：逐条判定 + 无来源时**如实标注**（零写入）。"""
    transfers = list_transfers(data_dir)
    rows = [
        {
            "transfer_id": item.get("transfer_id", ""),
            "source_form": item.get("source_form"),
            "target_form": item.get("target_form"),
            "evaluator_key": item.get("evaluator_key"),
            "period": item.get("period"),
            "samples": item.get("samples"),
            "transfer_basis": item.get("transfer_basis"),
            "verdict": (item.get("comparability") or {}).get("verdict"),
            "status": item.get("status"),
            "reasons": (item.get("comparability") or {}).get("reasons", []),
            "trusted": item.get("trusted", True),
        }
        for item in transfers
    ]
    conditions = dict(transfer_config.conditions) if transfer_config is not None else {}
    observe = {
        "min_real_days": conditions.get("real_coverage_days"),
        "min_samples": conditions.get("min_samples"),
        "note": (
            "继续观察条件：来源真实覆盖 ≥ min_real_days 且样本 ≥ min_samples"
            if conditions
            else "未给形态配置（--config）亦无既有迁移件可参照 ⇒ 阈值未标定（不发明数字）"
        ),
    }
    untrusted = [row["transfer_id"] for row in rows if not row["trusted"]]
    return {
        "dir": str(transfer_dir(data_dir)),
        "transfers": rows,
        "counts": {
            "total": len(rows),
            "pending": sum(1 for row in rows if row["status"] == "pending"),
            "confirmed": sum(1 for row in rows if row["status"] == "confirmed"),
            "shelved": sum(1 for row in rows if row["status"] == "shelved"),
            "not_transferable": sum(1 for row in rows if row["verdict"] == "not_transferable"),
            "untrusted": len(untrusted),
        },
        "conclusion": NO_SOURCE_CONCLUSION if not rows else "机制已就绪：逐条判定见 transfers",
        "untrusted": untrusted,
        "observe_conditions": observe,
        "source_domain": list(RUN_SOURCES),
        "evidence_claim": "mechanism_ready_real_feedback_pending",
        "note": (
            "机制已就绪 / 真实回流待运营：迁移件只迁结论、不改权重（权重再拟合仍走 010 的提案 → "
            "人工确认）；采纳不改动任何既有节点的 eval_breakdown 与得分；"
            "无可迁移结论时如实标注「无可迁移结论（来源缺失）」"
        ),
    }
