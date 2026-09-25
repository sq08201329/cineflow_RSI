"""每周信度报告（功能 010 US2 契约 C6；功能 020 起口径进产物 + 按轮并留存零覆盖）。

build_report 从台账（ledger/{agent_id}/{evaluator_id}.jsonl）取本周期记录，
产出 `reports/{period}-{run_id}.json`（**同周期多轮并留存、零覆盖**；缺省 `run_id`
= **兼容别名路径** `reports/{period}.json`）：per agent per evaluator 的相关系数
（pearson_r 或 kendall_tau）+ samples + meets_target（≥ calibration.reliability_target，
默认 0.6）；负相关记录入 alerts（只告警不自动反向调权，原则六）。

窗口口径（`window_semantics` / `period_days` / `window{start,end,period_days}` /
`window_semantics_change_date` / `note`）随产物回写：口径**取值域外一律拒绝落盘**，
跨"口径变更日"的窗口在 `note` 显式标注（禁止静默比较两段不同口径的窗口）。

**零覆盖（功能 020，契约 C3/C5 的产物层规则）**：任何**已存在**路径
（轮级报告与兼容别名**同规则**）的改写一律抛错——同内容重复调用是幂等的（不写、不报错），
内容不同则拒绝（"后写覆盖前写"次数恒 0）；兼容别名因此只在**首次**落盘时写出，此后永不改写。
"同周期多轮"的每轮各落 `reports/{period}-{run_id}.json` ⇒ 文件数 == 轮数、两者都保留。
"""

import json
from datetime import date
from pathlib import Path

from core.calibration.errors import CalibrationReportConflictError
from core.calibration.periods import WINDOW_SEMANTICS, cadence_of, period_start, period_window
from core.evaluators.errors import ValidationError


def _correlation_of(record: dict) -> tuple[str, float | None]:
    """取记录的相关口径：连续 → pearson_r；judge → kendall_tau。"""
    if record.get("kendall_tau") is not None:
        return "kendall_tau", record["kendall_tau"]
    return "pearson_r", record.get("pearson_r")


def report_path(data_dir: str | Path, period: str, run_id: str | None = None) -> Path:
    """报告路径规则**单点**：`run_id` 给定 ⇒ `reports/{period}-{run_id}.json`；
    缺省 ⇒ 兼容别名 `reports/{period}.json`（010 既有读取点据此取到同一份内容）。"""
    name = f"{period}-{run_id}.json" if run_id else f"{period}.json"
    return Path(data_dir) / "reports" / name


def latest_report_path(data_dir: str | Path, period: str) -> Path | None:
    """该周期**最新一份**报告的路径（读取口唯一入口）。

    **确定性规则（不依赖目录遍历顺序、不依赖文件系统时间戳）**：

    1. 轮级报告（`reports/{period}-{run_id}.json`）优先，取 **`run_id` 字符串序的末者**
       ——`run_id` 由 `core.tree.models.new_id` 产出（uuid7，**时间有序**）⇒ 字符串序
       即写入时间序；自定义 `run_id` 时同样按字符串序（仍然确定、可复现，不"猜最新"）；
    2. 无轮级报告 ⇒ 回退**兼容别名** `reports/{period}.json`（010 既有读取点据此取到同一份内容）；
    3. 两者皆无 ⇒ `None`（如实报"无报告"，不报错、不编造）。

    为什么不用 mtime：mtime 会因复制/检出/`touch` 而变，是比 `run_id` 更弱的不确定源；
    轮标识本身已是"第几轮"的可比较键（任务清单 T2040④ 的"排序末者"即此规则）。
    """
    directory = Path(data_dir) / "reports"
    by_run = (
        sorted(directory.glob(f"{period}-*.json"), key=lambda path: path.name)
        if (directory.is_dir())
        else []
    )
    if by_run:
        return by_run[-1]
    alias = report_path(data_dir, period)
    return alias if alias.is_file() else None


def _current_record(records: list[dict], period: str, run_id: str | None) -> dict | None:
    """取本周期**本轮**的台账行（"同周期取末行" → "**按轮并留存**"）。

    `run_id` 给定时优先取 `round_id == run_id` 的行（本轮证据）；历史行无 `round_id`
    或该轮无行 ⇒ 退回本周期末行（兼容 010 落地前的历史台账，如实呈现而不静默空报告）。
    """
    scoped = [record for record in records if record.get("period") == period]
    if not scoped:
        return None
    if run_id is not None:
        matched = [record for record in scoped if record.get("round_id") == run_id]
        if matched:
            return matched[-1]
    return scoped[-1]


