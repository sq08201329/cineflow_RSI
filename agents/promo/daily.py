"""日级指标回流与覆盖窗口（功能 020 契约 C6~C10；业务侧库函数，CLI 只作薄封装）。

分层（镜像 `agents/promo/ingest.py`）：**库函数承载全部判定**，`ops/ingest_metrics.py`
只做参数解析与装配（**零判定逻辑**）。

四个入口：

- `daily_node_id(material_id, period)`：每周期一个节点 id（周期进 id，(活动, 周期) 才是身份）；
- `record_daily_ingest(...)`：插入一条 `promo_daily_metrics` 行；
  同 `(campaign_id, period)` 已存在 ⇒ 返回 `False`（**幂等拒绝、零变更、整批不中断**）；
- `ingest_daily(...)`：按采集日分片回流一轮（候选 `delivered|ingested`）→ 归属日解析 →
  幂等插入 → 周期派生节点一次性落盘 → 运营表更新；
- `daily_coverage(...)`：日级覆盖窗口（**按归属日聚合**，`covered_days` 只计 `source == "real"`）。

## 两条规则分属两层，**不得互相吞并**（契约 C6）

① **快照层（按事件）**：唯一性键 =（活动, 周期）⇒ 同键再次回流 = **幂等拒绝**；
② **外环产物层（按轮）**：同一周期内多轮的台账行与信度报告**并留存、零覆盖**
（路径与台账行由 `core/calibration/{ledger,report}.py` 承担，本模块不重复判定）。

## 判定的唯一实现（U-01）

`daily_coverage` 的"覆盖 ∧ 连续"判定**必须委托** `core/calibration/periods.py::coverage_window`
（唯一实现）——本模块只做"取行 → 组装归属日集合 → 调 `coverage_window` → 回填日级专属键"，
**不重算** `covered_days` / `max_gap_days` / `continuous` / `meets` / `gaps` / `reasons`。
"""

import time
from dataclasses import asdict
from datetime import date, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agents.promo.anchors import attribution_fallback_count
from agents.promo.db import promo_campaigns, promo_daily_metrics
from agents.promo.loop import _round_root_id, round_tree_id
from agents.promo.platform.base import MetricSnapshot, validate_metrics
from core.billing.runlog import RUN_SOURCES
from core.calibration.ledger import snapshot_fingerprint
from core.calibration.periods import WINDOW_SEMANTICS, coverage_window, period_label
from core.evaluators.errors import ValidationError
from core.tree.errors import DuplicateError
from core.tree.models import new_id

#: 回流**候选行**的运营状态：首日 `delivered`、后续日 `ingested`。
#: （只取 `delivered` 是"同一活动无法连续多日各采一次"的直接根因——本常量即该放宽的落点。
#: 定义在本模块以避免 `agents/promo/{ingest,daily}.py` 的循环 import。）
INGEST_CANDIDATE_STATUSES = ("delivered", "ingested")

# 日级覆盖视图的键集以 `contracts/daily-ingest.md` 的 C9 为**唯一权威**（本模块不另立取舍）
COVERAGE_KEYS = (
    "end",
    "period_days",
    "window_semantics",
    "attribution_based",
    "covered_days",
    "covered_dates",
    "gaps",
    "max_gap_days",
    "continuous",
    "min_window_days",
    "gap_tolerance_days",
    "meets",
    "coverage_shortfall_days",
    "gap_shortfall_days",
    "reasons",
    "days",
    "legacy_single_snapshots",
    "attribution_missing_anchors",
    "evidence_claim",
    "note",
)

#: 日级覆盖视图 `days[]` 条目声明的键（三时间并列 + 来源 + 活动/周期）
DAY_KEYS = ("metric_date", "collected_at", "platform_timestamp", "source", "campaign_id", "period")

#: `evidence_claim` 的取值域二元素（诚实分层：未满足前提时禁止宣称"真实回流已达成"）
EVIDENCE_MECHANISM_READY = "mechanism_ready_real_feedback_pending"
EVIDENCE_REAL_MET = "real_feedback_met"

