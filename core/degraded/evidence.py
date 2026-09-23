"""判据材料（降级模式机制件，业务无关）：全量阈值快照 + 逐项可评价性 + 系统结论留痕。

产出 `{data_dir}/{agent_id}/{period}.json`（**按 agent 分目录**：否则两个降级 Agent 同
ISO 周写入同一路径会互相覆盖，审计证据失效）：

- **判据项** = 键 + 阈值键 + 提供者可调用；逐项取值形态 = **实测值** 或
  **`无法评价（来源缺失）` + 缺失原因**——**无空白、无省略**（阈值缺任一项即报错，
  不允许静默"无判据"）；
- 阈值快照随材料冻结（不可变快照，含当时阈值）；材料落盘后配置变更不影响已产材料；
- **系统字段不可改写**：`system_digest` 内容哈希机检，手工改写系统字段即拒绝（不覆盖篡改、
  不放行）；结论必须落在调用方声明的取值域内（`达标` 等取值域外的值一律拒绝）；
- 人工推翻**只追加**留痕（人/时间/理由），系统字段逐字节不变；材料 append-only：
  同周期已存在即拒绝重产。

判定口径（结论取值域与措辞）属**业务件**，由调用方以 `judge` 可调用注入；本模块只负责
机制面（阈值校验、逐项取值、落盘纪律、完整性机检）。
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import blake3

# 判据项取值形态（FR-010：每项必有取值形态，不得留空或省略）
MEASURED = "实测值"
MISSING_SOURCE = "无法评价（来源缺失）"
# 结论取值域（默认三态；调用方可收窄——例如声明为不含"达标"的取值域）
CONCLUSIONS = ("meets", "below", "insufficient")
# 系统字段（系统自动写入、人工只可推翻不可改写；内容哈希的覆盖面）
_SYSTEM_FIELDS = (
    "period",
    "agent_id",
    "threshold_snapshot",
    "raw",
    "items",
    "conclusion",
    "reasons",
    "alerts",
    "human_anchor_count",
    "created_at",
)


class UpgradeEvidenceError(Exception):
    """判据材料错误（阈值缺失/判据项不合法/材料已存在/完整性校验失败/留痕缺字段）。"""


@dataclass(frozen=True)
class ItemValue:
    """判据项取值（提供者回传）：实测值（+ 随值入 `raw` 的口径字段）或无法评价（+ 缺失原因）。"""

    value: float | int | str | None = None
    extra: dict = field(default_factory=dict)
    missing_reason: str = ""


@dataclass(frozen=True)
class EvidenceItem:
    """判据项（材料中的一项）：键 + 阈值键（无阈值的观测项为 None）+ 取值 + 缺失原因。"""

    key: str
    threshold_key: str | None
    status: str  # MEASURED | MISSING_SOURCE
    value: float | int | str | None
    missing_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class UpgradeEvidence:
    """判据材料（快照）：全量阈值 + 逐项取值 + 系统结论 + 告警 + 推翻记录。"""

    period: str
    agent_id: str
    threshold_snapshot: dict
    raw: dict
    conclusion: str
    reasons: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    human_anchor_count: int = 0
    created_at: str = ""
    system_digest: str = ""
    overrides: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def measured(value: float | int | str, **extra) -> ItemValue:
    """实测值（`extra` 为随该值一并入 `raw` 的口径字段，如度量名/口径说明）。"""
    return ItemValue(value=value, extra=dict(extra))


def unavailable(reason: str, **extra) -> ItemValue:
    """无法评价（来源缺失）：必须给出缺失原因（不留空；`extra` 口径字段同样入 `raw`）。"""
    return ItemValue(value=None, extra=dict(extra), missing_reason=reason)


def system_digest(payload: Mapping) -> str:
    """系统写入部分的内容哈希（推翻不改写；手工篡改即机检失败）。"""
    canonical = json.dumps(
        {key: payload.get(key) for key in _SYSTEM_FIELDS}, ensure_ascii=False, sort_keys=True
    )
    return blake3.blake3(canonical.encode()).hexdigest()


def _require_items(items: Sequence[Mapping]) -> tuple[Mapping, ...]:
    """判据项校验：非空、键唯一、每项含 provider 可调用（缺项即报错，不静默无判据）。"""
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)) or not items:
        raise UpgradeEvidenceError("判据项不能为空（全量声明阈值项，不得静默无判据）")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or not item.get("key"):
            raise UpgradeEvidenceError(f"判据项必须为含 key 的映射，实际为 {item!r}")
        key = str(item["key"])
        if key in seen:
            raise UpgradeEvidenceError(f"判据项键重复：{key!r}（每项取值形态唯一）")
        seen.add(key)
        if not callable(item.get("provider")):
            raise UpgradeEvidenceError(f"判据项 {key!r} 缺少 provider 可调用（取值来源必填）")
    return tuple(items)


def _require_snapshot(cfg, items: tuple[Mapping, ...], snapshot: Mapping | None) -> dict:
    """阈值快照：全量声明（缺任一项即报错），调用方给定则原样冻结。"""
    threshold_keys = [item.get("threshold_key") for item in items if item.get("threshold_key")]
    if snapshot is None:
        criteria = getattr(cfg, "upgrade_criteria", None)
        if not isinstance(criteria, Mapping):
            raise UpgradeEvidenceError(
                "升级判据阈值缺失：形态配置无 upgrade_criteria（不允许静默无判据）"
            )
        result: dict = {}
        for key in threshold_keys:
            if criteria.get(key) is None:
                raise UpgradeEvidenceError(f"升级判据阈值缺失：upgrade_criteria.{key}")
            result[key] = criteria[key]
        return result
    if not isinstance(snapshot, Mapping):
        raise UpgradeEvidenceError(f"阈值快照必须为映射，实际为 {snapshot!r}")
    for key in threshold_keys:
        if snapshot.get(key) is None:
            raise UpgradeEvidenceError(f"升级判据阈值缺失：阈值快照缺 {key}")
    return dict(snapshot)


def _measure(item: Mapping) -> tuple[EvidenceItem, dict]:
    """执行一个判据项的提供者：实测值或无法评价（来源缺失）+ 缺失原因（无空白项）。"""
    key = str(item["key"])
    threshold_key = item.get("threshold_key")
    value = item["provider"]()
    if not isinstance(value, ItemValue):
        raise UpgradeEvidenceError(
            f"判据项 {key!r} 的 provider 必须回传 ItemValue（measured/unavailable），"
            f"实际为 {value!r}"
        )
    if value.value is None:
        if not str(value.missing_reason or "").strip():
            raise UpgradeEvidenceError(f"判据项 {key!r} 取不到数但未登记缺失原因（不得留空项）")
        return (
            EvidenceItem(
                key=key,
                threshold_key=threshold_key,
                status=MISSING_SOURCE,
                value=None,
                missing_reason=str(value.missing_reason),
            ),
            {key: None, **value.extra},
        )
    if str(value.missing_reason or "").strip():
        raise UpgradeEvidenceError(f"判据项 {key!r} 实测值与缺失原因不得同时声明（口径互斥）")
    return (
        EvidenceItem(key=key, threshold_key=threshold_key, status=MEASURED, value=value.value),
        {key: value.value, **value.extra},
    )


def build_upgrade_evidence(
    period: str,
    cfg,
    items: Sequence[Mapping],
    *,
    agent_id: str,
    data_dir: str | Path,
    judge: Callable[..., tuple[str, list[str], list[str]]],
    snapshot: Mapping | None = None,
    conclusions: Sequence[str] = CONCLUSIONS,
    human_anchor_count: int = 0,
) -> UpgradeEvidence:
    """生成周期判据材料（不可变快照：同周期已存在即拒绝）。

    items：判据项序列（`{key, threshold_key, provider}`；无阈值的观测项 threshold_key 为 None）；
    judge：判定口径可调用 `(snapshot, raw, items) -> (conclusion, reasons, alerts)`（业务件注入）；
    snapshot：阈值快照（缺省从 `cfg.upgrade_criteria` 按各判据项的阈值键全量取，缺即报错）。
    """
    if not isinstance(period, str) or not period:
        raise UpgradeEvidenceError("period 必须为非空字符串（如 2026-W38）")
    definitions = _require_items(items)
    frozen = _require_snapshot(cfg, definitions, snapshot)
    measured_items: list[EvidenceItem] = []
    raw: dict = {}
    for item in definitions:
        record, fragment = _measure(item)
        measured_items.append(record)
        raw.update(fragment)
    conclusion, reasons, alerts = judge(frozen, raw, tuple(measured_items))
    if conclusion not in conclusions:
        raise UpgradeEvidenceError(
            f"系统结论必须为 {list(conclusions)} 之一，实际为 {conclusion!r}"
            "（取值域外的结论一律拒绝：不得暗示可升级）"
        )
    payload = {
        "period": period,
        "agent_id": agent_id,
        "threshold_snapshot": frozen,
        "raw": raw,
        "items": [item.to_dict() for item in measured_items],
        "conclusion": conclusion,
        "reasons": list(reasons),
        "alerts": list(alerts),
        "human_anchor_count": int(human_anchor_count),
        "created_at": datetime.now(UTC).isoformat(),
        "overrides": [],
    }
    payload["system_digest"] = system_digest(payload)
    target = _material_path(data_dir, agent_id, period)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise UpgradeEvidenceError(
            f"判据材料已存在（不可变快照，只增不改）：{target}"
            "（如需推翻结论请用 override_conclusion）"
        )
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return UpgradeEvidence(**payload)


def _material_path(data_dir: str | Path, agent_id: str, period: str) -> Path:
    """材料路径（按 agent 分目录；不读旧路径——避免同周期两份材料的二义性）。"""
    return Path(data_dir) / agent_id / f"{period}.json"


def load_evidence(period: str, *, agent_id: str, data_dir: str | Path) -> dict:
    """读取周期判据材料（不存在即报错，不静默返回空）。"""
    path = _material_path(data_dir, agent_id, period)
    if not path.is_file():
        raise UpgradeEvidenceError(f"判据材料不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def override_conclusion(
    period: str,
    *,
    by: str,
    reason: str,
    agent_id: str,
    data_dir: str | Path,
) -> UpgradeEvidence:
    """人推翻系统结论：追加留痕（人/时间/理由）——**系统结论字段逐字节不变**。

    快照完整性机检（`system_digest`）：系统字段被手工改写即拒绝（不覆盖篡改，也不放行）。
    """
    if not isinstance(by, str) or not by:
        raise UpgradeEvidenceError("推翻人（by）不能为空（留痕必填）")
    if not isinstance(reason, str) or not reason.strip():
        raise UpgradeEvidenceError("推翻理由（reason）不能为空（留痕必填）")
    path = _material_path(data_dir, agent_id, period)
    if not path.is_file():
        raise UpgradeEvidenceError(f"判据材料不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.pop("system_digest", None) != system_digest(payload):
        raise UpgradeEvidenceError(
            f"判据材料完整性校验失败：{path} 的系统字段被改写（不得篡改、不得覆盖）"
        )
    payload["overrides"] = [
        *payload.get("overrides", []),
        {"by": by, "reason": reason, "at": datetime.now(UTC).isoformat()},
    ]
    payload["system_digest"] = system_digest(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return UpgradeEvidence(**payload)
