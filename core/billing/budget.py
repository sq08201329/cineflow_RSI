"""预算门禁与跨进程账本（机制件，业务无关；功能 019 / 契约 C1 / C3 / C9 / C11）。

五模块之一，承接"花得起、拒得掉"的机制面：

- `BudgetConfig.from_yaml` / `from_dict`：`budget:` 段**全量校验**（缺项即报错、不取码内默认）；
  `BudgetTier`（余量 = `limit − spent − reserved`）、`PeakWindows`（**闭开区间 `[start, end)`**、
  支持跨夜、按**调用开始时刻**归属）、`ChannelSpec`（渠道登记 + 账单列映射）；
- `FileLedger`：**单主机跨进程**账本（`fcntl.flock(LOCK_EX)` → 读 → 改 → `os.replace` 原子替换，
  每写 `revision += 1` 单调）。余量、预留与拒绝计数**全落在文件**——"额度校验不得只在进程内"；
  锁超时 ⇒ `BudgetLedgerError` 拒绝调用（不无锁写、不静默放行）；崩溃残留的未结算预留**如实呈现**、
  不静默清零（处置由运营决定）；
- `SpendGuard.check(request) -> Reservation` + `Reservation.settle(actual_usd)`：预留—结算两段，
  使并发不超额（`check` 时同事务占额，`settle` 时按实测结清，实测超预估 ⇒ 余量可为负 +
  `over_limit` 告警）；
- `AlertLog`：`alerts.jsonl` 只增写手（`kind` 六值取值域）——"可变余量 + 只增事实"双轨的
  **只增一轨**，审计不依赖内存。

**依赖方向**（原则五）：`core/billing → core/llm_gateway`（单向，取 `GatewayError` 作拒绝的
分型基准）。网关侧的请求对象**结构化**接入（只读 `channel_id` / `stage` / `estimated_usd`）：
本包不 import 网关侧的请求类型（那是**网关**的类型），故 `SpendRequest` 在此是**协议**——
网关的请求 dataclass 天然满足它，两侧各自独立可测。

**布局与告警的落点（判断调用，如实登记）**：C1 限定五模块，故渠道目录的路径拼接口径
（`billing/{channel}/…`）与只增告警留痕一并落在本模块——账本与告警都是门禁的直接伴生件，
单一来源胜过五处各写一份。

**边界（如实登记）**：本账本为**单主机多进程安全**；多主机并发共享额度需换 PG 行锁/事务
（**未做**），不得据此声称跨主机安全。金额沿用仓库既有 float + 容差口径（不做 Decimal 重构）。
"""

from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import yaml

from core.llm_gateway.gateway import GatewayError

REPO_ROOT = Path(__file__).resolve().parents[2]

# 时间窗取值域（C9：day 的日历取 peak_windows.timezone，不另立时区键）
WINDOW_KINDS = ("run", "day", "period")
# 额度耗尽语义：取值域单元素——拒绝（不排队、不降级为模拟）
ON_EXHAUSTED_REFUSE = "refuse"
# 峰谷归属：取值域单元素（按调用开始时刻归属，跨峰谷不拆分）
ATTRIBUTION_CALL_START = "call_start"
# 告警取值域（C14：六值固定，不增不减）
ALERT_KINDS = (
    "budget_refused",
    "over_limit",
    "unexplained_delta",
    "delta_over_threshold",
    "tier_raised",
    "uncalibrated_raise",
)
LEDGER_FILENAME = "ledger.json"
LEDGER_LOCK_SUFFIX = ".lock"
ALERTS_FILENAME = "alerts.jsonl"
# 限额判定容差（float 尾差不构成"超额"；口径同源对账的 1e-9 二分）
AMOUNT_EPSILON = 1e-9
_LOCK_POLL_SECONDS = 0.01
_EMPTY_LEDGER: dict = {"revision": 0, "updated_at": "", "tiers": {}}


class BudgetConfigError(Exception):
    """`budget:` 段配置错误（缺项、取值非法、单元素取值域外）——不静默取码内默认。"""


class BudgetLedgerError(Exception):
    """账本错误（锁超时、预留不存在或重复结算、窗口实例缺失）——拒绝调用，不静默放行。"""