# 来源的"强弱"优先级（同一归属日多条记录合并为一条 days[] 条目时取最强来源）：
# **由 RUN_SOURCES 派生**（取值域唯一属主，不在本模块重复声明字面量）；
# `days[]` 中 `source == "real"` 的日期集合必须**逐日等于** `covered_dates`（C9 机检）。
_SOURCE_PRECEDENCE = (RUN_SOURCES[0], RUN_SOURCES[2], RUN_SOURCES[1])


def daily_node_id(material_id: str, period: str) -> str:
    """每周期一个节点 id：`f"{material_id}-node@{period}"`（确定性派生）。

    同一 `(material_id, period)` 恒等、不同必不相同；**历史 `f"{material_id}-node"`
    节点一律不回改**（已落盘节点是冻结证据，原则一/二）。
    """
    if not isinstance(material_id, str) or not material_id:
        raise ValidationError(f"material_id 必须为非空字符串，实际为 {material_id!r}")
    if not isinstance(period, str) or not period:
        raise ValidationError(f"period 必须为非空字符串，实际为 {period!r}")
    return f"{material_id}-node@{period}"


def _require_source(source: str, *, fallback_reason: str | None) -> str:
    """来源取值域校验（唯一属主 = `core/billing/runlog.py` 的 `RUN_SOURCES`）。

    域外 ⇒ 拒绝（不猜、不默认、零落盘）；`fallback` 必须显式声明**非空原因**
    （镜像 `core/billing/runlog.py` 的硬校验口径；本特性**不在** `agents/promo/` 内
    新增回落路径，故 `fallback` 的持久原因落在 019 的运行记录/告警侧）。
    """
    if source not in RUN_SOURCES:
        raise ValidationError(
            f"回流来源取值域外：{source!r}（唯一取值域 {list(RUN_SOURCES)}，"
            "由装配面显式声明；域外 ⇒ 拒绝且零落盘，不猜、不降级）"
        )
    if source == "fallback" and not fallback_reason:
        raise ValidationError(
            "source=fallback 必须声明原因（fallback_reason 非空）——本特性不新增回落路径，"
            "回落若发生必须显式声明并如实标注来源"
        )
    return source


