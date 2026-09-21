"""账目汇总与对账（功能 015 阶段 2 / T1510，契约 C4）。

按阶段与形态汇总运行成本，并与各来源（业务侧各 Agent 的落盘账目）**逐项对账**：
键集必须一致（不多不少）、金额超容差即 `LedgerMismatchError`——账目对不上必须报错，
不允许静默取其一（原则二的成本可核对性）：账目是样片包的一部分，对不上即证据不可信。

**零业务概念**：本模块只认识 stage_id、金额与形态值（`form` 仅作汇总分档），
不认识任何环节语义；"各 Agent 成本"由调用方以键值给出（键即阶段标识）。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.evaluators.errors import ValidationError
from core.orchestration.errors import LedgerMismatchError
from core.orchestration.models import RunRecord

DEFAULT_TOLERANCE_USD = 1e-9


@dataclass(frozen=True)
class CostLine:
    """单个阶段的对账行：记录成本 vs 来源账目成本 vs 差额。"""

    stage_id: str
    recorded_usd: float
    ledger_usd: float
    delta_usd: float

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "recorded_usd": self.recorded_usd,
            "ledger_usd": self.ledger_usd,
            "delta_usd": self.delta_usd,
        }


@dataclass(frozen=True)
class CostLedger:
    """账目汇总（对账已通过的快照）：按阶段/来源/形态的合计 + 逐项对账行。"""

    run_id: str
    form: str
    total_usd: float
    by_stage: Mapping[str, float]
    by_source: Mapping[str, float]
    by_form: Mapping[str, float]
    lines: tuple[CostLine, ...]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "form": self.form,
            "total_usd": self.total_usd,
            "by_stage": dict(self.by_stage),
            "by_source": dict(self.by_source),
            "by_form": dict(self.by_form),
            "lines": [line.to_dict() for line in self.lines],
        }


def summarize_cost(
    record: RunRecord,
    agent_ledgers: Mapping[str, float],
    *,
    tolerance_usd: float = DEFAULT_TOLERANCE_USD,
) -> CostLedger:
    """汇总并对账：`agent_ledgers` 的键即阶段标识（业务侧各 Agent 的落盘成本）。

    对账口径：键集必须与运行记录的阶段集合一致；每阶段金额差 ≤ `tolerance_usd`
    （默认 1e-9，仅吸收浮点误差）；任一不符即 `LedgerMismatchError`。
    """
    if (
        isinstance(tolerance_usd, bool)
        or not isinstance(tolerance_usd, int | float)
        or (tolerance_usd < 0)
    ):
        raise ValidationError(f"tolerance_usd 必须为非负数，实际为 {tolerance_usd!r}")
    ledger_amounts: dict[str, float] = {}
    for stage_id, amount in agent_ledgers.items():
        if not isinstance(stage_id, str) or not stage_id:
            raise ValidationError(f"账目键必须为非空字符串，实际为 {stage_id!r}")
        if isinstance(amount, bool) or not isinstance(amount, int | float) or amount < 0:
            raise ValidationError(
                f"账目金额必须为非负数值（美元），实际为 {stage_id!r}: {amount!r}"
            )
        ledger_amounts[stage_id] = float(amount)

    recorded = {state.stage_id: state.cost_usd for state in record.stages}
    missing = sorted(set(recorded) - set(ledger_amounts))
    extra = sorted(set(ledger_amounts) - set(recorded))
    if missing or extra:
        raise LedgerMismatchError(
            "账目键集与运行记录阶段集合不一致（对账失败）："
            f"缺 {missing or '无'}，多 {extra or '无'}"
        )

    lines: list[CostLine] = []
    mismatched: list[str] = []
    for state in record.stages:
        recorded_usd = float(recorded[state.stage_id])
        ledger_usd = ledger_amounts[state.stage_id]
        delta = abs(recorded_usd - ledger_usd)
        lines.append(
            CostLine(
                stage_id=state.stage_id,
                recorded_usd=recorded_usd,
                ledger_usd=ledger_usd,
                delta_usd=recorded_usd - ledger_usd,
            )
        )
        if delta > tolerance_usd:
            mismatched.append(
                f"{state.stage_id}: 记录 {recorded_usd} vs 账目 {ledger_usd}（差 {delta}）"
            )
    if mismatched:
        raise LedgerMismatchError("账目对账不一致（不允许静默取其一）：" + "；".join(mismatched))

    by_stage = {state.stage_id: float(recorded[state.stage_id]) for state in record.stages}
    total = sum(by_stage.values())
    by_source: dict[str, float] = {stage_id: ledger_amounts[stage_id] for stage_id in by_stage}
    return CostLedger(
        run_id=record.run_id,
        form=record.form,
        total_usd=total,
        by_stage=by_stage,
        by_source=by_source,
        by_form={record.form: total},
        lines=tuple(lines),
    )


def ledger_payload(ledger: CostLedger) -> dict[str, Any]:
    """样片包 `cost.json` 的载荷（金额 + 对账结果，键排序在落盘侧统一）。"""
    payload = ledger.to_dict()
    payload["reconciled"] = True
    return payload