class BudgetRefusedError(GatewayError):
    """预算门禁拒绝（**本系统**门禁，与厂商 401/429 分型互斥）。

    `GatewayError` 子类：调用方可统一捕获；但按契约 C10 的**调用点分支规则**，拒绝必须
    **先于**通用失败分支捕获并走零成本分支（被拒的那一笔不入账）。网关侧 `call_count` /
    `total_cost_usd` / `_breakdown` 均不变。

    三处可辨：错误类型（本类） + 告警 `kind=budget_refused` + 调用点失败原因点名"预算拒绝"。
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        tier_id: str = "",
        remaining_usd: float | None = None,
        estimated_usd: float | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.tier_id = tier_id
        self.remaining_usd = remaining_usd
        self.estimated_usd = estimated_usd


class SpendRequest(Protocol):
    """门禁请求的**结构化**口径（字段由网关侧的请求对象提供，本包不 import 它）。

    `estimated_usd` 由**网关**填（既有成本上界估算的唯一实现在网关侧，guard 不自行估算，
    避免两套口径）；`stage` 为**调用点声明**的环节 id（缺声明 ⇒ 拒绝 `tier_undeclared`，
    不静默归入默认档）。
    """

    channel_id: str
    stage: str
    estimated_usd: float


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PeakWindow:
    """峰时区间：`[start, end)` 闭开，支持跨夜（如 `08:30–00:30`）。"""

    start: str
    end: str

    def to_snapshot(self) -> dict:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class PeakWindows:
    """峰谷时段：**渠道日历的唯一来源**（峰谷判定 + 额度 day 窗口 + 运行记录日期共用它）。

    一次调用只取一个格位（按 `attribution` 声明的**调用开始时刻**归属，跨峰谷切换不拆分）。
    """

    timezone: str
    attribution: str
    windows: tuple[PeakWindow, ...] = ()

    def to_snapshot(self) -> dict:
        return {
            "timezone": self.timezone,
            "attribution": self.attribution,
            "windows": [window.to_snapshot() for window in self.windows],
        }

    def local_date(self, moment: datetime) -> str:
        """本地日期（`YYYY-MM-DD`）：运行记录 `{date}`、额度 day 窗口与峰谷判定同一日历。"""
        return _localize(moment, self.timezone).strftime("%Y-%m-%d")

    def is_peak(self, moment: datetime) -> bool:
        """是否落在峰时区间（闭开 `[start, end)`，支持跨夜；`windows` 为空 = 全谷时）。"""
        clock = _localize(moment, self.timezone).time()
        for window in self.windows:
            start = _parse_clock(window.start, "budget.peak_windows.windows.start")
            end = _parse_clock(window.end, "budget.peak_windows.windows.end")
            inside = start <= clock < end if start < end else (clock >= start or clock < end)
            if inside:
                return True
        return False


@dataclass(frozen=True)
class BudgetTier:
    """预算档（身份 = `(channel_id, tier_id, 窗口实例)`；余量由跨进程账本持有）。"""

    tier_id: str
    limit_usd: float
    window_kind: str
    note: str = ""
    calibrated_by: str = ""

    def remaining_usd(self, *, spent_usd: float, reserved_usd: float) -> float:
        """余量 = `limit − spent − reserved`（可为负：实测超预估如实呈现，不回滚、不清零）。"""
        return self.limit_usd - spent_usd - reserved_usd

    def to_snapshot(self) -> dict:
        return {
            "tier_id": self.tier_id,
            "limit_usd": self.limit_usd,
            "window": {"kind": self.window_kind},
            "on_exhausted": ON_EXHAUSTED_REFUSE,
            "note": self.note,
            "calibrated_by": self.calibrated_by,
        }


@dataclass(frozen=True)
class ChannelBillSpec:
    """渠道账单导入面：格式 id + 来源形态 + 列映射 + 分类驱动列的取值域。

    `columns`（语义字段 → 账单列名）与 `classification`（驱动列取值 → 六类/符号）都由**配置声明**：
    分类的输入面必须是声明的列，**禁止**改用金额阈值或符号猜测推断分类（C2/C13）。
    """

    format_id: str
    fetch: str
    columns: Mapping[str, str]
    line_kind_classes: Mapping[str, str]
    amount_signs: Mapping[str, float]

    def sign_of(self, token: str) -> float | None:
        """符号取值域内的符号；域外 ⇒ None（由对账判 `unclassified`，不猜符号、不静默丢弃）。"""
        return self.amount_signs.get(str(token))

    def class_of(self, token: str) -> str | None:
        """`line_kind` 取值域内的固定六类之一；域外 ⇒ None（⇒ `unclassified` ⇒ 告警）。"""
        return self.line_kind_classes.get(str(token))

    def to_snapshot(self) -> dict:
        return {
            "format": self.format_id,
            "fetch": self.fetch,
            "columns": dict(self.columns),
            "classification": {
                "line_kind": dict(self.line_kind_classes),
                "amount_sign": dict(self.amount_signs),
            },
        }


@dataclass(frozen=True)
class ChannelSpec:
    """渠道登记：id + 真实调用面的装配引用 + 账单导入面。"""

    channel_id: str
    adapter: str
    bill: ChannelBillSpec

    def to_snapshot(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "adapter": self.adapter,
            "bill": self.bill.to_snapshot(),
        }


@dataclass(frozen=True)
class BudgetConfig:
    """`budget:` 段配置（冻结快照；改额度只影响此后新装配，历史节点不变）。"""

    channels: Mapping[str, ChannelSpec]
    tiers: Mapping[str, BudgetTier]
    peak_windows: PeakWindows
    calibration: Mapping[str, object]
    reconcile: Mapping[str, object]
    ledger: Mapping[str, object]
    runs: Mapping[str, object]
    notes: tuple[str, ...] = field(default=())

    @classmethod
    def from_yaml(cls, path: str | Path) -> BudgetConfig:
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BudgetConfigError(f"形态配置文件不可读：{path}（{exc}）") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, config: object) -> BudgetConfig:
        if not isinstance(config, Mapping):
            raise BudgetConfigError("形态配置必须为映射（含 budget 段）")
        section = _require_section("budget", config.get("budget"))
        notes: list[str] = []
        return cls(
            channels=_parse_channels(section),
            tiers=_parse_tiers(section, notes),
            peak_windows=_parse_peak_windows(section),
            calibration=_parse_calibration(section),
            reconcile=_parse_reconcile(section),
            ledger=_parse_ledger(section),
            runs=_parse_runs(section),
            notes=tuple(notes),
        )

    def channel(self, channel_id: str) -> ChannelSpec:
        """按渠道 id 取登记（缺即报错——不发明渠道，不静默回落）。"""
        spec = self.channels.get(str(channel_id))
        if spec is None:
            raise BudgetConfigError(
                f"渠道 {channel_id!r} 未在 budget.channels 登记（不发明渠道、不静默回落）"
            )
        return spec

    def tier(self, tier_id: str) -> BudgetTier:
        """按环节 id 取档（缺即报错——不取码内默认额度）。"""
        tier = self.tiers.get(str(tier_id))
        if tier is None:
            raise BudgetConfigError(
                f"环节 {tier_id!r} 未在 budget.tiers 声明（缺档即拒绝，不取码内默认）"
            )
        return tier

    def ledger_root(self) -> Path:
        """产物根：相对路径落仓库根，绝对路径原样使用（C4）。"""
        return billing_root(str(self.ledger["root"]))

    def local_date(self, moment: datetime) -> str:
        """渠道日历的本地日期（与峰谷判定、额度 day 窗口同一时区）。"""
        return self.peak_windows.local_date(moment)

    def is_peak(self, moment: datetime) -> bool:
        return self.peak_windows.is_peak(moment)

    def to_snapshot(self, channel_id: str) -> dict:
        """装配时的生效档位快照（并入 Agent 的 `config_snapshot["budget_tiers"]`，C9）。"""
        spec = self.channel(channel_id)
        return {
            "channel_id": spec.channel_id,
            "adapter": spec.adapter,
            "tiers": {tier_id: tier.to_snapshot() for tier_id, tier in sorted(self.tiers.items())},
            "peak_windows_snapshot": self.peak_windows.to_snapshot(),
            "calibration": dict(self.calibration),
            "reconcile": dict(self.reconcile),
            "runs": dict(self.runs),
        }


def peak_windows_snapshot(cfg: BudgetConfig) -> dict:
    """峰谷快照（口径三处可见之一：报告/运行记录口径备注、档位快照、校准记录 note）。"""
    return cfg.peak_windows.to_snapshot()


# ---------------------------------------------------------------------------
# 路径拼装（C4 单一来源；五模块共用）
# ---------------------------------------------------------------------------


def billing_root(configured: str | Path) -> Path:
    """产物根：相对路径落在仓库根（与 CWD 无关，脚本/测试口径一致），绝对路径原样使用。"""
    path = Path(configured)
    return path if path.is_absolute() else REPO_ROOT / path


def channel_dir(root: str | Path, channel_id: str) -> Path:
    """按渠道分目录：单渠道的产物不跨目录写（两渠道同周期互不覆盖）。"""
    return Path(root) / str(channel_id)


def ledger_path(root: str | Path, channel_id: str) -> Path:
    return channel_dir(root, channel_id) / LEDGER_FILENAME


def alerts_path(root: str | Path, channel_id: str) -> Path:
    return channel_dir(root, channel_id) / ALERTS_FILENAME


# ---------------------------------------------------------------------------
# 只增告警留痕（C3 的只增事实一轨 / C14 的告警件）
# ---------------------------------------------------------------------------


class AlertLog:
    """`billing/{channel}/alerts.jsonl` 只增写手。

    字段固定 `kind`/`at`/`channel_id`/`period`/`detail`/`ref`（C14）。

    **只增**：本类不提供改写/删除入口（防绕过事件必须可追溯，镜像部署侧的 `alerts.jsonl` 先例）。
    `kind` 取值域六值固定（`ALERT_KINDS`），域外即报错——新增告警类型必须显式扩取值域。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(
        self,
        *,
        kind: str,
        at: str,
        channel_id: str,
        period: str = "",
        detail: Mapping | None = None,
        ref: str = "",
    ) -> dict:
        if kind not in ALERT_KINDS:
            raise BudgetLedgerError(
                f"告警 kind 必须 ∈ {list(ALERT_KINDS)}，实际为 {kind!r}（不得自造类型）"
            )
        if not str(channel_id or "").strip():
            raise BudgetLedgerError("告警必须带渠道 id（留痕不得无归属）")
        payload = {
            "kind": str(kind),
            "at": str(at),
            "channel_id": str(channel_id),
            "period": str(period),
            "detail": dict(detail or {}),
            "ref": str(ref),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload

    def entries(self) -> list[dict]:
        """读取全部告警行（不存在 → 空；供 `alert-check` 只读门禁消费）。"""
        if not self.path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


# ---------------------------------------------------------------------------
# 跨进程账本（C11）
# ---------------------------------------------------------------------------


class FileLedger:
    """单主机文件账本：`fcntl.flock(LOCK_EX)` → 读 → 改 → `os.replace` 原子替换。

    锁落在**旁路锁文件**（`ledger.json.lock`）：原子替换会换掉账本自身的 inode，锁若落在账本上，
    替换后的后来者锁的是新 inode，会与仍持有旧 inode 锁的进程并行写（丢失更新）。旁路锁文件
    永不被替换，故所有写者争用同一把锁。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        if isinstance(timeout_seconds, bool) or float(timeout_seconds) <= 0:
            raise BudgetLedgerError(f"账本锁超时必须为正数，实际为 {timeout_seconds!r}")
        self.timeout_seconds = float(timeout_seconds)
        self.lock_path = self.path.with_name(self.path.name + LEDGER_LOCK_SUFFIX)
        self._clock = clock or _now

    def read(self) -> dict:
        """无锁读（审计/CLI 只读）；写入一律经 `update`（加锁读改写）。"""
        return self._load()

    def update(self, mutate: Callable[[dict], object]) -> tuple[dict, object]:
        """加锁读改写：`mutate(payload)` 就地修改 → 原子替换；每写 `revision += 1`（单调）。

        锁超时 ⇒ `BudgetLedgerError`（**不无锁写、不静默放行**）；`mutate` 抛错 ⇒ 零落盘。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            payload = self._load()
            result = mutate(payload)
            payload["revision"] = int(payload.get("revision", 0)) + 1
            payload["updated_at"] = self._clock().isoformat()
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            temporary = self.path.with_name(f"{self.path.name}.tmp{os.getpid()}")
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, self.path)
        return payload, result

    def _load(self) -> dict:
        if not self.path.is_file():
            return copy.deepcopy(_EMPTY_LEDGER)
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            return copy.deepcopy(_EMPTY_LEDGER)
        payload = json.loads(text)
        payload.setdefault("revision", 0)
        payload.setdefault("tiers", {})
        return payload

    @contextmanager
    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BudgetLedgerError(
                            f"账本锁超时（{self.timeout_seconds}s）：{self.lock_path} 被占用——"
                            "拒绝调用（不无锁写、不静默放行）"
                        ) from None
                    time.sleep(_LOCK_POLL_SECONDS)
            yield handle
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                os.close(handle)


