"""每周信度报告（功能 010 US2，契约 C6）。

build_report 从台账（ledger/{agent_id}/{evaluator_id}.jsonl）取本周期记录，
产出 reports/{period}.json：per agent per evaluator 的相关系数（pearson_r 或
kendall_tau）+ samples + meets_target（≥ calibration.reliability_target，默认 0.6）；
负相关记录入 alerts（只告警不自动反向调权，原则六）。
"""

import json
from pathlib import Path

from core.calibration.ledger import ledger_path


def _correlation_of(record: dict) -> tuple[str, float | None]:
    """取记录的相关口径：连续 → pearson_r；judge → kendall_tau。"""
    if record.get("kendall_tau") is not None:
        return "kendall_tau", record["kendall_tau"]
    return "pearson_r", record.get("pearson_r")


def build_report(data_dir: str | Path, period: str, *, target: float) -> dict:
    """生成周期信度报告并落盘；返回报告 dict（schema：period/agents/target/alerts）。"""
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
                # 本周期最新一条（同周期可能追加多次，取末行）
                current = [r for r in records if r.get("period") == period]
                if not current:
                    continue
                record = current[-1]
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

    report = {"period": period, "agents": agents, "target": target, "alerts": alerts}
    path = data_dir / "reports" / f"{period}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
