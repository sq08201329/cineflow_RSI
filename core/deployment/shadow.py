"""影子模式与对照报告（功能 014 US2 / T1415，契约 C5 + FR-005/FR-012）。

影子期的语义：**门槛判定照跑、部署指针一律不动**——系统把"若 auto 已开启会放行哪些候选、
会拦截哪些候选及其理由"记成事件留痕，并对照同期人工决策产出报告。它把"门槛是否标定得当"
从上线后的赌注变成上线前的证据：报告里"系统会放行但人工拒绝"的案例正是门槛太松的直接信号。

- 事件留痕 `deployment/shadow/events.jsonl`（追加写，只增不改；**拦截的候选也要记**，
  否则差异分类永远取不到 `human_pass_sys_block`）；
- 报告 `deployment/shadow/{period}.json`（同刻同内容幂等；内容变化拒绝覆盖，原则二）；
- 误入率口径（SC-007 机检）：影子期分子 = "会放行但人工拒绝" ∪ "放行样本中被判不可接受"
  （**按候选去重**），分母 = 影子放行候选数（影子期无真实部署，不以部署数为分母）；
  `recompute_misadmission_rate` 只读事件留痕即可重算，与报告值一致；
- 本模块**没有任何指针写入口**——影子期指针变更次数恒 0 由"无写路径"保证（机检）。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from core.deployment import mode
from core.deployment.config import DeploymentConfig
from core.deployment.errors import (
    DeploymentEvidenceError,
    DeploymentRecordConflictError,
)
from core.deployment.models import (
    DiffCategory,
    GateDecision,
    HumanDecision,
    ShadowEvent,
    ShadowReport,
)
from core.evaluators.errors import ValidationError

SHADOW_SUBDIR = "shadow"
EVENTS_FILE = "events.jsonl"

# 影子期误入率口径（进报告 note 与 recompute 返回值：口径必须自描述、可被证伪）
MISADMISSION_DEFINITION = (
    "影子期误入率口径：分子 = 会放行但人工拒绝的候选 ∪ 放行样本中被人工判定不可接受的候选"
    "（按候选去重）；分母 = 影子放行的候选数（影子期无真实部署，故不以自动部署数为分母）；"
    "rate = 分子 / 分母（分母为 0 时如实为 None）"
)


def shadow_dir(data_dir: str | Path) -> Path:
    """影子期产物目录：deployment/shadow/。"""
    return Path(data_dir) / SHADOW_SUBDIR


def events_path(data_dir: str | Path) -> Path:
    """影子事件留痕：deployment/shadow/events.jsonl（追加写，只增不改）。"""
    return shadow_dir(data_dir) / EVENTS_FILE


def report_path(data_dir: str | Path, period: str) -> Path:
    """对照报告路径：deployment/shadow/{period}.json（data-model 口径，按周期分档）。"""
    return shadow_dir(data_dir) / f"{period}.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def default_period(at: str | None = None) -> str:
    """周期标签默认值：判定时刻的 ISO 周（与 010/012 报表同款口径）。"""
    moment = datetime.fromisoformat(at) if at else datetime.now(UTC)
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def record_shadow_event(
    agent_id: str,
    candidate_version: str,
    verdict: GateDecision | str,
    *,
    data_dir: str | Path,
    reason: str,
    period: str | None = None,
    human_decision: HumanDecision | str = HumanDecision.NONE,
    unacceptable: bool = False,
    at: str | None = None,
) -> ShadowEvent:
    """记录一个影子事件（放行与拦截**都记**）：差异分类由（放行与否 × 人工决策）推导。

    `unacceptable`：事后人工复核判定"即便自动接班也不可接受"（影子期误入率分子的第二项）。
    """
    moment = at or _now()
    if not isinstance(verdict, GateDecision):
        try:
            verdict = GateDecision(verdict)
        except ValueError as exc:
            raise ValidationError(f"verdict 必须为 GateDecision 取值，实际为 {verdict!r}") from exc
    event = ShadowEvent(
        period=period or default_period(moment),
        agent_id=agent_id,
        candidate_version=candidate_version,
        verdict=verdict,
        would_allow=verdict is GateDecision.ELIGIBLE,
        human_decision=human_decision,
        diff_category=ShadowEvent.classify(verdict is GateDecision.ELIGIBLE, human_decision),
        reason=reason,
        recorded_at=moment,
        unacceptable=unacceptable,
    )
    path = events_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return event


def _event_from_dict(payload: dict) -> ShadowEvent:
    """留痕读回（events.jsonl 一行一个事件；字段形态由模型层校验）。"""
    return ShadowEvent(
        period=payload["period"],
        agent_id=payload["agent_id"],
        candidate_version=payload["candidate_version"],
        verdict=payload["verdict"],
        would_allow=payload["would_allow"],
        human_decision=payload["human_decision"],
        diff_category=payload["diff_category"],
        reason=payload["reason"],
        recorded_at=payload["recorded_at"],
        unacceptable=payload.get("unacceptable", False),
    )


def load_shadow_events(
    data_dir: str | Path, *, period: str | None = None, agent_id: str | None = None
) -> list[ShadowEvent]:
    """读影子事件留痕（按记录顺序）；可按周期/Agent 过滤；无留痕 → 空列表。"""
    path = events_path(data_dir)
    if not path.is_file():
        return []
    events = [
        _event_from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if period is not None:
        events = [event for event in events if event.period == period]
    if agent_id is not None:
        events = [event for event in events if event.agent_id == agent_id]
    return events


def latest_events_by_candidate(events: list[ShadowEvent]) -> list[ShadowEvent]:
    """同候选只取最后一条（人工决策晚于判定到达）：报告按候选去重，不重复计样本。"""
    latest: dict[tuple[str, str], ShadowEvent] = {}
    for event in events:
        latest[(event.agent_id, event.candidate_version)] = event
    return list(latest.values())


def _misadmission_counts(events: list[ShadowEvent]) -> tuple[int, int]:
    """影子期误入率分子/分母（口径见 MISADMISSION_DEFINITION；事件已按候选去重）。"""
    passing = [event for event in events if event.would_allow]
    numerator = sum(
        1 for event in passing if event.human_decision is HumanDecision.REJECT or event.unacceptable
    )
    return numerator, len(passing)


def build_shadow_report(
    period: str,
    cfg: DeploymentConfig,
    *,
    data_dir: str | Path,
    agent_id: str,
    at: str | None = None,
) -> ShadowReport:
    """产出该周期该 Agent 的对照报告并落盘（只增不改）。

    报告按 (周期, Agent) 分档：同一周期的第二个 Agent 会撞名 → 显式报冲突（**已知限制**，
    文档化于此）：一期影子运行按 Agent 逐周期进行；多 Agent 并行影子需按周期错开或扩展命名。
    """
    moment = at or _now()
    events = latest_events_by_candidate(
        load_shadow_events(data_dir, period=period, agent_id=agent_id)
    )
    if not events:
        raise DeploymentEvidenceError(
            f"周期 {period} 的 {agent_id} 无影子事件留痕：不产对照报告（不伪造对照证据）"
        )

    passes = sum(1 for event in events if event.would_allow)
    blocks = len(events) - passes
    reason_distribution: dict[str, int] = {}
    for event in events:
        if not event.would_allow:
            reason_distribution[event.verdict.value] = (
                reason_distribution.get(event.verdict.value, 0) + 1
            )
    diff_counts = {category.value: 0 for category in DiffCategory}
    for event in events:
        diff_counts[event.diff_category.value] += 1

    numerator, denominator = _misadmission_counts(events)
    projected = mode.load_mode_state(
        data_dir, mode_default=cfg.mode_default, at=moment
    ).with_elapsed_shadow(moment)
    gap = projected.shadow_window_gap(
        min_days=cfg.shadow.min_days, min_candidates=cfg.shadow.min_candidates
    )
    report = ShadowReport(
        period=period,
        agent_id=agent_id,
        shadow_days=projected.shadow_days_accumulated,
        candidate_count=len(events),
        passes=passes,
        blocks=blocks,
        reason_distribution=reason_distribution,
        diff_counts=diff_counts,
        misadmission_numerator=numerator,
        misadmission_denominator=denominator,
        misadmission_rate=None if denominator == 0 else numerator / denominator,
        met=not gap,
        note=MISADMISSION_DEFINITION + ("；影子期已达标（双下限满足）" if not gap else f"；{gap}"),
        generated_at=moment,
    )
    _persist_report(data_dir, report)
    return report


def _persist_report(data_dir: str | Path, report: ShadowReport) -> Path:
    """报告落盘（只增不改）：同刻同内容幂等；内容变化拒绝覆盖既有报告。"""
    path = report_path(data_dir, report.period)
    text = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return path
        raise DeploymentRecordConflictError(
            f"影子对照报告只增不改：{path} 已存在且内容不同（不覆盖既有报告；"
            "证据变化请换周期档或另存）"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def recompute_misadmission_rate(data_dir: str | Path, period: str, *, agent_id: str) -> dict:
    """从事件留痕重算误入率（SC-007 机检）：只读留痕，不读报告自身。"""
    events = latest_events_by_candidate(
        load_shadow_events(data_dir, period=period, agent_id=agent_id)
    )
    numerator, denominator = _misadmission_counts(events)
    return {
        "agent_id": agent_id,
        "period": period,
        "numerator": numerator,
        "denominator": denominator,
        "rate": None if denominator == 0 else numerator / denominator,
        "definition": MISADMISSION_DEFINITION,
        "source": f"{SHADOW_SUBDIR}/{EVENTS_FILE}（周期 {period}，{agent_id}）",
    }