def _tier_record(
    payload: dict,
    tier_id: str,
    window_key: tuple[str, str],
    *,
    limit_usd: float = 0.0,
    create: bool = False,
) -> dict | None:
    """取该档在当前窗口的账本记录；窗口滚动即归零计数（旧窗口记录保留在 `history` 里可审计）。

    未结算预留（`pending`）**跨窗口保留**：它们是已发生的事实、不是窗口计数——静默清零
    等于把"崩溃残留"抹掉。
    """
    tiers = payload.setdefault("tiers", {})
    record = tiers.get(tier_id)
    if record is None:
        if not create:
            return None
        record = {
            "window_key": list(window_key),
            "limit_usd": limit_usd,
            "spent_usd": 0.0,
            "reserved_usd": 0.0,
            "refusals": 0,
            "last_refusal": None,
            "pending": [],
            "history": [],
        }
        tiers[tier_id] = record
        return record
    if list(window_key) != list(record.get("window_key") or []):
        record.setdefault("history", []).append(
            {
                key: record.get(key)
                for key in ("window_key", "limit_usd", "spent_usd", "reserved_usd", "refusals")
            }
        )
        record["window_key"] = list(window_key)
        record["limit_usd"] = limit_usd
        record["spent_usd"] = 0.0
        record["refusals"] = 0
        record["last_refusal"] = None
    return record