def _as_date(value: str | date, *, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{where} 必须为 ISO 日期（YYYY-MM-DD），实际为 {value!r}") from exc


def record_daily_ingest(
    engine: Engine,
    *,
    campaign_id: str,
    round_id: str,
    external_id: str,
    material_id: str,
    snapshot: MetricSnapshot,
    period: str,
    collected_at: float,
    source: str,
    node_id: str,
    fallback_reason: str | None = None,
) -> bool:
    """插入一条日级回流记录；同 `(campaign_id, period)` 已存在 ⇒ `False`（幂等拒绝、零变更）。

    **写入即冻结**：行由 `promo_daily_metrics` 的 INSERT-only 触发器保护，
    插入后 `UPDATE`/`DELETE` 一律被拒（存储层，SC-008）。
    """
    _require_source(source, fallback_reason=fallback_reason)
    metric_date = snapshot.metric_date
    if metric_date is None:
        raise ValidationError("平台未提供指标归属日（metric_date）：该条不落日级回流记录")
    payload = asdict(snapshot)
    values = {
        "ingest_id": new_id(),
        "campaign_id": campaign_id,
        "round_id": round_id,
        "external_id": external_id,
        "material_id": material_id,
        "period": period,
        "metric_date": metric_date,
        "collected_at": float(collected_at),
        "platform_timestamp": float(snapshot.platform_timestamp),
        "source": source,
        "snapshot_fingerprint": snapshot_fingerprint(payload),
        "node_id": node_id,
        "metrics": payload,
        "created_at": time.time(),
    }
    try:
        with engine.begin() as conn:
            with conn.begin_nested():
                conn.execute(insert(promo_daily_metrics).values(**values))
    except IntegrityError:
        return False  # 唯一键（活动, 周期）已存在 ⇒ 幂等拒绝（不是覆盖）
    return True


def ingest_daily(
    round_id: str,
    store,
    adapter,
    engine: Engine,
    config,
    *,
    source: str,
    metric_date: str | None = None,
    fallback_reason: str | None = None,
    collected_at: float | None = None,
) -> dict:
    """按采集日分片回流一轮（契约 C6）。

    流程：候选 `delivered|ingested` → `fetch_metrics` → **写入路径校验**
    （越界或归属日缺失即该条 `rejected`、零落盘、整批不中断）→ 归属日解析 →
    周期派生（`period_label(metric_date, config.period_days)`）→ 幂等插入 →
    周期节点一次性落盘 → 运营表更新。

    返回 `{round_id, ingested, rejected[{campaign_id, period, reason}], skipped, periods}`。

    `metric_date`：**显式声明**的归属日覆盖（离线跨日演练用），取值必须为 ISO 日期；
    **不得**由采集时刻或 `platform_timestamp` 派生——本函数从不派生归属日，
    缺归属日时该条直接失败（原因「平台未提供指标归属日」）。
    `source`：由**装配面显式声明**（取值域 `RUN_SOURCES`；域外 ⇒ 拒绝且零落盘）。
    """
    # 函数级 import：`agents.promo.ingest` 在模块级 import 本模块（节点 id 与候选状态
    # 的唯一实现），此处反向引用其**共用**的节点构造纪律（不得各写一套校验/得分）
    from agents.promo.ingest import build_metrics_node

    _require_source(source, fallback_reason=fallback_reason)
    if metric_date is not None:
        _as_date(metric_date, where="显式声明的归属日（--metric-date）")
    now = time.time() if collected_at is None else float(collected_at)

    with engine.connect() as conn:
        rows = conn.execute(
            select(promo_campaigns).where(
                promo_campaigns.c.round_id == round_id,
                promo_campaigns.c.status.in_(INGEST_CANDIDATE_STATUSES),
            )
        ).all()

    report: dict = {
        "round_id": round_id,
        "ingested": [],
        "rejected": [],
        "skipped": 0,
        "periods": [],
    }
    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)

    for row in rows:
        try:
            snapshot = adapter.fetch_metrics(row.external_id)
        except Exception as exc:  # PlatformError 族：**按条拒绝、整批不中断**、不改运营表状态
            # （理由同单快照路径：平台侧故障可重试，多日分片下历史活动可能已不在平台侧）
            report["rejected"].append(
                {
                    "campaign_id": row.campaign_id,
                    "period": None,
                    "reason": f"平台采集失败：{exc}",
                    "retryable": True,
                }
            )
            continue
        try:
            validate_metrics(snapshot)  # **写入路径**校验：越界或归属日缺失即失败
        except Exception as exc:  # MetricValidationError 是 PlatformError 子类
            report["rejected"].append(
                {"campaign_id": row.campaign_id, "period": None, "reason": str(exc)}
            )
            continue

        resolved = metric_date if metric_date is not None else snapshot.metric_date
        if resolved is None:
            report["rejected"].append(
                {
                    "campaign_id": row.campaign_id,
                    "period": None,
                    "reason": "平台未提供指标归属日（metric_date）",
                }
            )
            continue

        period = period_label(resolved, config.period_days)
        node_id = daily_node_id(row.material_id, period)
        # 幂等插入先于节点落盘：同（活动, 周期）第二次采集**零变更**、节点数不变
        if not record_daily_ingest(
            engine,
            campaign_id=row.campaign_id,
            round_id=round_id,
            external_id=row.external_id,
            material_id=row.material_id,
            snapshot=snapshot,
            period=period,
            collected_at=now,
            source=source,
            node_id=node_id,
            fallback_reason=fallback_reason,
        ):
            report["rejected"].append(
                {
                    "campaign_id": row.campaign_id,
                    "period": period,
                    "reason": "同（活动, 周期）已回流：幂等拒绝（零变更）",
                }
            )
            continue

        metrics_payload = row.metrics or {}
        node = build_metrics_node(
            metrics_payload=metrics_payload,
            snapshot=snapshot,
            config=config,
            node_id=node_id,
            material_id=row.material_id,
            tree_id=tree_id,
            root_id=root_id,
            created_at=now,  # 回流节点 created_at 保持**采集墙钟**（语义不变）
            metric_date=resolved,
            period=period,
        )
        try:
            store.append_node(node)  # 一次性完整 INSERT，落盘即冻结
        except DuplicateError:
            report["skipped"] += 1  # 幂等：同 (material_id, period) 节点已落盘
        else:
            report["ingested"].append(row.material_id)
        with engine.begin() as conn:
            conn.execute(
                update(promo_campaigns)
                .where(promo_campaigns.c.campaign_id == row.campaign_id)
                .values(
                    status="ingested",
                    node_id=node.node_id,
                    metrics={**metrics_payload, "platform_metrics": asdict(snapshot)},
                    updated_at=now,
                )
            )
        if period not in report["periods"]:
            report["periods"].append(period)
    return report