def _window_of(period: str) -> tuple[int, date, date]:
    """窗口端点：`period_days` + 半开 `[start, start + period_days)`（端点只来自 periods）。"""
    period_days = cadence_of(period)
    start_day, open_end = period_window(period_start(period, period_days), period_days)
    return period_days, start_day, open_end


def _note_of(start_day: date, open_end: date, change_date: date) -> str:
    """跨口径变更日的标注（C5）：窗口横跨变更日必须标注，整体早于变更日则如实注明。"""
    if start_day < change_date < open_end:
        return (
            f"窗口横跨口径变更日 {change_date.isoformat()}（半开口径），"
            "口径变更日两侧的窗口不可直接比较"
        )
    if open_end <= change_date:
        return f"窗口整体早于口径变更日 {change_date.isoformat()}（半开口径）"
    return ""


def build_report(
    data_dir: str | Path,
    period: str,
    *,
    target: float,
    window_semantics: str,
    window_semantics_change_date: str,
    run_id: str | None = None,
) -> dict:
    """生成周期信度报告并落盘；返回报告 dict。

    既有四键（`period`/`agents`/`target`/`alerts`）**逐字保留**；窗口口径键
    （`period_days`/`window_semantics`/`window_semantics_change_date`/`run_id`/
    `window{start,end,period_days}`/`note`）为功能 020 新增。

    **零覆盖**：目标路径已存在时，同内容 ⇒ 幂等返回既有产物（零字节写入）；
    内容不同 ⇒ 抛 `CalibrationReportConflictError`（"覆盖成功"路径不存在）。
    """
    if window_semantics != WINDOW_SEMANTICS:
        raise ValidationError(
            f"窗口口径取值域外：{window_semantics!r}（唯一取值 {WINDOW_SEMANTICS}），拒绝落盘"
        )
    try:
        change_date = date.fromisoformat(window_semantics_change_date)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "窗口口径变更日必须为 ISO 日期（YYYY-MM-DD）："
            f"{window_semantics_change_date!r}（{exc}）"
        ) from exc

    data_dir = Path(data_dir)
    agents: dict[str, dict] = {}
    alerts: list[dict] = []
    ledger_root = data_dir / "ledger"
    if ledger_root.is_dir():
        for agent_dir in sorted(ledger_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            for ledger_file in sorted(agent_dir.glob("*.jsonl")):
                records = [
                    json.loads(line)
                    for line in ledger_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                # 本周期**本轮**一条（同周期多轮各自留存于台账；报告按轮落盘，两者配对）
                record = _current_record(records, period, run_id)
                if record is None:
                    continue
                metric, value = _correlation_of(record)
                meets_target = value is not None and value >= target
                entry = {
                    metric: value,
                    "samples": record["samples"],
                    "meets_target": meets_target,
                }
                agents.setdefault(agent_dir.name, {})[record["evaluator_key"]] = entry
                if value is not None and value < 0:
                    alerts.append(
                        {"evaluator": record["evaluator_key"], "reason": "negative_correlation"}
                    )

    period_days, start_day, open_end = _window_of(period)
    report = {
        "period": period,
        "agents": agents,
        "target": target,
        "alerts": alerts,
        "period_days": period_days,
        "window_semantics": window_semantics,
        "window_semantics_change_date": change_date.isoformat(),
        "run_id": run_id,
        "window": {
            "start": start_day.isoformat(),
            "end": open_end.isoformat(),
            "period_days": period_days,
        },
        "note": _note_of(start_day, open_end, change_date),
    }
    path = report_path(data_dir, period, run_id)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") == payload:
            return report  # 同内容 ⇒ 幂等（零字节写入，mtime 与字节均不变）
        raise CalibrationReportConflictError(
            f"信度报告不可改写：{path}（已存在且内容不同）——"
            "同周期多轮必须各带 run_id 落轮级报告，兼容别名只在首次落盘时写出、此后永不改写"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return report