def _pending_total(record: Mapping) -> float:
    return sum(float(item["estimated_usd"]) for item in record.get("pending", []))


def _remaining_of(record: Mapping) -> float:
    return (
        float(record.get("limit_usd", 0.0))
        - float(record.get("spent_usd", 0.0))
        - float(record.get("reserved_usd", 0.0))
    )


@dataclass
class Reservation:
    """一次预留（两段式第一段）：`settle(actual_usd)` 结清；未结算即为崩溃残留的可见标记。"""

    ledger: FileLedger
    alerts: AlertLog
    channel_id: str
    tier_id: str
    window_key: tuple[str, str]
    estimated_usd: float
    reservation_id: str
    at: str
    period: str = ""
    settled: bool = False

    def settle(self, actual_usd: float) -> dict:
        """结算：`reserved -= estimated; spent += actual`（实测超预估 ⇒ 余量可为负，如实入账）。

        实测超预估时**追加 `over_limit` 告警**：已发生的花费如实入账、不回滚、不改写，
        但后续调用会被同一门禁拒绝（余量为负）。
        """
        if self.settled:
            raise BudgetLedgerError(f"预留 {self.reservation_id!r} 已结算（不得重复结算）")
        actual = _require_amount("settle.actual_usd", actual_usd, positive=False)

        def mutate(payload: dict) -> dict:
            record = _tier_record(payload, self.tier_id, self.window_key, create=False)
            if record is None:
                raise BudgetLedgerError(
                    f"账本中无环节 {self.tier_id!r} 的记录（预留 {self.reservation_id!r} 无从结算）"
                )
            pending = record.get("pending", [])
            index = next(
                (
                    position
                    for position, item in enumerate(pending)
                    if item.get("reservation_id") == self.reservation_id
                ),
                None,
            )
            if index is None:
                raise BudgetLedgerError(
                    f"账本中找不到预留 {self.reservation_id!r}（拒绝静默改写余量："
                    "该预留已结算，或账本被外部改写）"
                )
            pending.pop(index)
            record["reserved_usd"] = _pending_total(record)
            record["spent_usd"] = float(record.get("spent_usd", 0.0)) + actual
            return dict(record)

        _, record = self.ledger.update(mutate)
        self.settled = True
        remaining = _remaining_of(record)
        if remaining < -AMOUNT_EPSILON:
            self.alerts.record(
                kind="over_limit",
                at=self.at,
                channel_id=self.channel_id,
                period=self.period,
                detail={
                    "tier_id": self.tier_id,
                    "window_key": list(self.window_key),
                    "remaining_usd": remaining,
                    "estimated_usd": self.estimated_usd,
                    "actual_usd": actual,
                    "note": "实测超预估（上界估算仍可能被超）：如实入账、不回滚，后续调用拒绝",
                },
                ref=self.reservation_id,
            )
        return record


