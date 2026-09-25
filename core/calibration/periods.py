"""周期量纲与窗口口径（功能 020，契约 C1/C2/C3/C5）。

**业务无关**（宪章原则五）：本模块只认 `period_days`（cadence）这个**数值参数**，
零形态名、零 cadence 分支树（`1`/`7` 的判定即量纲本身，其余值一律拒绝）、零 IO、
零业务概念；**不 import `core/calibration/rounds.py`**（否则成环）、**不 import `core.billing`**
（`coverage_window` 是 019 覆盖口径的纯函数化，避免 core 内新增 `calibration → billing` 耦合）。

- 标签口径唯一实现：`period_label`（周级**逐字复用** `iso_week_label` 的 ISO 周语义，
  该函数由本模块提供、`core/calibration/rounds.py` 再导出）；
- 窗口口径唯一实现：`period_window`（**半开** `[start, start + period_days)`）+
  `default_period_bounds`（CLI 缺省窗口：含首尾跨 `period_days` 天）+
  `window_timestamps`（秒级端点，`core/calibration/selection.py:28` 的适配层据此实现）；
- `cadence_of` 让**既有无 cadence 参数的调用点**（`write_anchor_snapshots` / `append_ledger` 等，
  含 `ops/demo_web.py`、`ops/demo_judge_drift.py`、`tests/conftest.py` 夹具）不改签名即可
  由标签反推 `period_days`（标签形态与 cadence 在 `{1,7}` 上双射，是唯一确定性做法）；
- `coverage_window` 是"覆盖 ∧ 连续"判定的**唯一实现**（`agents/promo/daily.py` 的
  `daily_coverage` 必须委托它，`agents/promo/` 与 `ops/` 内不得自算 `meets` / `max_gap_days`）。
"""

import re
from datetime import UTC, date, datetime, time, timedelta

from core.evaluators.errors import ValidationError

# 窗口口径唯一取值（进产物；历史行缺失时读取端按 UNSPECIFIED 标注，不冒充 half_open）
WINDOW_SEMANTICS = "half_open"
UNSPECIFIED_WINDOW_SEMANTICS = "unspecified"

# cadence 取值域（不发明"月""双周"等第三档量纲）
SUPPORTED_CADENCES = (1, 7)
CADENCE_UNIT = {1: "day", 7: "week"}

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEK_PATTERN = re.compile(r"^(\d{4})-W(\d{2})$")


def _check_cadence(period_days: int) -> int:
    if isinstance(period_days, bool) or not isinstance(period_days, int):
        raise ValidationError(f"未支持的 cadence：{period_days!r}（取值域 {SUPPORTED_CADENCES}）")
    if period_days not in SUPPORTED_CADENCES:
        raise ValidationError(f"未支持的 cadence：{period_days!r}（取值域 {SUPPORTED_CADENCES}）")
    return period_days


def _as_date(day: str | date) -> date:
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    if isinstance(day, str) and _DATE_PATTERN.fullmatch(day):
        try:
            return date.fromisoformat(day)
        except ValueError as exc:
            raise ValidationError(f"非法日期：{day!r}（{exc}）") from exc
    raise ValidationError(f"日期必须为 ISO 日期（YYYY-MM-DD），实际为 {day!r}")


def iso_week_label(day: str | date) -> str:
    """ISO 日期 → 周期标签（YYYY-Www，取 ISO 周）。

    **唯一实现**（原 `core/calibration/rounds.py:28` 的实现整体移入本模块）：
    周级标签语义逐字节不变，既有读取点经 `core.calibration.rounds` 再导出零改动。
    """
    iso = _as_date(day).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def cadence_of(label: str) -> int:
    """周期标签 → cadence（`{1,7}` 双射）；形态非法 ⇒ `ValidationError`（不猜测）。"""
    if not isinstance(label, str):
        raise ValidationError(f"周期标签必须为字符串，实际为 {label!r}")
    if _DATE_PATTERN.fullmatch(label):
        _as_date(label)
        return 1
    matched = _WEEK_PATTERN.fullmatch(label)
    if matched:
        _week_start(int(matched.group(1)), int(matched.group(2)))
        return 7
    raise ValidationError(f"非法周期标签：{label!r}（须为 YYYY-MM-DD 或 YYYY-Www）")


def _week_start(year: int, week: int) -> date:
    try:
        return date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValidationError(f"不存在的 ISO 周：{year}-W{week:02d}（{exc}）") from exc


def period_label(day: str | date, period_days: int) -> str:
    """ISO 日期 → 周期标签：`1` ⇒ `YYYY-MM-DD`；`7` ⇒ `YYYY-Www`；其余 ⇒ `ValidationError`。"""
    _check_cadence(period_days)
    if period_days == 1:
        return _as_date(day).isoformat()
    return iso_week_label(day)


