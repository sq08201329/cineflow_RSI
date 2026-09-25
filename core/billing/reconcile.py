"""网关记账 vs 厂商账单的逐项对账（机制件，业务无关；契约 C13）。

五模块之一，承接"成本已核实"必须**有账单背书**的判定面（原则三）：

- 配对口径：按 `(档案 id, 周期)` 聚合逐项比对；金额以账单币种为准（异币种先按声明的 `fx` 折算）；
- **分类枚举固定六类、不增不减**（`计费口径` / `未入账` / `时序错位` / `免费额度与折扣` /
  `币种汇率` / `未结账`）；分类的**输入面**只有账单声明的驱动列（`line_kind` / `amount_sign`），
  取值 → 分类的映射由配置声明——**禁止**改用金额阈值或符号猜测推断分类；
- 每条差异必带 `classification` + `delta_usd` + `note`；缺失或取值域外 ⇒ `unclassified`
  ⇒ `unexplained` 非空 ⇒ **告警**（"无分类即不可解释"）。`|delta| ≤ amount_tolerance_usd` 视为
  零差异，但**仍须分类与备注**（零差异不等于免分类）；
- 报告必须引用账单批次与来源（`bill_refs[]`）：**网关记账禁止作"成本已核实"的唯一依据**；
  无账单 ⇒ 拒绝产出（不产"零差异"报告，否则告警门禁形同虚设）；
- 时序错位 / 未结账**不得**据此判定"网关记账有误"（留待下期对账），报告以 `accounting_note`
  显式写明这条边界。

`delta_usd = bill_usd − gateway_usd`（正 = 厂商比本仓记账多收）。报告不写盘（落盘与告警追加
属 US2 的产物面）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from core.billing.bill import load_snapshot, system_digest, write_snapshot
from core.billing.budget import AlertLog, BudgetConfig, alerts_path, channel_dir

# 分类枚举：固定六类，不增不减（FR-007 六类 + 边界情况）
CLASSIFICATIONS = (
    "计费口径",
    "未入账",
    "时序错位",
    "免费额度与折扣",
    "币种汇率",
    "未结账",
)
# 无分类 / 取值域外：一律记作未分类 ⇒ unexplained ⇒ 告警（不得静默归入某一类）
UNCLASSIFIED = "unclassified"
# 网关侧正常计费线所属的分类（网关没有折扣/挂账行的对应物，故其余分类的网关侧恒为 0）
BOOKING_CLASS = "计费口径"
REPORT_SYSTEM_FIELDS = (
    "report_id",
    "channel_id",
    "period",
    "gateway_total_usd",
    "bill_total_usd",
    "items",
    "deviates",
    "unexplained",
    "alerts",
    "thresholds_snapshot",
    "bill_refs",
    "accounting_note",
    "generated_at",
)

ACCOUNTING_NOTE = (
    "网关记账 ≠ 厂商账单：本报告的网关侧是内部折算口径，账单侧以厂商账单为准。分类为"
    "「时序错位」与「未结账」的差异属跨期/尚未结算，**不得**据此判定网关记账有误（留待下期对账）。"
    "金额为 float + 容差口径（|delta| ≤ amount_tolerance_usd 视为零差异，但仍须分类与备注）。"
)


class ReconciliationError(Exception):
    """对账错误（无账单批次、批次不属该渠道、网关账目形状不明）——拒绝产出报告。"""


@dataclass(frozen=True)
class ReconciliationItem:
    """一条差异项：每条**必带**分类与口径备注（零差异也须分类）。"""

    key: str
    classification: str
    gateway_usd: float
    bill_usd: float
    delta_usd: float
    note: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "classification": self.classification,
            "gateway_usd": self.gateway_usd,
            "bill_usd": self.bill_usd,
            "delta_usd": self.delta_usd,
            "note": self.note,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    """差异报告（一次性快照）：`report_id = (channel_id, period)`，产出即冻结。"""

    report_id: str
    channel_id: str
    period: str
    gateway_total_usd: float
    bill_total_usd: float
    items: tuple[ReconciliationItem, ...]
    deviates: bool
    unexplained: tuple[str, ...]
    alerts: tuple[str, ...]
    thresholds_snapshot: Mapping
    bill_refs: tuple[Mapping, ...]
    accounting_note: str = ACCOUNTING_NOTE
    generated_at: str = ""
    system_digest: str = ""
    overrides: tuple[Mapping, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "channel_id": self.channel_id,
            "period": self.period,
            "gateway_total_usd": self.gateway_total_usd,
            "bill_total_usd": self.bill_total_usd,
            "items": [item.to_dict() for item in self.items],
            "deviates": self.deviates,
            "unexplained": list(self.unexplained),
            "alerts": list(self.alerts),
            "thresholds_snapshot": dict(self.thresholds_snapshot),
            "bill_refs": [dict(ref) for ref in self.bill_refs],
            "accounting_note": self.accounting_note,
            "generated_at": self.generated_at,
            "system_digest": self.system_digest,
            "overrides": [dict(item) for item in self.overrides],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def has_alerts(self) -> bool:
        """告警信号（**判定全在 core**；CLI 只据此选退出码：有告警 = 1）。"""
        return bool(self.alerts) or bool(self.unexplained)


def report_path(root: str | Path, channel_id: str, period: str) -> Path:
    """差异报告路径（C4）：`billing/{channel}/reports/{period}.json`。"""
    return channel_dir(root, channel_id) / "reports" / f"{period}.json"


def load_report(period: str, *, channel_id: str, root: str | Path) -> dict:
    """读取差异报告并机检 `system_digest`（系统字段被改写即报错，不静默取）。"""
    return load_snapshot(report_path(root, channel_id, period), system_fields=REPORT_SYSTEM_FIELDS)


def save_report(report: ReconciliationReport, *, root: str | Path) -> Path:
    """报告落盘 + 告警留痕（C13/C14 的产物面）。

    - 报告：同键（渠道 + 周期）重产拒绝；`system_digest` 机检（人工批注只追加）；
    - 告警：报告的 `alerts[]` **逐条**追加到 `alerts.jsonl`（只增；`kind` 取
      `unexplained_delta` / `delta_over_threshold`）——未解释项与超阈值必须留下可追溯的痕迹，
      不能只躺在报告里；
    - 先落报告再去重写告警：报告因同键被拒时**不重复写**告警（事实不重复记账）。
    """
    path = report_path(root, report.channel_id, report.period)
    write_snapshot(path, report.to_dict(), system_fields=REPORT_SYSTEM_FIELDS)
    alerts = AlertLog(alerts_path(root, report.channel_id))
    for kind in report.alerts:
        alerts.record(
            kind=kind,
            at=report.generated_at,
            channel_id=report.channel_id,
            period=report.period,
            detail={
                "unexplained": list(report.unexplained),
                "thresholds_snapshot": dict(report.thresholds_snapshot),
                "bill_refs": [dict(ref) for ref in report.bill_refs],
            },
            ref=report.report_id,
        )
    return path


def _by_profile(gateway_ledger: Mapping) -> dict[str, Mapping]:
    """网关账目的分档案视图（接受 `cost_report()` 与 `cost_breakdown()` 两种形状）。"""
    if not isinstance(gateway_ledger, Mapping):
        raise ReconciliationError(f"网关账目必须为映射，实际为 {gateway_ledger!r}")
    direct = gateway_ledger.get("by_profile")
    if isinstance(direct, Mapping):
        return {str(key): value for key, value in direct.items()}
    merged: dict[str, dict] = {}
    for role, profiles in gateway_ledger.items():
        if not isinstance(profiles, Mapping):
            raise ReconciliationError(
                f"网关账目形状不明：{role!r} → {profiles!r}"
                "（须为 cost_report() 或 cost_breakdown() 的形状）"
            )
        for profile_id, entry in profiles.items():
            if not isinstance(entry, Mapping) or "cost_usd" not in entry:
                raise ReconciliationError(f"网关账目条目缺 cost_usd：{profile_id!r} → {entry!r}")
            bucket = merged.setdefault(str(profile_id), {"calls": 0, "cost_usd": 0.0})
            bucket["calls"] += int(entry.get("calls", 0))
            bucket["cost_usd"] += float(entry["cost_usd"])
    return merged


def _item(
    *,
    key: str,
    classification: str,
    gateway_usd: float,
    bill_usd: float,
    tolerance: float,
    alert_threshold: float,
    note: str,
) -> ReconciliationItem:
    """构造一条差异项：零差异也保留分类与备注（`|delta| ≤ 容差` 不等于免分类）。"""
    delta = bill_usd - gateway_usd
    parts = [note] if note else []
    if classification == UNCLASSIFIED:
        parts.append("unclassified：账单声明的取值域外（映射不到固定六类）⇒ 不可解释")
    if abs(delta) <= tolerance:
        parts.append("零差异（|delta| ≤ amount_tolerance_usd；仍须分类与备注）")
    elif abs(delta) > alert_threshold:
        parts.append(f"超告警阈值（|delta|={abs(delta)} > alert_threshold_usd={alert_threshold}）")
    return ReconciliationItem(
        key=key,
        classification=classification,
        gateway_usd=round(gateway_usd, 12),
        bill_usd=round(bill_usd, 12),
        delta_usd=round(delta, 12),
        note="；".join(parts),
    )


def reconcile(
    period: str,
    *,
    channel_id: str,
    gateway_ledger: Mapping,
    bill,
    cfg: BudgetConfig,
    at: datetime | None = None,
) -> ReconciliationReport:
    """逐项比对：网关账目（内部折算口径）vs 厂商账单（外部权威口径）。

    `bill` 为已规范化的 `VendorBill`；**无账单条目一律拒绝产出**——否则"无账单"就成了
    "零差异报告"的后门（告警门禁形同虚设）。
    """
    spec = cfg.channel(channel_id).bill
    if getattr(bill, "entries", None) is None:
        raise ReconciliationError("对账必须引用账单批次（缺账单即拒绝产出，不产零差异报告）")
    if not bill.entries:
        raise ReconciliationError("账单无条目（无账单即拒绝产出：「零差异报告」不得由无账单伪造）")
    if str(bill.channel_id) != str(channel_id):
        raise ReconciliationError(
            f"账单批次 {bill.bill_id!r} 属于渠道 {bill.channel_id!r}，"
            f"与本次对账渠道 {channel_id!r} 不符"
        )
    thresholds = {
        "amount_tolerance_usd": float(cfg.reconcile["amount_tolerance_usd"]),
        "alert_threshold_usd": float(cfg.reconcile["alert_threshold_usd"]),
        "unexplained_alert": bool(cfg.reconcile["unexplained_alert"]),
    }
    tolerance = thresholds["amount_tolerance_usd"]
    alert_threshold = thresholds["alert_threshold_usd"]
    gateway = _by_profile(gateway_ledger)

    groups: dict[tuple[str, str], float] = {}
    notes: dict[tuple[str, str], list[str]] = {}
    for entry in bill.entries:
        classification = spec.class_of(entry.line_kind) or UNCLASSIFIED
        sign = spec.sign_of(entry.amount_sign)
        extras = [f"账单条目 {entry.entry_id}"]
        if sign is None:
            # 符号未知：按已入账（+1）计入并判未分类——不猜成冲减、也不静默丢条目
            sign = 1.0
            classification = UNCLASSIFIED
            extras.append(
                f"符号口径未声明（amount_sign={entry.amount_sign!r}）：按已入账计入，"
                "不猜成冲减、也不丢条目"
            )
        if entry.note:
            extras.append(str(entry.note))
        if entry.currency != bill.currency:
            extras.append(f"异币种 {entry.currency!r} → {bill.currency!r} 已按 fx 折算")
        if not entry.model_ref:
            extras.append("账单未声明档案归属（无法与网关账目按档案配对）")
        bucket = (str(entry.model_ref or f"entry:{entry.entry_id}"), classification)
        groups[bucket] = groups.get(bucket, 0.0) + entry.amount_in(bill.currency) * sign
        notes.setdefault(bucket, []).extend(extras)

    items: list[ReconciliationItem] = []
    booked: set[str] = set()
    for (key, classification), bill_usd in groups.items():
        gateway_usd = 0.0
        if classification == BOOKING_CLASS:
            gateway_usd = float(gateway.get(key, {}).get("cost_usd", 0.0))
            booked.add(key)
        items.append(
            _item(
                key=key,
                classification=classification,
                gateway_usd=gateway_usd,
                bill_usd=bill_usd,
                tolerance=tolerance,
                alert_threshold=alert_threshold,
                note="；".join(notes.get((key, classification), [])),
            )
        )
    for profile_id, entry in sorted(gateway.items()):
        if profile_id in booked:
            continue
        # 网关已入账而账单无该档案的计费线 ⇒ 未入账（唯一依据仍是账单：网关记账不得自证）
        items.append(
            _item(
                key=profile_id,
                classification="未入账",
                gateway_usd=float(entry.get("cost_usd", 0.0)),
                bill_usd=0.0,
                tolerance=tolerance,
                alert_threshold=alert_threshold,
                note="网关已入账而账单无该档案的计费线（缺条目即不可核实，门禁拒绝自证）",
            )
        )
    items.sort(key=lambda item: (item.key, item.classification))
    unexplained = tuple(
        sorted(
            {
                f"{item.key}:{item.classification}"
                for item in items
                if item.classification == UNCLASSIFIED or abs(item.delta_usd) > alert_threshold
            }
        )
    )
    alerts: list[str] = []
    if any(item.classification == UNCLASSIFIED for item in items):
        alerts.append("unexplained_delta")
    if any(abs(item.delta_usd) > alert_threshold for item in items):
        alerts.append("delta_over_threshold")
    payload = {
        "report_id": f"{channel_id}:{period}",
        "channel_id": str(channel_id),
        "period": str(period),
        "gateway_total_usd": round(
            sum(float(entry.get("cost_usd", 0.0)) for entry in gateway.values()), 12
        ),
        "bill_total_usd": round(sum(item.bill_usd for item in items), 12),
        "items": [item.to_dict() for item in items],
        "deviates": any(abs(item.delta_usd) > tolerance for item in items),
        "unexplained": list(unexplained),
        "alerts": sorted(set(alerts)),
        "thresholds_snapshot": thresholds,
        "bill_refs": [bill.ref()],
        "accounting_note": ACCOUNTING_NOTE,
        "generated_at": (at or datetime.now(UTC)).isoformat(),
    }
    payload["system_digest"] = system_digest(payload, REPORT_SYSTEM_FIELDS)
    return ReconciliationReport(
        items=tuple(items),
        deviates=payload["deviates"],
        unexplained=unexplained,
        alerts=tuple(payload["alerts"]),
        thresholds_snapshot=thresholds,
        bill_refs=(bill.ref(),),
        report_id=payload["report_id"],
        channel_id=payload["channel_id"],
        period=payload["period"],
        gateway_total_usd=payload["gateway_total_usd"],
        bill_total_usd=payload["bill_total_usd"],
        accounting_note=payload["accounting_note"],
        generated_at=payload["generated_at"],
        system_digest=payload["system_digest"],
    )