class SpendGuard:
    """前置预算门禁：固定序列第③步（后端调用**之前**）按（预估额 vs 余量）判定。

    - `check(request)`：缺 `stage` ⇒ 拒绝 `tier_undeclared`；预估价 > 余量 ⇒ 拒绝 `over_limit`；
      通过则**同事务占额**（`reserved += estimated`）并返回 `Reservation`；
    - 拒绝语义：告警落 `alerts.jsonl`（`kind=budget_refused`）+ 账本 `refusals` 计数 +
      `last_refusal{at, reason, estimated_usd}`，随后抛 `BudgetRefusedError`（调用方零入账）；
    - `window_context`：`run`/`period` 窗口实例（`day` 由 `peak_windows.timezone` 的本地日期派生）；
      缺实例 ⇒ 拒绝 `window_unresolved`（拒绝而不是猜一格）。

    未声明的环节档**不写账本**（拒绝计数只落在已声明档上）；事实由 `alerts.jsonl` 承载。
    """

    def __init__(
        self,
        *,
        cfg: BudgetConfig,
        channel_id: str,
        ledger: FileLedger,
        alerts: AlertLog,
        window_context: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        self.channel_id = str(channel_id)
        self.ledger = ledger
        self.alerts = alerts
        self.window_context = dict(window_context or {})
        self._clock = clock or _now
        cfg.channel(self.channel_id)  # 未登记的渠道不得装配门禁（缺声明即报错）

    @property
    def period(self) -> str:
        return str(self.window_context.get("period", "") or "")

    def window_key(self, tier: BudgetTier, moment: datetime) -> tuple[str, str]:
        """窗口实例：`run` 用运行标识、`day` 用本地日期、`period` 用账单周期。"""
        if tier.window_kind == "day":
            return ("day", self.cfg.local_date(moment))
        instance = str(self.window_context.get(tier.window_kind, "") or "")
        if not instance:
            raise BudgetRefusedError(
                f"预算拒绝（window_unresolved）：环节 {tier.tier_id!r} 的窗口类型 "
                f"{tier.window_kind!r} 缺窗口实例（拒绝调用，不猜窗口）",
                reason="window_unresolved",
                tier_id=tier.tier_id,
            )
        return (tier.window_kind, instance)

    def check(self, request: SpendRequest) -> Reservation:
        """判定并占额：通过 ⇒ `Reservation`；拒绝 ⇒ 告警 + 计数 + `BudgetRefusedError`。"""
        moment = self._clock()
        tier_id = str(getattr(request, "stage", "") or "")
        requested_channel = str(getattr(request, "channel_id", "") or "")
        estimated = _estimate_of(request, tier_id=tier_id)
        if requested_channel != self.channel_id:
            self._refuse(
                tier=None,
                reason="channel_mismatch",
                moment=moment,
                estimated_usd=estimated,
                detail={
                    "requested_channel_id": requested_channel,
                    "guard_channel_id": self.channel_id,
                },
            )
        tier = self.cfg.tiers.get(tier_id)
        if tier is None:
            self._refuse(
                tier=None,
                reason="tier_undeclared",
                moment=moment,
                estimated_usd=estimated,
                detail={"stage": tier_id, "declared_tiers": sorted(self.cfg.tiers)},
            )
        window_key = self.window_key(tier, moment)
        at = moment.isoformat()
        reservation_id = f"{self.channel_id}:{tier_id}:{window_key[0]}:{window_key[1]}:{at}"

        def mutate(payload: dict) -> dict:
            record = _tier_record(
                payload, tier.tier_id, window_key, limit_usd=tier.limit_usd, create=True
            )
            remaining = _remaining_of(record)
            if estimated > remaining + AMOUNT_EPSILON:
                record["refusals"] = int(record.get("refusals", 0)) + 1
                record["last_refusal"] = {
                    "at": at,
                    "reason": "over_limit",
                    "estimated_usd": estimated,
                }
                return {"refused": True, "remaining_usd": remaining}
            record["pending"] = [
                *record.get("pending", []),
                {
                    "reservation_id": reservation_id,
                    "at": at,
                    "estimated_usd": estimated,
                    "window_key": list(window_key),
                },
            ]
            record["reserved_usd"] = _pending_total(record)
            return {"refused": False, "remaining_usd": remaining}

        _, outcome = self.ledger.update(mutate)
        if outcome["refused"]:
            self._refuse(
                tier=tier,
                reason="over_limit",
                moment=moment,
                estimated_usd=estimated,
                detail={"remaining_usd": outcome["remaining_usd"]},
            )
        return Reservation(
            ledger=self.ledger,
            alerts=self.alerts,
            channel_id=self.channel_id,
            tier_id=tier_id,
            window_key=window_key,
            estimated_usd=estimated,
            reservation_id=reservation_id,
            at=at,
            period=self.period,
        )

    def _refuse(
        self,
        *,
        tier: BudgetTier | None,
        reason: str,
        moment: datetime,
        estimated_usd: float,
        detail: Mapping | None = None,
    ) -> None:
        """拒绝：告警留痕 + 抛 `BudgetRefusedError`（计数已在加锁段内完成或本就不适用）。"""
        payload = {"reason": reason, "estimated_usd": estimated_usd, **dict(detail or {})}
        self.alerts.record(
            kind="budget_refused",
            at=moment.isoformat(),
            channel_id=self.channel_id,
            period=self.period,
            detail=payload,
            ref=str(getattr(tier, "tier_id", "") or payload.get("stage", "")),
        )
        remaining = payload.get("remaining_usd")
        raise BudgetRefusedError(
            f"预算拒绝（{reason}）：环节 {tier.tier_id if tier else payload.get('stage', '')!r} "
            f"预估价 {estimated_usd} / 余量 {remaining}——拒绝调用，成本零入账",
            reason=reason,
            tier_id=tier.tier_id if tier else str(payload.get("stage", "")),
            remaining_usd=float(remaining) if remaining is not None else None,
            estimated_usd=estimated_usd,
        )


# ---------------------------------------------------------------------------
# `budget:` 段解析（C9：全量校验，缺项即报错）
# ---------------------------------------------------------------------------

_REQUIRED_CALIBRATION_KEYS = ("min_samples", "deviation_tolerance", "record_ttl_days")
_REQUIRED_RECONCILE_KEYS = ("amount_tolerance_usd", "alert_threshold_usd", "unexplained_alert")
_REQUIRED_LEDGER_KEYS = ("root", "lock_timeout_seconds")
_REQUIRED_RUN_KEYS = ("min_window_days", "gap_tolerance_days")


def _require_section(path: str, value: object) -> Mapping:
    if not isinstance(value, Mapping) or not value:
        raise BudgetConfigError(f"{path} 必须为非空映射段，实际为 {value!r}")
    return value


def _require_key(path: str, section: Mapping, key: str) -> object:
    if key not in section or section[key] is None:
        raise BudgetConfigError(f"{path} 缺少配置项 {key!r}（缺项即报错，不取码内默认）")
    return section[key]


def _require_str(path: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BudgetConfigError(f"{path} 必须为非空字符串，实际为 {value!r}")
    return value


def _require_bool(path: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise BudgetConfigError(f"{path} 必须为布尔值，实际为 {value!r}")
    return value


def _require_amount(path: str, value: object, *, positive: bool) -> float:
    """金额校验：数值、非 bool、有限（`limit_usd` > 0；`amount`/容差 ≥ 0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BudgetConfigError(f"{path} 必须为数值，实际为 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise BudgetConfigError(f"{path} 必须为有限数值，实际为 {value!r}")
    if positive and number <= 0:
        raise BudgetConfigError(f"{path} 必须 > 0，实际为 {number!r}")
    if not positive and number < 0:
        raise BudgetConfigError(f"{path} 必须 ≥ 0，实际为 {number!r}")
    return number


def _require_int(path: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BudgetConfigError(f"{path} 必须为 ≥ {minimum} 的整数，实际为 {value!r}")
    return value


def _require_sign_factor(path: str, value: object) -> float:
    """符号取值域：`+1`（记账）或 `-1`（冲减）——符号是符号，不是可调系数。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) not in (1.0, -1.0)
    ):
        raise BudgetConfigError(
            f"{path} 必须为 1.0（记账）或 -1.0（冲减），实际为 {value!r}"
            "（符号由账单列声明，不得用金额启发式推断）"
        )
    return float(value)


def _parse_clock(value: str, path: str) -> clock_time:
    text = _require_str(path, value)
    try:
        hour, minute = text.split(":")
        if len(hour) != 2 or len(minute) != 2:
            raise ValueError(text)
        return clock_time(int(hour), int(minute))
    except ValueError as exc:
        raise BudgetConfigError(f"{path} 必须为 HH:MM 时刻，实际为 {value!r}") from exc


def _localize(moment: datetime, timezone: str) -> datetime:
    if not isinstance(moment, datetime):
        raise BudgetConfigError(f"时刻必须为 datetime（带时区），实际为 {moment!r}")
    zone = ZoneInfo(timezone)
    return moment.astimezone(zone) if moment.tzinfo else moment.replace(tzinfo=UTC).astimezone(zone)


def _parse_channels(section: Mapping) -> dict[str, ChannelSpec]:
    raw = _require_section("budget.channels", _require_key("budget", section, "channels"))
    channels: dict[str, ChannelSpec] = {}
    for channel_id, channel in raw.items():
        where = f"budget.channels.{channel_id}"
        spec = _require_section(where, channel)
        bill = _require_section(f"{where}.bill", _require_key(where, spec, "bill"))
        columns = _require_section(
            f"{where}.bill.columns", _require_key(f"{where}.bill", bill, "columns")
        )
        classification = _require_section(
            f"{where}.bill.classification", _require_key(f"{where}.bill", bill, "classification")
        )
        line_kinds = _require_section(
            f"{where}.bill.classification.line_kind",
            _require_key(f"{where}.bill.classification", classification, "line_kind"),
        )
        signs = _require_section(
            f"{where}.bill.classification.amount_sign",
            _require_key(f"{where}.bill.classification", classification, "amount_sign"),
        )
        fetch = _require_str(f"{where}.bill.fetch", _require_key(f"{where}.bill", bill, "fetch"))
        if fetch not in ("export", "api"):
            raise BudgetConfigError(
                f"{where}.bill.fetch 必须 ∈ ['export', 'api']，实际为 {fetch!r}"
            )
        channels[str(channel_id)] = ChannelSpec(
            channel_id=str(channel_id),
            adapter=_require_str(f"{where}.adapter", _require_key(where, spec, "adapter")),
            bill=ChannelBillSpec(
                format_id=_require_str(
                    f"{where}.bill.format", _require_key(f"{where}.bill", bill, "format")
                ),
                fetch=fetch,
                columns={str(key): str(value) for key, value in columns.items()},
                line_kind_classes={str(key): str(value) for key, value in line_kinds.items()},
                amount_signs={
                    str(key): _require_sign_factor(
                        f"{where}.bill.classification.amount_sign.{key}", value
                    )
                    for key, value in signs.items()
                },
            ),
        )
    return channels


def _parse_tiers(section: Mapping, notes: list[str]) -> dict[str, BudgetTier]:
    raw = _require_section("budget.tiers", _require_key("budget", section, "tiers"))
    tiers: dict[str, BudgetTier] = {}
    for tier_id, tier in raw.items():
        where = f"budget.tiers.{tier_id}"
        spec = _require_section(where, tier)
        window = _require_section(f"{where}.window", _require_key(where, spec, "window"))
        kind = _require_str(f"{where}.window.kind", _require_key(f"{where}.window", window, "kind"))
        if kind not in WINDOW_KINDS:
            raise BudgetConfigError(
                f"{where}.window.kind 必须 ∈ {list(WINDOW_KINDS)}，实际为 {kind!r}"
            )
        exhausted = _require_str(f"{where}.on_exhausted", _require_key(where, spec, "on_exhausted"))
        if exhausted != ON_EXHAUSTED_REFUSE:
            raise BudgetConfigError(
                f"{where}.on_exhausted 取值域单元素 {ON_EXHAUSTED_REFUSE!r}，实际为 {exhausted!r}"
                "（不排队、不降级为模拟）"
            )
        note = str(spec.get("note") or "")
        if not note.strip():
            # 口径必须可追溯：缺 note 记 notes 告警但可通过（运营补全前不阻塞装配）
            notes.append(f"{where}.note 缺失：额度口径不可追溯（缺省通过但记 notes 告警）")
        tiers[str(tier_id)] = BudgetTier(
            tier_id=str(tier_id),
            limit_usd=_require_amount(
                f"{where}.limit_usd", _require_key(where, spec, "limit_usd"), positive=True
            ),
            window_kind=kind,
            note=note,
            calibrated_by=str(spec.get("calibrated_by") or ""),
        )
    return tiers


def _parse_peak_windows(section: Mapping) -> PeakWindows:
    raw = _require_section("budget.peak_windows", _require_key("budget", section, "peak_windows"))
    timezone = _require_str(
        "budget.peak_windows.timezone",
        _require_key("budget.peak_windows", raw, "timezone"),
    )
    try:
        ZoneInfo(timezone)
    except Exception as exc:  # noqa: BLE001 - 未知时区名一律拒绝（不静默回落 UTC）
        raise BudgetConfigError(
            f"budget.peak_windows.timezone 不是合法时区名：{timezone!r}（{exc}）"
        ) from exc
    attribution = _require_str(
        "budget.peak_windows.attribution",
        _require_key("budget.peak_windows", raw, "attribution"),
    )
    if attribution != ATTRIBUTION_CALL_START:
        raise BudgetConfigError(
            f"budget.peak_windows.attribution 取值域单元素 {ATTRIBUTION_CALL_START!r}，"
            f"实际为 {attribution!r}（跨峰谷不拆分）"
        )
    windows_raw = _require_key("budget.peak_windows", raw, "windows")
    if not isinstance(windows_raw, Sequence) or isinstance(windows_raw, (str, bytes)):
        raise BudgetConfigError(
            f"budget.peak_windows.windows 必须为序列（不需峰谷定价的渠道写 []），"
            f"实际为 {windows_raw!r}"
        )
    windows: list[PeakWindow] = []
    for index, window in enumerate(windows_raw):
        where = f"budget.peak_windows.windows[{index}]"
        entry = _require_section(where, window)
        start = _require_str(f"{where}.start", _require_key(where, entry, "start"))
        end = _require_str(f"{where}.end", _require_key(where, entry, "end"))
        _parse_clock(start, f"{where}.start")
        _parse_clock(end, f"{where}.end")
        if start == end:
            raise BudgetConfigError(f"{where} 起止相同（{start!r}）：口径不明，拒绝装配")
        windows.append(PeakWindow(start=start, end=end))
    return PeakWindows(timezone=timezone, attribution=attribution, windows=tuple(windows))


def _parse_calibration(section: Mapping) -> dict:
    raw = _require_section("budget.calibration", _require_key("budget", section, "calibration"))
    for key in _REQUIRED_CALIBRATION_KEYS:
        if key not in raw:
            raise BudgetConfigError(f"budget.calibration 缺少配置项 {key!r}")
    tolerance = _require_amount(
        "budget.calibration.deviation_tolerance", raw["deviation_tolerance"], positive=False
    )
    if tolerance > 1.0:
        raise BudgetConfigError(
            f"budget.calibration.deviation_tolerance 必须 ≤ 1，实际为 {tolerance!r}"
        )
    return {
        "min_samples": _require_int(
            "budget.calibration.min_samples", raw["min_samples"], minimum=1
        ),
        "deviation_tolerance": tolerance,
        "record_ttl_days": _require_int(
            "budget.calibration.record_ttl_days", raw["record_ttl_days"], minimum=1
        ),
    }


def _parse_reconcile(section: Mapping) -> dict:
    raw = _require_section("budget.reconcile", _require_key("budget", section, "reconcile"))
    for key in _REQUIRED_RECONCILE_KEYS:
        if key not in raw:
            raise BudgetConfigError(f"budget.reconcile 缺少配置项 {key!r}")
    return {
        "amount_tolerance_usd": _require_amount(
            "budget.reconcile.amount_tolerance_usd", raw["amount_tolerance_usd"], positive=False
        ),
        "alert_threshold_usd": _require_amount(
            "budget.reconcile.alert_threshold_usd", raw["alert_threshold_usd"], positive=False
        ),
        "unexplained_alert": _require_bool(
            "budget.reconcile.unexplained_alert", raw["unexplained_alert"]
        ),
    }


def _parse_ledger(section: Mapping) -> dict:
    raw = _require_section("budget.ledger", _require_key("budget", section, "ledger"))
    for key in _REQUIRED_LEDGER_KEYS:
        if key not in raw:
            raise BudgetConfigError(f"budget.ledger 缺少配置项 {key!r}")
    return {
        "root": _require_str("budget.ledger.root", raw["root"]),
        "lock_timeout_seconds": _require_amount(
            "budget.ledger.lock_timeout_seconds", raw["lock_timeout_seconds"], positive=True
        ),
    }


def _parse_runs(section: Mapping) -> dict:
    raw = _require_section("budget.runs", _require_key("budget", section, "runs"))
    for key in _REQUIRED_RUN_KEYS:
        if key not in raw:
            raise BudgetConfigError(f"budget.runs 缺少配置项 {key!r}")
    return {
        "min_window_days": _require_int(
            "budget.runs.min_window_days", raw["min_window_days"], minimum=1
        ),
        "gap_tolerance_days": _require_int(
            "budget.runs.gap_tolerance_days", raw["gap_tolerance_days"], minimum=0
        ),
    }


def _estimate_of(request: SpendRequest, *, tier_id: str) -> float:
    """读请求里的预估价（网关填，guard 不自行估算）；非法即拒绝 `bad_estimate`。"""
    value = getattr(request, "estimated_usd", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BudgetRefusedError(
            f"预算拒绝（bad_estimate）：环节 {tier_id!r} 的 estimated_usd 非数值（实际 {value!r}）"
            "——预估价由网关填，缺它无从判定，故拒绝调用",
            reason="bad_estimate",
            tier_id=tier_id,
        )
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise BudgetRefusedError(
            f"预算拒绝（bad_estimate）：环节 {tier_id!r} 的 estimated_usd 非法（{value!r}）",
            reason="bad_estimate",
            tier_id=tier_id,
        )
    return number