# ---------------------------------------------------------------------------
# 归因缺失计数与日级覆盖窗口（C7/C9）
#
# `attribution_fallback_count` 的**唯一实现**在 `agents/promo/anchors.py`（锚点侧的读口径），
# 此处经 import 复用（不重写第二份），使覆盖视图的 `attribution_missing_anchors` 与之同源。
# ---------------------------------------------------------------------------


def _legacy_single_snapshots(engine: Engine, daily_campaigns: set[str]) -> int:
    """历史"一次活动一次快照"行数：运营表有 `platform_metrics` 而日级表无该活动行。

    这些行的**归属日未标定**：不参与任何归属日、不计入 `covered_days`（C6 兼容规则）。
    """
    with engine.connect() as conn:
        rows = conn.execute(select(promo_campaigns.c.campaign_id, promo_campaigns.c.metrics)).all()
    total = 0
    for row in rows:
        if not (row.metrics or {}).get("platform_metrics"):
            continue
        if row.campaign_id in daily_campaigns:
            continue
        total += 1
    return total


def daily_coverage(
    engine: Engine,
    *,
    end: str | date,
    min_window_days: int,
    gap_tolerance_days: int,
    period_days: int,
    anchors_engine: Engine | None = None,
    required_since: str | None = None,
) -> dict:
    """日级覆盖窗口（**按归属日聚合**；`covered_days` 只计 `source == "real"`）。

    **判定唯一实现**：`covered_days` / `gaps` / `max_gap_days` / `continuous` / `meets` /
    `coverage_shortfall_days` / `gap_shortfall_days` / `reasons` 全部来自
    `core/calibration/periods.py::coverage_window`（本函数不重算任一量）。

    输出键逐键对齐契约 C9（`COVERAGE_KEYS`）；`days[]` 每条同时含
    `metric_date` / `collected_at` / `platform_timestamp`（三时间并列，FR-004/006），
    缺口**逐段如实列出**、**不插值补齐**。

    `anchors_engine` / `required_since`：锚点侧"归属日缺失（按 created_at 回退）"
    的计数来源（`attribution_fallback_count`）；`required_since` 缺省 ⇒ **不计数**并在
    `note` 如实标注"未标定"（不冒充已计）。
    """
    end_day = _as_date(end, where="覆盖窗口右端（end）")
    rows = _daily_rows(engine, end_day)

    observed: dict[date, set[str]] = {}
    slots: dict[str, dict] = {}
    unlabeled = 0
    for row in rows:
        if not row.metric_date:
            unlabeled += 1
            continue
        observed.setdefault(date.fromisoformat(row.metric_date), set()).add(row.source)
        slot = slots.get(row.metric_date)
        if slot is None:
            slots[row.metric_date] = {"row": row, "sources": {row.source: 1}}
        else:
            slot["sources"][row.source] = slot["sources"].get(row.source, 0) + 1
            # 同一归属日合并：三时间取该日**最晚采集**那次读取的值
            if row.collected_at >= slot["row"].collected_at:
                slot["row"] = row

    base = coverage_window(
        observed,
        end=end_day,
        min_window_days=min_window_days,
        gap_tolerance_days=gap_tolerance_days,
    )

    legacy = _legacy_single_snapshots(engine, {row.campaign_id for row in rows})
    missing_anchors = 0
    if required_since is not None:
        missing_anchors = attribution_fallback_count(
            anchors_engine if anchors_engine is not None else engine,
            required_since=required_since,
        )

    covered_dates = base["covered_dates"]
    # `real_feedback_met` 仅当达标**且**每个覆盖日**只**来自真实来源（混合日不得冒充达成）
    exclusively_real = all(observed[date.fromisoformat(day)] == {"real"} for day in covered_dates)
    evidence_claim = (
        EVIDENCE_REAL_MET if base["meets"] and exclusively_real else EVIDENCE_MECHANISM_READY
    )

    notes = [base["note"]]
    if missing_anchors:
        notes.append(
            f"归属日缺失（按 created_at 回退）锚点数：{missing_anchors}"
            "（回退只作用于历史行；新采集写入路径缺失即失败）"
        )
    if legacy:
        notes.append(f"历史单快照行 {legacy} 条（归属日未标定，不参与归属日、不计入 covered_days）")
    if unlabeled:
        notes.append(f"日级记录缺归属日的行 {unlabeled} 条（不参与归属日聚合）")
    if required_since is None:
        notes.append("未给定归属日必填口径生效日 ⇒ 回退锚点数未计（未标定，不冒充已计）")
    notes.append(
        "attribution_based=true：本窗口按**归属日**聚合（不是采集日、不是平台时间戳）；"
        "缺口逐段如实列出、不插值"
    )

    return {
        "end": base["end"],
        "period_days": int(period_days),
        "window_semantics": WINDOW_SEMANTICS,
        "attribution_based": True,
        "covered_days": base["covered_days"],
        "covered_dates": base["covered_dates"],
        "gaps": base["gaps"],
        "max_gap_days": base["max_gap_days"],
        "continuous": base["continuous"],
        "min_window_days": base["min_window_days"],
        "gap_tolerance_days": base["gap_tolerance_days"],
        "meets": base["meets"],
        "coverage_shortfall_days": base["coverage_shortfall_days"],
        "gap_shortfall_days": base["gap_shortfall_days"],
        "reasons": base["reasons"],
        "days": [_day_entry(slot["row"], slot["sources"]) for _, slot in sorted(slots.items())],
        "legacy_single_snapshots": legacy,
        "attribution_missing_anchors": missing_anchors,
        "evidence_claim": evidence_claim,
        "note": "；".join(part for part in notes if part),
    }


