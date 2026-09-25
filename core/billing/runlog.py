"""渠道运行记录（按时间索引的 append-only 证据；契约 C3 / C15）。

五模块之一，承接"连续运行"从叙述变成**可机检事实**的记录面：

- 粒度：每次真实调用一条 entry（`at` / `stage` / `source` / `adapter_ref` / `profile_id` /
  `result` / `cost_source` / `fallback_reason`）；按 `peak_windows.timezone` 的**本地日期**
  分片落 `billing/{channel}/runs/{date}.json`（与峰谷判定、额度 day 窗口同一日历）；
- `entries` 追加只增；`head_digest` = BLAKE3(前一条 `head_digest` + 本条 canonical JSON)
  **链式摘要**——改写或删除任一条即断链 ⇒ 读取报错（不静默取）；
- 当日 `sealed` 后追加拒绝（当日证据封存）；
- `source=fallback`（回落）必须显式声明**原因**：真实渠道失败**禁止**静默回落模拟并照常计费
  （FR-013）。该值为保留值——本特性无写入点（装配期即拒、网关不设回落路径），但读取口径
  必须齐备，故本模块在写入侧就要求原因。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import blake3

from core.billing.budget import (
    BudgetConfig,
    BudgetRefusedError,
    GuardAssembly,
    channel_dir,
)

RUN_SOURCES = ("real", "simulated", "fallback")
# 保留值：本特性无写入点（装配期即拒、网关不设回落路径），值域保留 + 写入侧要求原因
FALLBACK_SOURCE = "fallback"
# 每条 entry 的必填字段（缺项即拒绝追加：记录不齐等于证据不齐）
RUN_ENTRY_FIELDS = (
    "at",
    "stage",
    "source",
    "adapter_ref",
    "profile_id",
    "result",
    "cost_source",
    "fallback_reason",
)


class RunLogError(Exception):
    """运行记录错误（来源取值域外、回落无原因、当日已封存、链式摘要断链）。"""


@dataclass(frozen=True)
class RunEntry:
    """一条运行记录（一次真实调用）；`head_digest` 为链式摘要的当前值。"""

    at: str
    stage: str
    source: str
    adapter_ref: str
    profile_id: str
    result: str
    cost_source: str
    fallback_reason: str
    head_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "stage": self.stage,
            "source": self.source,
            "adapter_ref": self.adapter_ref,
            "profile_id": self.profile_id,
            "result": self.result,
            "cost_source": self.cost_source,
            "fallback_reason": self.fallback_reason,
            "head_digest": self.head_digest,
        }


def run_path(root: str | Path, channel_id: str, date: str) -> Path:
    """运行记录路径（C4）：`billing/{channel}/runs/{date}.json`（`{date}` = 渠道本地日期）。"""
    return channel_dir(root, channel_id) / "runs" / f"{date}.json"


def _entry_digest(previous: str, entry: Mapping) -> str:
    """链式摘要：`BLAKE3(前一条 head_digest + 本条 canonical JSON)`（改任一条即断链）。"""
    canonical = json.dumps(
        {key: entry.get(key) for key in RUN_ENTRY_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return blake3.blake3(f"{previous}|{canonical}".encode()).hexdigest()


def _load_raw(path: Path) -> dict:
    if not path.is_file():
        raise RunLogError(f"运行记录不存在：{path}（先追加一次再读取，不静默返回空）")
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_chain(payload: Mapping, path: Path) -> None:
    """链校验：逐条重算摘要，任一处不符即报错（改写/删除/插入都逃不过）。"""
    previous = ""
    for index, entry in enumerate(payload.get("entries", [])):
        expected = _entry_digest(previous, entry)
        if entry.get("head_digest") != expected:
            raise RunLogError(
                f"运行记录链校验失败：{path} 第 {index + 1} 条 entry 的 head_digest 不匹配"
                "（记录被改写或删除，拒绝采信）"
            )
        previous = expected
    if payload.get("head_digest") != previous:
        raise RunLogError(
            f"运行记录链校验失败：{path} 的 head_digest 与 entries 不一致（尾条被改写或删除）"
        )


def append_run(
    channel_id: str,
    *,
    cfg: BudgetConfig,
    root: str | Path,
    moment: datetime,
    stage: str,
    source: str,
    adapter_ref: str = "",
    profile_id: str = "",
    result: str = "",
    cost_source: str = "",
    fallback_reason: str = "",
) -> dict:
    """追加一条运行记录（日期按 `peak_windows.timezone` 归属；当日封存后拒绝）。

    来源取值域 `real|simulated|fallback`；`fallback` 必须显式声明原因——回落是被**声明**的事实，
    不是可静默发生的事。
    """
    if source not in RUN_SOURCES:
        raise RunLogError(f"运行来源必须 ∈ {list(RUN_SOURCES)}，实际为 {source!r}")
    if not str(stage or "").strip():
        raise RunLogError("stage（环节 id）必填：运行记录必须可归到环节")
    if source == FALLBACK_SOURCE and not str(fallback_reason or "").strip():
        raise RunLogError(
            "source=fallback 必须声明 fallback_reason（真实渠道失败禁止静默回落并照常计费）"
        )
    cfg.channel(channel_id)
    date = cfg.local_date(moment)
    path = run_path(root, channel_id, date)
    if path.is_file():
        payload = _load_raw(path)
        _verify_chain(payload, path)
        if payload.get("sealed"):
            raise RunLogError(f"当日运行记录已封存（sealed），追加拒绝：{path}")
    else:
        payload = {
            "channel_id": str(channel_id),
            "date": date,
            "timezone": cfg.peak_windows.timezone,
            "entries": [],
            "head_digest": "",
            "sealed": False,
        }
    if str(payload.get("channel_id")) != str(channel_id):
        raise RunLogError(f"运行记录文件与渠道不符：{path} 属 {payload.get('channel_id')!r}")
    entry = {
        "at": moment.isoformat(),
        "stage": str(stage),
        "source": str(source),
        "adapter_ref": str(adapter_ref),
        "profile_id": str(profile_id),
        "result": str(result),
        "cost_source": str(cost_source),
        "fallback_reason": str(fallback_reason),
    }
    digest = _entry_digest(str(payload.get("head_digest", "")), entry)
    entry["head_digest"] = digest
    payload["entries"] = [*payload.get("entries", []), entry]
    payload["head_digest"] = digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return entry


def seal_run(date: str, *, channel_id: str, root: str | Path) -> dict:
    """封存当日记录（此后追加拒绝）。封存不改动既有 entries 与链（只置 sealed）。"""
    path = run_path(root, channel_id, date)
    payload = _load_raw(path)
    _verify_chain(payload, path)
    payload["sealed"] = True
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def load_run(date: str, *, channel_id: str, root: str | Path) -> dict:
    """读取当日记录并**链校验**：改写/删除任一条即报错（不静默取）。"""
    path = run_path(root, channel_id, date)
    payload = _load_raw(path)
    if str(payload.get("channel_id")) != str(channel_id):
        raise RunLogError(f"运行记录文件与渠道不符：{path} 属 {payload.get('channel_id')!r}")
    _verify_chain(payload, path)
    return payload


# 金额来源口径（C12/C15：花费可回溯、不凭报告自证）：网关折算记账值 / 厂商侧实测回填
COST_SOURCE_GATEWAY = "gateway_accounting"
COST_SOURCE_MEASURED = "measured_backfill"


def _as_date(value, cfg: BudgetConfig):
    """日期归一：`end` 允许 date / `YYYY-MM-DD` / 带时区 datetime。

    带时区的 datetime 按**渠道日历**（`peak_windows.timezone`）取本地日。
    """
    if isinstance(value, datetime):
        return date.fromisoformat(cfg.local_date(value))
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise RunLogError(f"日期必须为 YYYY-MM-DD，实际为 {value!r}") from exc
    raise RunLogError(f"日期必须为 date / YYYY-MM-DD / datetime，实际为 {value!r}")


def window_coverage(
    channel_id: str,
    *,
    cfg: BudgetConfig,
    root: str | Path,
    end,
    min_window_days: int | None = None,
    gap_tolerance_days: int | None = None,
) -> dict:
    """窗口机检（C15）：**覆盖 + 连续双条件**，缺口如实列出、**禁止插值补齐**。

    - `covered_days` **只计 `source=real` 的日期**（模拟/回落日不算真实运行日）；
    - `meets = covered_days ≥ min_window_days` **∧** `max_gap_days ≤ gap_tolerance_days`
      （两项均由配置声明：缺省取 `budget.runs.*`）；
    - `continuous = not gaps`（**有无断档**，与容差无关）：通过也不谎报为"连续"，
      `covered_days` / `continuous` / `gaps` 三项照旧落在产物里，失败可归因；
    - 未达标时给出**归因**：覆盖差值、最长断档与容差的差值、逐段 `gaps`；
    - 日期口径 = `peak_windows.timezone`（与额度 day 窗口、峰谷判定同一日历）。
    """
    from datetime import timedelta

    cfg.channel(channel_id)
    end_date = _as_date(end, cfg)
    min_days = int(cfg.runs["min_window_days"] if min_window_days is None else min_window_days)
    tolerance = int(
        cfg.runs["gap_tolerance_days"] if gap_tolerance_days is None else gap_tolerance_days
    )
    directory = channel_dir(root, channel_id) / "runs"
    observed: dict[date, dict[str, int]] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        payload = load_run(path.stem, channel_id=channel_id, root=root)  # 链校验后才采信
        day = _as_date(payload["date"], cfg)
        if day > end_date:
            continue
        counts = observed.setdefault(day, {"real": 0, "simulated": 0, "fallback": 0})
        for entry in payload.get("entries", []):
            counts[str(entry.get("source"))] = counts.get(str(entry.get("source")), 0) + 1
    covered_dates = sorted(day for day, counts in observed.items() if counts.get("real"))
    start_date = min(observed) if observed else end_date
    gaps: list[dict] = []
    cursor = start_date
    while cursor <= end_date:
        if cursor not in observed:  # 断档 = **没有任何记录**的日期；不插值、不补零
            gap_start = cursor
            while cursor <= end_date and cursor not in observed:
                cursor += timedelta(days=1)
            gaps.append(
                {
                    "from": gap_start.isoformat(),
                    "to": (cursor - timedelta(days=1)).isoformat(),
                    "days": (cursor - gap_start).days,
                }
            )
            continue
        cursor += timedelta(days=1)
    max_gap_days = max((gap["days"] for gap in gaps), default=0)
    covered_days = len(covered_dates)
    meets = covered_days >= min_days and max_gap_days <= tolerance
    coverage_shortfall = max(0, min_days - covered_days)
    gap_shortfall = max(0, max_gap_days - tolerance)
    reasons: list[str] = []
    if coverage_shortfall:
        reasons.append(f"覆盖不足：covered_days={covered_days} < min_window_days={min_days}")
    if gap_shortfall:
        reasons.append(f"断档超容差：max_gap_days={max_gap_days} > gap_tolerance_days={tolerance}")
    return {
        "channel_id": str(channel_id),
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "covered_days": covered_days,
        "covered_dates": [day.isoformat() for day in covered_dates],
        "gaps": gaps,
        "max_gap_days": max_gap_days,
        "continuous": not gaps,
        "min_window_days": min_days,
        "gap_tolerance_days": tolerance,
        "meets": meets,
        "coverage_shortfall_days": coverage_shortfall,
        "gap_shortfall_days": gap_shortfall,
        "reasons": reasons,
        "note": (
            "covered_days 只计 source=real 的日期；断档按逐日**如实列出**（不插值补齐）；"
            "continuous 与 meets 并列呈现——容差放开时可能 meets=true 而 continuous=false"
        ),
    }


class RecordingGateway:
    """网关的运行记录包装（C15 / T1942）：转发一切，`chat` 之后落一条运行记录。

    **为什么是包装而非网关内改**：`core/llm_gateway` 对本包**零 import**（C1），且网关必须
    独立可用；运行记录属渠道计费纪律，故在**装配面**包装——两个真实装配点共用本实现。

    - 一次调用 = 一条 entry：`result` ∈ `ok` / `failed` / `refused`（拒绝与厂商失败可辨）；
    - `source` 由装配面声明：真实渠道 = `real`，模拟后端 = `simulated`（**模拟日不计入
      `covered_days`**，C15）；`fallback` 为保留值（本特性无写入点）；
    - `cost_source` 标注金额来源（网关折算记账值 / 厂商侧实测回填），使花费可回溯；
    - 缺少环节归属的拒绝（`tier_undeclared`）不写记录（运行记录要求可归到环节，
      该次拒绝的证据在 `alerts.jsonl`），其余调用一律留痕；写记录失败**不吞**（证据不得静默丢失）。
    """

    def __init__(
        self,
        gateway,
        *,
        cfg: BudgetConfig,
        root: str | Path,
        channel_id: str,
        source: str,
        adapter_ref: str = "",
        clock=None,
    ) -> None:
        if source not in RUN_SOURCES:
            raise RunLogError(f"运行来源必须 ∈ {list(RUN_SOURCES)}，实际为 {source!r}")
        self._gateway = gateway
        self._cfg = cfg
        self._root = root
        self._channel_id = str(channel_id)
        self._source = str(source)
        self._adapter_ref = str(adapter_ref)
        self._clock = clock or (lambda: _now())

    def chat(self, prompt: str, **kwargs):
        """转发一次调用并留痕。

        **本处不新增 `.chat(` 调用点**（静态计数钉死 8 处；那 8 处各自声明 `role=`/`stage=`）：
        转发目标用 `getattr` 取——这是**装配面的转发器**，不是发起调用的调用点。
        """
        moment = self._clock()
        stage = str(kwargs.get("stage", "") or "")
        try:
            result = getattr(self._gateway, "chat")(prompt, **kwargs)  # noqa: B009 - 见 docstring：刻意的转发取法
        except Exception as exc:  # noqa: BLE001 - 失败/拒绝都要留痕后原样上抛
            self._write(
                moment=moment,
                stage=stage,
                result="refused" if isinstance(exc, BudgetRefusedError) else "failed",
                profile_id="",
            )
            raise
        self._write(
            moment=moment,
            stage=stage,
            result="ok",
            profile_id=str(getattr(result, "profile_id", "") or ""),
        )
        return result

    def __getattr__(self, name: str):
        """其余属性与方法一律转发（`cost_report` / `profile_snapshot` / `spend_guard` …）。"""
        return getattr(self._gateway, name)

    def _write(self, *, moment, stage: str, result: str, profile_id: str) -> None:
        if not stage:
            return  # 无环节归属（如缺 stage= 的拒绝）：证据在 alerts.jsonl
        append_run(
            self._channel_id,
            cfg=self._cfg,
            root=self._root,
            moment=moment,
            stage=stage,
            source=self._source,
            adapter_ref=self._adapter_ref,
            profile_id=profile_id,
            result=result,
            cost_source=COST_SOURCE_GATEWAY,
            fallback_reason="",
        )


class RecordingChannelCall:
    """投放渠道调用的**门禁包装**（C14 / T2049）：与既有 `RecordingGateway` 同构，同处无第二份实现。

    **投放面唯一的调用包装点**（投放执行器内不得再写一份门禁调用）：在
    `create_campaign(...)` **之前**取门禁判定（`guard.check`，`stage` = 该渠道的投放环节 id），
    平台响应之后 `reservation.settle(campaign.spent_usd)`（**实测超预估如实入账** + `over_limit`
    告警，019 口径不变）；其余属性与方法一律转发（`get_status` / `pause` / `fetch_metrics` …），
    故可原样替换适配器传入投放执行器（调用点的零成本分支见该模块文档）。

    三条纪律：

    - **拒绝 ⇒ 前置**：平台调用 **0 次**、成本 **0 入账**、运行记录 `result=refused`、
      `alerts.jsonl` 落 `kind=budget_refused`（019 C10 的零成本分支，投放面同样成立）；
    - **失败 ⇒ 零入账**：平台失败的这一笔**未发生花费**，以 `settle(0.0)` 释放预留
      （不留崩溃残留标记），运行记录 `result=failed`，异常原样上抛（不吞、**不静默回落模拟**）；
    - **来源由装配面声明**：取值域 `RUN_SOURCES`；`fallback` 必须带非空 `fallback_reason`
      （本特性不新增回落路径，该值为保留值）。

    金额来源标注 `measured_backfill`：投放的实际花费以**平台响应**为准（不是网关折算记账值）。
    """

    def __init__(
        self,
        adapter,
        *,
        assembly: GuardAssembly,
        stage: str,
        source: str,
        fallback_reason: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if source not in RUN_SOURCES:
            raise RunLogError(f"运行来源必须 ∈ {list(RUN_SOURCES)}，实际为 {source!r}")
        if source == FALLBACK_SOURCE and not str(fallback_reason or "").strip():
            raise RunLogError(
                "source=fallback 必须声明 fallback_reason（真实渠道失败禁止静默回落并照常计费）"
            )
        self._adapter = adapter
        self._assembly = assembly
        self._stage = str(stage or "")
        self._source = str(source)
        self._fallback_reason = str(fallback_reason or "")
        self._clock = clock or _now
        self._adapter_ref = assembly.cfg.channel(assembly.channel_id).adapter

    @property
    def adapter(self):
        """**被包装的投放适配器**（其余方法经 `__getattr__` 转发；仅 `create_campaign` 被包）。"""
        return self._adapter

    @property
    def channel_id(self) -> str:
        return self._assembly.channel_id

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def source(self) -> str:
        return self._source

    def create_campaign(self, material, budget_usd, **kwargs):
        """门禁判定 → 平台调用 → 结算入账（一次调用 = 一条运行记录）。"""
        moment = self._clock()
        request = _ChannelSpendRequest(
            channel_id=self._assembly.channel_id,
            stage=self._stage,
            estimated_usd=budget_usd,
        )
        try:
            reservation = self._assembly.guard.check(request)
        except BudgetRefusedError:
            # 调用前拒绝：平台 0 次调用、成本零入账（证据在 alerts.jsonl + 运行记录）
            self._write(moment=moment, result="refused")
            raise
        try:
            campaign = self._adapter.create_campaign(material, budget_usd, **kwargs)
        except Exception:  # noqa: BLE001 - 失败也要留痕并释放预留，异常原样上抛
            reservation.settle(0.0)
            self._write(moment=moment, result="failed")
            raise
        actual = getattr(campaign, "spent_usd", None)
        reservation.settle(budget_usd if actual is None else float(actual))
        self._write(moment=moment, result="ok")
        return campaign

    def __getattr__(self, name: str):
        """其余属性与方法一律转发（`get_status` / `pause` / `fetch_metrics` / …）。"""
        return getattr(self._adapter, name)

    def _write(self, *, moment, result: str) -> None:
        if not self._stage:
            # 无环节归属（缺 stage= 的拒绝）：证据在 alerts.jsonl（与 RecordingGateway 同口径）
            return
        append_run(
            self._assembly.channel_id,
            cfg=self._assembly.cfg,
            root=self._assembly.root,
            moment=moment,
            stage=self._stage,
            source=self._source,
            adapter_ref=self._adapter_ref,
            profile_id="",
            result=result,
            cost_source=COST_SOURCE_MEASURED,
            fallback_reason=self._fallback_reason,
        )


@dataclass(frozen=True)
class _ChannelSpendRequest:
    """门禁请求的最小结构化形态（`SpendRequest` 协议：三个只读属性）。"""

    channel_id: str
    stage: str
    estimated_usd: float


def _now() -> datetime:
    return datetime.now(UTC)
