"""账单规范化 + 导入器注册表 + 一次性快照纪律（机制件，业务无关；契约 C1/C2/C3）。

五模块之一，承接"账单进来必须先规范化"的机制面：

- `normalize_bill(raw, ...) -> VendorBill`：格式由 `fmt` 声明（**不假设统一格式**），解析器从
  注册表取（内置通用键仅 `csv_lines` / `json_lines`；未注册格式 ⇒ `UnknownBillFormatError`，
  **报错而非静默跳过**）；列映射由配置声明，**必填** `entry_id` / `amount` / `currency` /
  `period` / `line_kind` / `amount_sign`（分类驱动列），异币种**条件必填** `fx_rate` /
  `fx_source` / `fx_at`——任一缺失即导入报错，**禁止**改用金额阈值或符号猜测；
- **全成或全败**：任一行非法 ⇒ 整体拒绝、零落盘（不"部分导入后谎报成功"）；
- **批次身份** `(channel_id, bill_id)`：同批次重复导入由 `save_bill` 拒绝（append-only）；
- **一次性快照纪律**（`system_digest` / `write_snapshot` / `load_snapshot` / `append_override`）：
  系统字段内容哈希机检、同键拒重产、人工批注只追加（镜像 `core/degraded/evidence.py` 的范式）。
  该 helper 置于本模块供 `reconcile.py` / `calibration.py` 复用——C1 限定五模块，
  不新增第六个模块。
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import blake3

from core.billing.budget import channel_dir

# 列映射必填项（分类驱动列；缺声明即导入报错，不得改用金额启发式）
BILL_FIELDS = ("entry_id", "amount", "currency", "period", "line_kind", "amount_sign")
# 异币种条件必填项（账单币种 ≠ 记账币种时）
FX_FIELDS = ("fx_rate", "fx_source", "fx_at")
BILL_SOURCES = ("export", "api")
# 注册表内置通用格式键（C2；`core/billing/` 内唯一允许出现的格式字面量，白名单常驻断言见
# tests/unit/test_billing_core_purity.py）
BUILTIN_FORMAT_IDS = ("csv_lines", "json_lines")
# 账单导入记录的系统字段（内容哈希覆盖面；人工批注只追加、不改这些字段）
BILL_SYSTEM_FIELDS = (
    "channel_id",
    "bill_id",
    "period",
    "currency",
    "source",
    "raw_ref",
    "fetched_at",
    "format_id",
    "entries",
)


class BillError(Exception):
    """账单面错误基类。"""


class UnknownBillFormatError(BillError):
    """未注册的账单格式 id（报错而非静默跳过）。"""


class BillImportError(BillError):
    """账单导入错误（缺列/字段非法/条目重复/币种与汇率口径不齐）——整体拒绝、零落盘。"""


class SnapshotIntegrityError(BillError):
    """一次性快照错误（已存在拒重产、系统字段被改写、批注缺失字段）。"""


@dataclass(frozen=True)
class BillEntry:
    """账单条目：`entry_id`（账单内唯一）+ 非负 `amount` + 分类驱动列 + 异币种 `fx`。"""

    entry_id: str
    amount: float
    currency: str
    period: str
    line_kind: str
    amount_sign: str
    model_ref: str = ""
    fx: Mapping = field(default_factory=dict)
    usage: Mapping = field(default_factory=dict)
    note: str = ""

    def amount_in(self, currency: str) -> float:
        """该条目在目标币种下的金额（异币种按声明的 `fx.rate` 折算；同币种原样）。"""
        if self.currency == currency:
            return self.amount
        rate = self.fx.get("rate")
        if rate is None:
            raise BillImportError(
                f"条目 {self.entry_id!r} 币种 {self.currency!r} ≠ 记账币种 {currency!r} "
                "且无 fx 汇率（不猜汇率）"
            )
        return self.amount * float(rate)

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "amount": self.amount,
            "currency": self.currency,
            "period": self.period,
            "line_kind": self.line_kind,
            "amount_sign": self.amount_sign,
            "model_ref": self.model_ref,
            "fx": dict(self.fx),
            "usage": dict(self.usage),
            "note": self.note,
        }


@dataclass(frozen=True)
class VendorBill:
    """厂商账单批次（一次性快照）：来源/批次/币种/周期/原始行摘要/条目全留痕。"""

    channel_id: str
    bill_id: str
    period: str
    currency: str
    source: str
    raw_ref: str
    fetched_at: str
    format_id: str
    entries: tuple[BillEntry, ...]
    system_digest: str = ""
    overrides: tuple[Mapping, ...] = ()

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "bill_id": self.bill_id,
            "period": self.period,
            "currency": self.currency,
            "source": self.source,
            "raw_ref": self.raw_ref,
            "fetched_at": self.fetched_at,
            "format_id": self.format_id,
            "entries": [entry.to_dict() for entry in self.entries],
            "system_digest": self.system_digest,
            "overrides": [dict(item) for item in self.overrides],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def ref(self) -> dict:
        """`bill_refs[]` 的一项（网关记账不得作"成本已核实"的唯一依据）。"""
        return {
            "bill_id": self.bill_id,
            "source": self.source,
            "raw_ref": self.raw_ref,
            "fetched_at": self.fetched_at,
            "currency": self.currency,
        }


class BillImporter(Protocol):
    """账单导入器：`format_id` + 把原始文本按**列映射**解析为语义字段行。

    只做"原始行 → 语义字段行"；字段校验与条目构造是全格式共用的（`normalize_bill`），
    故新增渠道格式只需注册一个解析器，校验口径不会分叉。
    """

    format_id: str

    def parse(self, raw: str | bytes, *, columns: Mapping[str, str]) -> Sequence[Mapping]: ...


_REGISTRY: dict[str, BillImporter] = {}


def register_importer(format_id: str, parser: BillImporter) -> None:
    """注册格式 id → 导入器（装配方为渠道专有格式注册；内置通用键见 `BUILTIN_FORMAT_IDS`）。"""
    if not isinstance(format_id, str) or not format_id.strip():
        raise BillImportError("格式 id 必须为非空字符串")
    if not callable(getattr(parser, "parse", None)):
        raise BillImportError(f"导入器 {format_id!r} 必须实现 parse(raw, *, columns)")
    _REGISTRY[format_id] = parser


def importer_for(format_id: str) -> BillImporter:
    """取导入器：未注册 ⇒ `UnknownBillFormatError`（报错而非静默跳过）。"""
    parser = _REGISTRY.get(str(format_id))
    if parser is None:
        raise UnknownBillFormatError(
            f"未注册的账单格式 id：{format_id!r}（已注册：{sorted(_REGISTRY)}）——"
            "报错而非静默跳过；渠道专有格式由装配方 register_importer 注册"
        )
    return parser


def builtin_format_ids() -> tuple[str, ...]:
    """注册表内置通用格式键（是 `core/billing/` 内唯一允许的格式字面量）。"""
    return BUILTIN_FORMAT_IDS


def _decode(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if not isinstance(raw, str):
        raise BillImportError(f"账单原始内容必须为 str 或 bytes，实际为 {type(raw).__name__}")
    return raw


class _CsvLinesImporter:
    """逐行 CSV（首行为列名；列名由 `columns` 声明，缺列即报错）。"""

    format_id = "csv_lines"

    def parse(self, raw: str | bytes, *, columns: Mapping[str, str]) -> list[Mapping]:
        reader = csv.DictReader(io.StringIO(_decode(raw)))
        header = reader.fieldnames or []
        missing = sorted({name for name in columns.values() if name not in header})
        if missing:
            raise BillImportError(f"账单缺列 {missing}（列映射声明的列必须存在，不得靠猜列）")
        rows: list[Mapping] = []
        for payload in reader:
            if payload is None:
                continue
            rows.append(
                {
                    field_name: (payload.get(column) or "").strip()
                    for field_name, column in columns.items()
                }
            )
        return rows


class _JsonLinesImporter:
    """逐行 JSON（每行一个对象；键名由 `columns` 声明）。"""

    format_id = "json_lines"

    def parse(self, raw: str | bytes, *, columns: Mapping[str, str]) -> list[Mapping]:
        rows: list[Mapping] = []
        for number, line in enumerate(_decode(raw).splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BillImportError(f"第 {number} 行不是合法 JSON：{exc}") from exc
            if not isinstance(payload, Mapping):
                raise BillImportError(f"第 {number} 行必须为 JSON 对象，实际为 {payload!r}")
            rows.append({field_name: payload.get(column) for field_name, column in columns.items()})
        return rows


register_importer(BUILTIN_FORMAT_IDS[0], _CsvLinesImporter())
register_importer(BUILTIN_FORMAT_IDS[1], _JsonLinesImporter())


def _as_text(value: object, *, where: str, required: bool) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        raise BillImportError(f"{where} 不得为布尔值（实际 {value!r}）：口径栏目必须为文本")
    else:
        text = str(value).strip()
    if required and not text:
        raise BillImportError(f"{where} 缺失（必填栏目；缺声明即导入报错，不得靠猜）")
    return text


def _as_amount(value: object, *, where: str) -> float:
    """金额校验：非负、非 bool（镜像 `core/platform_http.py` 的金额纪律）。"""
    if isinstance(value, bool):
        raise BillImportError(f"{where} 不得为布尔值（实际 {value!r}）")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError as exc:
            raise BillImportError(f"{where} 非合法数值（实际 {value!r}）") from exc
    else:
        raise BillImportError(f"{where} 缺失或非数值（实际 {value!r}）")
    if not math.isfinite(number) or number < 0:
        raise BillImportError(f"{where} 必须为非负有限数值（实际 {value!r}）——金额不得为负或 NaN")
    return number


def _entry_of(row: Mapping, *, currency: str, where: str) -> BillEntry:
    """语义字段行 → 条目（含异币种条件必填校验；任一项非法即整体拒绝）。"""
    entry_id = _as_text(row.get("entry_id"), where=f"{where}.entry_id", required=True)
    entry_currency = _as_text(row.get("currency"), where=f"{where}.currency", required=True)
    fields = {
        "entry_id": entry_id,
        "amount": _as_amount(row.get("amount"), where=f"{where}.amount"),
        "currency": entry_currency,
        "period": _as_text(row.get("period"), where=f"{where}.period", required=True),
        "line_kind": _as_text(row.get("line_kind"), where=f"{where}.line_kind", required=True),
        "amount_sign": _as_text(
            row.get("amount_sign"), where=f"{where}.amount_sign", required=True
        ),
        "model_ref": _as_text(row.get("model_ref"), where=f"{where}.model_ref", required=False),
        "note": _as_text(row.get("note"), where=f"{where}.note", required=False),
    }
    fx: dict = {}
    if entry_currency != currency:
        rate = _as_amount(row.get("fx_rate"), where=f"{where}.fx_rate")
        if rate <= 0:
            raise BillImportError(f"{where}.fx_rate 必须 > 0（实际 {rate!r}）")
        fx = {
            "from": entry_currency,
            "to": currency,
            "rate": rate,
            "source": _as_text(row.get("fx_source"), where=f"{where}.fx_source", required=True),
            "at": _as_text(row.get("fx_at"), where=f"{where}.fx_at", required=True),
        }
    usage = {
        key: row[key]
        for key in ("prompt_tokens", "completion_tokens", "calls")
        if row.get(key) not in (None, "")
    }
    return BillEntry(fx=fx, usage=usage, **fields)


def normalize_bill(
    raw: str | bytes,
    *,
    channel_id: str,
    bill_id: str,
    period: str,
    currency: str,
    source: str,
    fmt: str,
    columns: Mapping[str, str],
    importer: BillImporter | None = None,
    fetched_at: str = "",
) -> VendorBill:
    """规范化一批账单：全成或全败（任一行非法 ⇒ 整体拒绝，**零落盘**）。

    `currency` 为**记账币种**（报告与账本口径）；条目自带币种，异币种按声明的 fx 折算。
    """
    if not str(channel_id or "").strip() or not str(bill_id or "").strip():
        raise BillImportError("批次身份必填：channel_id 与 bill_id 都不得为空")
    if source not in BILL_SOURCES:
        raise BillImportError(f"来源形态必须 ∈ {list(BILL_SOURCES)}，实际为 {source!r}")
    if not str(period or "").strip():
        raise BillImportError("period 必填（批次周期，键之一）")
    for field_name in BILL_FIELDS:
        if not columns.get(field_name):
            raise BillImportError(
                f"列映射缺少 {field_name!r}（必填驱动列；缺声明即导入报错，"
                "不得改用金额阈值或符号猜测）"
            )
    parser = importer if importer is not None else importer_for(fmt)
    if getattr(parser, "format_id", None) != fmt:
        raise BillImportError(
            f"导入器格式 {getattr(parser, 'format_id', None)!r} 与声明的格式 {fmt!r} 不一致"
        )
    rows = parser.parse(raw, columns=columns)
    if not rows:
        raise BillImportError("账单无条目（空批次导入无意义，拒绝）")
    entries = tuple(
        _entry_of(row, currency=str(currency), where=f"第 {index} 行")
        for index, row in enumerate(rows, 1)
    )
    seen: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen:
            raise BillImportError(f"条目 id 在账单内重复：{entry.entry_id!r}（同一批次不得重号）")
        seen.add(entry.entry_id)
    payload = {
        "channel_id": str(channel_id),
        "bill_id": str(bill_id),
        "period": str(period),
        "currency": str(currency),
        "source": source,
        "raw_ref": blake3.blake3(_decode(raw).encode("utf-8")).hexdigest(),
        "fetched_at": fetched_at or datetime.now(UTC).isoformat(),
        "format_id": str(fmt),
        "entries": [entry.to_dict() for entry in entries],
    }
    payload["system_digest"] = system_digest(payload, BILL_SYSTEM_FIELDS)
    return VendorBill(
        entries=entries, **{key: value for key, value in payload.items() if key != "entries"}
    )


def bill_path(root: str | Path, channel_id: str, bill_id: str) -> Path:
    """账单记录路径（C4）：`billing/{channel}/bills/{bill_id}.json`。"""
    return channel_dir(root, channel_id) / "bills" / f"{bill_id}.json"


def save_bill(bill: VendorBill, *, root: str | Path) -> Path:
    """导入记录落盘：同批次已存在即拒绝（批次幂等，append-only）。"""
    path = bill_path(root, bill.channel_id, bill.bill_id)
    write_snapshot(path, bill.to_dict(), system_fields=BILL_SYSTEM_FIELDS)
    return path


def load_bill(bill_id: str, *, channel_id: str, root: str | Path) -> dict:
    """读取导入记录（`system_digest` 机检；被改写即报错，不静默取）。"""
    return load_snapshot(bill_path(root, channel_id, bill_id), system_fields=BILL_SYSTEM_FIELDS)


# ---------------------------------------------------------------------------
# 一次性快照纪律（C3；供本模块与 reconcile/calibration 复用）
# ---------------------------------------------------------------------------


def system_digest(payload: Mapping, system_fields: Sequence[str]) -> str:
    """系统写入部分的内容哈希（BLAKE3 of canonical JSON）：人工批注不改写它。"""
    canonical = json.dumps(
        {field_name: payload.get(field_name) for field_name in system_fields},
        ensure_ascii=False,
        sort_keys=True,
    )
    return blake3.blake3(canonical.encode("utf-8")).hexdigest()


def write_snapshot(path: str | Path, payload: Mapping, *, system_fields: Sequence[str]) -> dict:
    """一次性快照落盘：**路径已存在即拒绝**（append-only，不覆盖、不重产）。"""
    path = Path(path)
    body = {key: value for key, value in payload.items() if key != "system_digest"}
    body["system_digest"] = system_digest(body, system_fields)
    if path.exists():
        raise SnapshotIntegrityError(
            f"快照已存在（不可变快照，只增不改）：{path}（如需批注请用 append_override）"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return body


def load_snapshot(path: str | Path, *, system_fields: Sequence[str]) -> dict:
    """读取快照并机检 `system_digest`：系统字段被改写即报错（不静默取篡改值）。"""
    path = Path(path)
    if not path.is_file():
        raise SnapshotIntegrityError(f"快照不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = payload.get("system_digest")
    if recorded != system_digest(payload, system_fields):
        raise SnapshotIntegrityError(
            f"快照完整性校验失败：{path} 的系统字段被改写（不得篡改、不得覆盖）"
        )
    return payload


def append_override(
    path: str | Path,
    *,
    by: str,
    reason: str,
    at: str,
    system_fields: Sequence[str],
) -> dict:
    """人工批注**只追加**：先机检系统字段，再追加 `overrides`（系统字段逐字节不变）。"""
    path = Path(path)
    if not str(by or "").strip() or not str(reason or "").strip():
        raise SnapshotIntegrityError("人工批注必须带批注人与理由（留痕不得无归属）")
    payload = load_snapshot(path, system_fields=system_fields)
    payload["overrides"] = [
        *payload.get("overrides", []),
        {"by": str(by), "reason": str(reason), "at": str(at)},
    ]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload
