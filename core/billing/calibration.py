"""最小规模校准记录 = 扩量的先决条件（机制件，业务无关；契约 C12）。

五模块之一，承接"必须先以最小规模验证协议与计费口径，验证通过后方可扩量"的机制面：

- `record_calibration(...) -> CalibrationRecord`：渠道 / 环节档 / **当时配置价目快照**
  （含 `price_matrix` 与 `peak_windows_snapshot`）/ 实测花费与样本量 /
  `deviation = (实测 − 按价目折算) / 按价目折算` / `passed`（阈值 `deviation_tolerance`
  与 `min_samples`）/ `reasons` / 口径备注 / `at`；
- **append-only**：同 `(channel_id, calibration_id)` 重产拒绝（一次性快照 + `system_digest` 机检）；
- `require_calibration(...)`：扩量前的六条先决检查（无 id / 不存在或不同渠道 / `passed != true` /
  样本量不足 / 超期 / 偏差超容差）——任一命中即拒绝并给出逐条理由；
- 渠道校准状态由**最新记录**派生 `untested|pass|fail|stale`（超期 ⇒ `stale`，按 `untested` 处理）。

"实测花费"的来源是**既有真实调用记录**的复述（运行记录 + 网关账目/账本），由调用方以
`measured_cost_usd` / `cost_source` 传入——本模块**不发起厂商调用**，故不产生新的真实装配点。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.billing.bill import (
    SnapshotIntegrityError,
    load_snapshot,
    system_digest,
    write_snapshot,
)
from core.billing.budget import AlertLog, BudgetConfig, alerts_path, channel_dir
from core.yaml_edit import YamlEditError, replace_section_entries, upsert_section_entries

# 渠道校准状态取值域（由最新记录派生）
CALIBRATION_STATES = ("untested", "pass", "fail", "stale")
# 校准记录的系统字段（内容哈希覆盖面；人工批注只追加、不改这些字段）
CALIBRATION_SYSTEM_FIELDS = (
    "calibration_id",
    "channel_id",
    "tier_id",
    "prices_snapshot",
    "measured",
    "deviation",
    "passed",
    "reasons",
    "note",
    "at",
)


class CalibrationRecordError(Exception):
    """校准记录错误（缺 id、记录不存在或不同渠道、先决条件不满足、同键重产）。"""


@dataclass(frozen=True)
class CalibrationRecord:
    """校准记录（一次性快照）：扩量的先决条件，产出即冻结。"""

    calibration_id: str
    channel_id: str
    tier_id: str
    prices_snapshot: Mapping
    measured: Mapping
    deviation: float
    passed: bool
    reasons: tuple[str, ...]
    note: str
    at: str
    system_digest: str = ""
    overrides: tuple[Mapping, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "channel_id": self.channel_id,
            "tier_id": self.tier_id,
            "prices_snapshot": dict(self.prices_snapshot),
            "measured": dict(self.measured),
            "deviation": self.deviation,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "note": self.note,
            "at": self.at,
            "system_digest": self.system_digest,
            "overrides": [dict(item) for item in self.overrides],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def calibration_path(root: str | Path, channel_id: str, calibration_id: str) -> Path:
    """校准记录路径（C4）：`billing/{channel}/calibrations/{calibration_id}.json`。"""
    return channel_dir(root, channel_id) / "calibrations" / f"{calibration_id}.json"


def _require_amount(path: str, value: object, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationRecordError(f"{path} 必须为数值，实际为 {value!r}")
    number = float(value)
    if positive and number <= 0:
        raise CalibrationRecordError(f"{path} 必须 > 0（偏差无定义即拒绝记录），实际为 {number!r}")
    if not positive and number < 0:
        raise CalibrationRecordError(f"{path} 必须 ≥ 0，实际为 {number!r}")
    return number


def _require_sample_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CalibrationRecordError(f"measured.sample_count 必须为 ≥ 1 的整数，实际为 {value!r}")
    return value


def _attribution_note(cfg: BudgetConfig, channel_id: str) -> str:
    """口径备注：峰谷归属与时区（三处可见之一——报告/运行记录、档位快照、本记录的 note）。"""
    snapshot = cfg.peak_windows.to_snapshot()
    windows = snapshot["windows"] or []
    rendered = (
        "全谷时（windows=[]）"
        if not windows
        else ", ".join(f"[{item['start']}, {item['end']})" for item in windows)
    )
    return (
        f"峰谷归属={snapshot['attribution']}（按调用开始时刻，闭开区间 [start, end)，"
        f"跨峰谷不拆分；时区={snapshot['timezone']}）；窗口区间={rendered}；"
        f"渠道={channel_id}"
    )


def record_calibration(
    calibration_id: str,
    *,
    cfg: BudgetConfig,
    channel_id: str,
    tier_id: str,
    prices_snapshot: Mapping,
    sample_count: int,
    measured_cost_usd: float,
    expected_cost_usd: float,
    root: str | Path,
    note: str = "",
    cost_source: str = "",
    at: datetime | None = None,
) -> CalibrationRecord:
    """落一条校准记录（append-only）：偏差与合格判定在此算定，口径备注随记录冻结。

    `measured_cost_usd` = 实测花费（**既有真实调用记录**的复述，来源标注在 `cost_source`）；
    `expected_cost_usd` = 按当时配置价目折算的金额（> 0，否则偏差无定义 ⇒ 拒绝记录）。
    """
    if not str(calibration_id or "").strip():
        raise CalibrationRecordError("calibration_id 不能为空（无 id 即无从引用，拒绝记录）")
    spec = cfg.channel(channel_id)
    tier = cfg.tier(tier_id)
    samples = _require_sample_count(sample_count)
    measured = _require_amount("measured_cost_usd", measured_cost_usd, positive=False)
    expected = _require_amount("expected_cost_usd", expected_cost_usd, positive=True)
    if not isinstance(prices_snapshot, Mapping):
        raise CalibrationRecordError(f"prices_snapshot 必须为映射，实际为 {prices_snapshot!r}")
    deviation = (measured - expected) / expected
    tolerance = float(cfg.calibration["deviation_tolerance"])
    min_samples = int(cfg.calibration["min_samples"])
    reasons: list[str] = []
    if samples < min_samples:
        reasons.append(f"样本量不足：{samples} < min_samples={min_samples}")
    if abs(deviation) > tolerance:
        reasons.append(f"偏差超容差：|deviation|={abs(deviation)} > {tolerance}")
    passed = not reasons
    if passed:
        reasons.append(
            f"合格：样本量 {samples} ≥ {min_samples} 且 |deviation|={abs(deviation)} ≤ {tolerance}"
        )
    moment = at or datetime.now(UTC)
    payload = {
        "calibration_id": str(calibration_id),
        "channel_id": spec.channel_id,
        "tier_id": tier.tier_id,
        "prices_snapshot": {
            "price_book": dict(prices_snapshot),
            "peak_windows_snapshot": cfg.peak_windows.to_snapshot(),
            "tier": tier.to_snapshot(),
            "channel_id": spec.channel_id,
        },
        "measured": {
            "sample_count": samples,
            "measured_cost_usd": measured,
            "expected_cost_usd": expected,
            "cost_source": str(cost_source),
        },
        "deviation": deviation,
        "passed": passed,
        "reasons": reasons,
        "note": "；".join(
            part for part in (str(note).strip(), _attribution_note(cfg, channel_id)) if part
        ),
        "at": moment.isoformat(),
    }
    payload["system_digest"] = system_digest(payload, CALIBRATION_SYSTEM_FIELDS)
    path = calibration_path(root, spec.channel_id, calibration_id)
    try:
        written = write_snapshot(path, payload, system_fields=CALIBRATION_SYSTEM_FIELDS)
    except SnapshotIntegrityError as exc:
        raise CalibrationRecordError(
            f"校准记录已存在（不可变快照，只增不改）：{path}（同键重产拒绝）"
        ) from exc
    return CalibrationRecord(
        prices_snapshot=written["prices_snapshot"],
        measured=written["measured"],
        reasons=tuple(written["reasons"]),
        **{
            key: written[key]
            for key in (
                "calibration_id",
                "channel_id",
                "tier_id",
                "deviation",
                "passed",
                "note",
                "at",
                "system_digest",
            )
        },
    )


def load_calibration(calibration_id: str, *, channel_id: str, root: str | Path) -> dict:
    """读取校准记录（`system_digest` 机检：系统字段被改写即报错，不静默取）。"""
    return load_snapshot(
        calibration_path(root, channel_id, calibration_id),
        system_fields=CALIBRATION_SYSTEM_FIELDS,
    )


def _is_expired(record: Mapping, cfg: BudgetConfig, moment: datetime) -> bool:
    recorded_at = datetime.fromisoformat(str(record.get("at", "")))
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    ttl = timedelta(days=int(cfg.calibration["record_ttl_days"]))
    return moment.astimezone(UTC) - recorded_at.astimezone(UTC) > ttl


def require_calibration(
    calibration_id: str,
    *,
    cfg: BudgetConfig,
    channel_id: str,
    tier_id: str,
    root: str | Path,
    at: datetime | None = None,
) -> dict:
    """扩量的先决条件（六条）：任一不满足即拒绝，并给出**逐条**理由（不糊涂拒绝）。

    ① 无 `calibration_id`；② 记录不存在或不同渠道（或不同环节档）；③ `passed != true`；
    ④ 样本量 < `min_samples`；⑤ 记录超期（`record_ttl_days`）；⑥ `deviation` 超容差。
    """
    if not str(calibration_id or "").strip():
        raise CalibrationRecordError("拒绝扩量（无 calibration_id）：校准记录是扩量的先决条件")
    cfg.channel(channel_id)
    cfg.tier(tier_id)
    try:
        record = load_calibration(calibration_id, channel_id=channel_id, root=root)
    except Exception as exc:  # noqa: BLE001 - 记录缺失/完整性失败一律拒绝扩量
        raise CalibrationRecordError(
            f"拒绝扩量（记录不存在或不可用）：{calibration_id!r}（{exc}）"
        ) from exc
    moment = at or datetime.now(UTC)
    failures: list[str] = []
    if str(record.get("channel_id")) != str(channel_id):
        failures.append(
            f"记录属于渠道 {record.get('channel_id')!r}，与本次渠道 {channel_id!r} 不符"
        )
    if str(record.get("tier_id")) != str(tier_id):
        failures.append(f"记录属于环节 {record.get('tier_id')!r}，与本次环节 {tier_id!r} 不符")
    if record.get("passed") is not True:
        failures.append(f"passed={record.get('passed')!r}（未通过最小规模校准）")
    measured = record.get("measured") or {}
    min_samples = int(cfg.calibration["min_samples"])
    if int(measured.get("sample_count", 0)) < min_samples:
        failures.append(f"样本量不足：{measured.get('sample_count')} < min_samples={min_samples}")
    if _is_expired(record, cfg, moment):
        failures.append(
            f"记录超期（record_ttl_days={cfg.calibration['record_ttl_days']}）"
            "：超期按未测处理，须重校"
        )
    tolerance = float(cfg.calibration["deviation_tolerance"])
    if abs(float(record.get("deviation", 0.0))) > tolerance:
        failures.append(
            f"偏差超容差：|deviation|={abs(float(record.get('deviation', 0.0)))} > {tolerance}"
        )
    if failures:
        raise CalibrationRecordError(f"拒绝扩量（先决条件不满足）：{'；'.join(failures)}")
    return record


def calibration_status(
    channel_id: str, *, cfg: BudgetConfig, root: str | Path, at: datetime | None = None
) -> str:
    """渠道校准状态（由**最新记录**派生）：`untested` / `pass` / `fail` / `stale`。

    超期 ⇒ `stale`（按 `untested` 处理：扩量恒拒绝，理由写明超期）。
    """
    cfg.channel(channel_id)
    directory = channel_dir(root, channel_id) / "calibrations"
    records = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            records.append(load_snapshot(path, system_fields=CALIBRATION_SYSTEM_FIELDS))
        except Exception:  # noqa: BLE001 - 损坏记录不得参与状态派生（不静默当作通过）
            records.append(None)
    if not records:
        return "untested"
    moment = at or datetime.now(UTC)
    valid = [record for record in records if record is not None]
    if not valid:
        return "untested"
    latest = max(valid, key=lambda record: str(record.get("at", "")))
    if _is_expired(latest, cfg, moment):
        return "stale"
    return "pass" if latest.get("passed") is True else "fail"


# ---------------------------------------------------------------------------
# 扩量面（C12）：raise_tier = 六条先决 + 定点改写 + 留痕
# ---------------------------------------------------------------------------


def _record(tier_id: str, calibration_id: str, by: str, by_reason: str, at: str, **extra) -> dict:
    """扩量留痕（追加式字典，由调用方写进 `alerts.jsonl`）。

    操作人的理由记作 `by_reason`（`reason` 留给**事件标签**：
    `tier_raised` / `uncalibrated_raise`），避免"人的理由"与事件类型同名字段互相覆盖。
    """
    return {
        "tier_id": tier_id,
        "calibration_id": calibration_id,
        "by": by,
        "by_reason": by_reason,
        "at": at,
        **extra,
    }


def raise_tier(
    channel_id: str,
    tier_id: str,
    limit_usd: float,
    *,
    calibration_id: str,
    by: str,
    reason: str,
    cfg: BudgetConfig,
    config_path: str | Path,
    root: str | Path,
    at: datetime | None = None,
) -> dict:
    """扩量：把某环节档的额度定点改写为新值（**必须有合格且未超期的校准记录**）。

    六条拒绝条件（C12，任一命中即拒绝 + `uncalibrated_raise` 留痕 + **配置一字不改**）：
    ① 无 `calibration_id`；② 记录不存在或不同渠道（或不同环节档）；③ `passed != true`；
    ④ 样本量 < `min_samples`；⑤ 记录超期（`record_ttl_days`）；⑥ `deviation` 超容差。

    落地动作（合格时）：经 `core/yaml_edit.py` **定点改写**配置额度 + 写 `calibrated_by`
    （与 017 采纳改部署指针同一范式）+ 追加 `tier_raised` 留痕——升级必须可追溯到记录，
    且配置的其余段与注释**逐字节不变**（改额度是配置动作，不是改代码）。
    """
    if not str(by or "").strip() or not str(reason or "").strip():
        raise CalibrationRecordError("扩量必须带操作人与理由（留痕不得无归属）")
    tier = cfg.tier(tier_id)  # 缺档即拒绝（不发明档位）
    cfg.channel(channel_id)
    new_limit = _require_amount("limit_usd", limit_usd, positive=True)
    moment = at or datetime.now(UTC)
    alerts = AlertLog(alerts_path(root, channel_id))
    previous = float(tier.limit_usd)
    if abs(new_limit - previous) < 1e-12:
        raise CalibrationRecordError(
            f"新额度与当前额度相同（{previous}）：无需改写（不制造空转留痕）"
        )
    try:
        record = require_calibration(
            calibration_id,
            cfg=cfg,
            channel_id=channel_id,
            tier_id=tier_id,
            root=root,
            at=moment,
        )
    except CalibrationRecordError as exc:
        alerts.record(
            kind="uncalibrated_raise",
            at=moment.isoformat(),
            channel_id=channel_id,
            detail=_record(
                tier_id,
                str(calibration_id),
                by,
                reason,
                moment.isoformat(),
                attempted_limit_usd=new_limit,
                previous_limit_usd=previous,
                refusal=str(exc),
            ),
            ref=str(calibration_id),
        )
        raise
    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    try:
        rewritten = replace_section_entries(
            text, ("budget", "tiers", tier_id), {"limit_usd": new_limit}
        )
    except YamlEditError as exc:
        raise CalibrationRecordError(
            f"额度定点改写失败（budget.tiers.{tier_id}.limit_usd 行形态不支持？）：{exc}"
        ) from exc
    rewritten = upsert_section_entries(
        rewritten, ("budget", "tiers", tier_id), {"calibrated_by": str(calibration_id)}
    )
    path.write_text(rewritten, encoding="utf-8")
    detail = _record(
        tier_id,
        str(calibration_id),
        by,
        reason,
        moment.isoformat(),
        previous_limit_usd=previous,
        limit_usd=new_limit,
        deviation=float(record.get("deviation", 0.0)),
        sample_count=int((record.get("measured") or {}).get("sample_count", 0)),
    )
    alerts.record(
        kind="tier_raised",
        at=moment.isoformat(),
        channel_id=channel_id,
        detail=detail,
        ref=str(calibration_id),
    )
    return {
        **detail,
        "event": "tier_raised",
        "channel_id": channel_id,
        "config_path": str(path),
    }