def _daily_rows(engine: Engine, end_day: date) -> list:
    """窗口内（归属日 ≤ end）的日级记录，按（归属日, 活动）升序（确定性顺序）。"""
    with engine.connect() as conn:
        rows = conn.execute(select(promo_daily_metrics)).all()
    kept = [
        row for row in rows if row.metric_date and date.fromisoformat(row.metric_date) <= end_day
    ]
    return sorted(kept, key=lambda row: (row.metric_date, row.campaign_id))


def _day_entry(row, source_counts: dict) -> dict:
    """`days[]` 条目：三时间并列 + 来源（+ 来源计数，契约 C9 的"保留 sources 计数"）。"""
    return {
        "metric_date": row.metric_date,
        "collected_at": float(row.collected_at),
        "platform_timestamp": float(row.platform_timestamp),
        "source": _strongest_source(set(source_counts)),
        "campaign_id": row.campaign_id,
        "period": row.period,
        "sources": dict(source_counts),
    }


def _strongest_source(sources: set[str]) -> str:
    """同一天多条记录合并时取**最强来源**（real > fallback > simulated）。

    这样 `days[]` 中 `source == "real"` 的日期集合**逐日等于** `covered_dates`；
    混合日的来源计数保留在 `sources` 里，模拟件因此不可能冒充真实（C10 机检）。
    """
    for candidate in _SOURCE_PRECEDENCE:
        if candidate in sources:
            return candidate
    raise ValidationError(f"日级记录的来源取值域外：{sorted(sources)}")