def period_start(label: str, period_days: int) -> date:
    """周期标签 → 窗口首日；标签与 cadence 不匹配、或非法标签 ⇒ `ValidationError`。"""
    cadence = cadence_of(label)
    if cadence != _check_cadence(period_days):
        raise ValidationError(
            f"未支持的 cadence：标签 {label!r} 的 cadence 为 {cadence}，"
            f"与 period_days={period_days} 不符"
        )
    if cadence == 1:
        return _as_date(label)
    matched = _WEEK_PATTERN.fullmatch(label)
    return _week_start(int(matched.group(1)), int(matched.group(2)))


def period_window(start_day: str | date, period_days: int) -> tuple[date, date]:
    """周期窗口：**半开** `[start, start + period_days)`（按日历日）。"""
    _check_cadence(period_days)
    start = _as_date(start_day)
    return start, start + timedelta(days=period_days)


def period_regex(period_days: int) -> re.Pattern[str]:
    """周期标签形态正则：`1` ⇒ `^\\d{4}-\\d{2}-\\d{2}$`；`7` ⇒ `^(\\d{4})-W(\\d{2})$`。"""
    _check_cadence(period_days)
    return _DATE_PATTERN if period_days == 1 else _WEEK_PATTERN


def default_period_bounds(
    period_start_arg: str | None, period_end: str | None, period_days: int, *, today: date
) -> tuple[str, str]:
    """CLI 缺省窗口（**含首尾**入参语义）：`period_end` 缺省取 `today`、
    `period_start` 缺省取 `today − (period_days − 1)` ⇒ 与半开窗口合成后跨 `period_days` 天。

    显式给定的两个端点**原样返回**（不重算、不改写），跨度必须与 cadence 一致：
    `(period_end − period_start) + 1 天 != period_days` ⇒ `ValidationError`（禁止静默截断）。
    """
    _check_cadence(period_days)
    end = period_end or today.isoformat()
    start = period_start_arg or (today - timedelta(days=period_days - 1)).isoformat()
    start_day, end_day = _as_date(start), _as_date(end)
    if start_day > end_day:
        raise ValidationError(f"周期起点晚于终点：{start} > {end}（非空窗口才可判）")
    if (end_day - start_day).days + 1 != period_days:
        raise ValidationError(
            f"窗口跨度与 cadence 不符：{start} ~ {end} 含首尾跨 "
            f"{(end_day - start_day).days + 1} 天，period_days={period_days}"
        )
    return start, end


def window_timestamps(start_day: str | date, period_days: int) -> tuple[float, float]:
    """窗口的**秒级**半开区间（UTC 零点对齐）：起始日 00:00Z → `+period_days` 天 00:00Z。"""
    start, end = period_window(start_day, period_days)
    return _midnight_utc(start), _midnight_utc(end)


def _midnight_utc(day: date) -> float:
    return datetime.combine(day, time.min, tzinfo=UTC).timestamp()


def coverage_window(
    days, *, end: str | date, min_window_days: int, gap_tolerance_days: int
) -> dict:
    """窗口机检（**覆盖 ∧ 连续**双条件、缺口逐段如实列出、**禁止插值补齐**）。

    `days` = 观测日记录（`{date: 来源集合}` 或 `(date, 来源集合)` 对的可迭代）：
    **有记录**的日期参与断档枚举（"断档 = 没有任何记录的日期"），
    其中含 `real` 来源者进 `covered_dates`（模拟/回落日不算真实运行日）——
    与 019 `core/billing/runlog.py:226` 的 `window_coverage` **同构**（同名键），
    但本函数是**纯函数**：输入即日期集合与两个阈值，不读账本、不 import `core.billing`。
    """
    end_date = _as_date(end)
    observed: dict[date, set[str]] = {}
    for item in days.items() if isinstance(days, dict) else days:
        day, sources = item
        observed.setdefault(_as_date(day), set()).update(str(source) for source in sources)
    for day in list(observed):
        if day > end_date:
            del observed[day]

    covered = sorted(day for day, sources in observed.items() if "real" in sources)
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

    covered_days = len(covered)
    max_gap_days = max((gap["days"] for gap in gaps), default=0)
    tolerance = int(gap_tolerance_days)
    meets = covered_days >= int(min_window_days) and max_gap_days <= tolerance
    coverage_shortfall = max(0, int(min_window_days) - covered_days)
    gap_shortfall = max(0, max_gap_days - tolerance)
    reasons: list[str] = []
    if coverage_shortfall:
        reasons.append(
            f"覆盖不足：covered_days={covered_days} < min_window_days={int(min_window_days)}"
        )
    if gap_shortfall:
        reasons.append(f"断档超容差：max_gap_days={max_gap_days} > gap_tolerance_days={tolerance}")
    return {
        "end": end_date.isoformat(),
        "covered_days": covered_days,
        "covered_dates": [day.isoformat() for day in covered],
        "gaps": gaps,
        "max_gap_days": max_gap_days,
        "continuous": not gaps,
        "min_window_days": int(min_window_days),
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
