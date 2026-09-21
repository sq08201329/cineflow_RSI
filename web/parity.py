"""同源比对辅助（测试用）：接口响应 vs 既有 JSON 报告逐字段一致（FR-010 分层口径）。

分层（2026-09-21 澄清决议）：

- **有报告产物的面板逐字段一致**（机检）——进化曲线 ← dreaming/history（以 005 的
  `build_curve` 为权威读数）、信度 ← 010 报告、漂移 ← 012 报表、谱系 ← 005 谱系报表
  （谱系面板的比对正是对冲 web/ 侧重写汇聚逻辑的口径漂移）；
- **树/节点接口以 DB 为权威源**：字段集与类型与 001 落库 schema 一致
  （`tests/contract/test_web_parity.py` 逐列对齐断言）。

比对纯函数、零 import core/agents/dreaming、不读写任何东西；服务与导出不依赖本模块。
差异以可读字符串列表返回（空列表 = 同源成立），调用方据此断言。
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

EVOLUTION = "evolution"
CALIBRATION = "calibration"
DRIFT = "drift"
LINEAGE = "lineage"
PANELS = (EVOLUTION, CALIBRATION, DRIFT, LINEAGE)

_COLLAPSE_FIELDS = ("collapsed", "start_round", "threshold", "window")
_DRIFT_ALERT_FIELDS = ("evaluator_key", "agent_id", "status", "level", "double_signal", "note")


def panel_report_path(
    panel: str, config, *, agent_id: str | None = None, period: str | None = None
) -> Path:
    """面板 → 既有报告文件（或轮次目录）路径映射（同源比对的输入定位）。

    - evolution：`{dreaming}/{agent_id}/`（轮次报告逐文件）；
    - calibration：`{calibration}/reports/{period}.json`（010）；
    - drift：`{calibration}/drift/reports/{period}.json`（012）；
    - lineage：`{policies}/`（005 谱系 meta 目录）。
    """
    if panel == EVOLUTION:
        if not agent_id:
            raise ValueError("evolution 面板需要 agent_id（轮次报告按 Agent 分目录）")
        return Path(config.data_dir("dreaming")) / agent_id
    if panel == CALIBRATION:
        if not period:
            raise ValueError("calibration 面板需要 period（010 报告按周期落盘）")
        return Path(config.data_dir("calibration")) / "reports" / f"{period}.json"
    if panel == DRIFT:
        if not period:
            raise ValueError("drift 面板需要 period（012 报表按周期落盘）")
        return Path(config.data_dir("calibration")) / "drift" / "reports" / f"{period}.json"
    if panel == LINEAGE:
        return Path(config.data_dir("policies"))
    raise ValueError(f"未知面板：{panel!r}（可选：{list(PANELS)}）")


def _report_diff(panel: str, diffs: list[str]):
    """面板级比对器：`check(label, 接口值, 报告值)` → 差异进 diffs。"""

    def check(label: str, actual, expected) -> None:
        if actual != expected:
            diffs.append(f"{panel}: {label} 不一致——接口 {actual!r} vs 报告 {expected!r}")

    return check


def compare_evolution(response: Mapping, report: Mapping) -> list[str]:
    """进化曲线面板 vs 005 曲线读数（`build_curve().to_dict()` 为权威）。

    比对：agent_id / 逐轮 (round, winner_version, reward↔best_reward, collapse_flag 存在) /
    baseline_reward / collapse 四字段 / plateau_note。成本（cost_usd）不在 005 曲线 schema 内，
    由调用方另按轮次报告的胜出候选轨迹断言。
    """
    diffs: list[str] = []
    check = _report_diff(EVOLUTION, diffs)
    check("agent_id", response.get("agent_id"), report.get("agent_id"))
    rounds = list(response.get("rounds") or [])
    report_rounds = list(report.get("rounds") or [])
    check("轮次数", len(rounds), len(report_rounds))
    for index, (left, right) in enumerate(zip(rounds, report_rounds, strict=False)):
        check(f"rounds[{index}].round", left.get("round"), right.get("round"))
        check(
            f"rounds[{index}].winner_version",
            left.get("winner_version"),
            right.get("winner_version"),
        )
        check(f"rounds[{index}].best_reward", left.get("best_reward"), right.get("reward"))
        if "collapse_flag" not in left:
            diffs.append(f"{EVOLUTION}: rounds[{index}] 缺 collapse_flag")
    check("baseline_reward", response.get("baseline_reward"), report.get("baseline_reward"))
    collapse = response.get("collapse") or {}
    report_collapse = report.get("collapse") or {}
    for field in _COLLAPSE_FIELDS:
        check(f"collapse.{field}", collapse.get(field), report_collapse.get(field))
    check("plateau_note", response.get("plateau_note"), report.get("plateau_note"))
    return diffs


def compare_calibration(response: Mapping, report: Mapping) -> list[str]:
    """信度面板 vs 010 报告：周期/目标/告警 + 逐 Agent 逐评估器（口径值/samples/达标）。"""
    diffs: list[str] = []
    check = _report_diff(CALIBRATION, diffs)
    check("period", response.get("period"), report.get("period"))
    check("target", response.get("target"), report.get("target"))
    check("alerts", response.get("alerts"), report.get("alerts"))
    report_agents = report.get("agents") or {}
    response_agents = {
        entry["agent_id"]: entry["evaluators"] for entry in response.get("agents") or []
    }
    check("agents 集合", sorted(response_agents), sorted(report_agents))
    for agent_id, evaluators in sorted(response_agents.items()):
        source = report_agents.get(agent_id) or {}
        flattened = {entry["evaluator_key"]: entry for entry in evaluators}
        check(f"agents[{agent_id}] 评估器集合", sorted(flattened), sorted(source))
        for evaluator_key, entry in sorted(flattened.items()):
            origin = source.get(evaluator_key) or {}
            expected_metric = (
                "kendall_tau" if origin.get("kendall_tau") is not None else "pearson_r"
            )
            prefix = f"agents[{agent_id}][{evaluator_key}]"
            check(f"{prefix} 口径", entry.get("metric"), expected_metric)
            check(f"{prefix}.value", entry.get("value"), origin.get(expected_metric))
            check(f"{prefix}.samples", entry.get("samples"), origin.get("samples"))
            check(f"{prefix}.meets_target", entry.get("meets_target"), origin.get("meets_target"))
    return diffs


def compare_drift(response: Mapping, report: Mapping) -> list[str]:
    """漂移面板 vs 012 报表：周期 + 逐项状态（evaluator_key/status/since）+ 告警投影。"""
    diffs: list[str] = []
    check = _report_diff(DRIFT, diffs)
    check("period", response.get("period"), report.get("period"))
    expected_items = [
        {
            "evaluator_key": item.get("evaluator_key"),
            "status": (item.get("status") or {}).get("status"),
            "since": (item.get("status") or {}).get("since"),
        }
        for item in report.get("items") or []
    ]
    check("items", list(response.get("items") or []), expected_items)
    expected_alerts = [
        {field: alert.get(field) for field in _DRIFT_ALERT_FIELDS}
        for alert in report.get("alerts") or []
    ]
    check("alerts", list(response.get("alerts") or []), expected_alerts)
    return diffs


def _parent_chain(version: str, versions: Mapping[str, Mapping]) -> list[str]:
    """报告口径的父链（直接父 → 更早），逐级取 parent_version（环即截断，不递归爆栈）。"""
    chain: list[str] = []
    seen = {version}
    current = (versions.get(version) or {}).get("parent_version")
    while current:
        if current in seen:
            break
        seen.add(current)
        chain.append(current)
        current = (versions.get(current) or {}).get("parent_version")
    return chain


def _dedup(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def compare_lineage(
    response: Mapping, report: Mapping, *, agent_id: str | None = None
) -> list[str]:
    """谱系面板 vs 005 谱系报表（按 Agent 作用域比对）。

    比对项：版本是否被报告汇聚、产出树集合（报告 `tree_ids`）、子版本集合
    （报告 `child_versions`）、父链世代顺序（报告 `parent_version` 逐级回指）。
    报告不含跨项目归属（`project_id`），故项目归属由契约测试的 DB 断言核对——
    本函数只比对 005 报告能给出的字段（口径不重叠处不冒充一致）。
    """
    diffs: list[str] = []
    check = _report_diff(LINEAGE, diffs)
    versions = {row["version"]: row for row in report.get("versions") or []}
    version = response.get("policy_version")
    scope = agent_id if agent_id is not None else report.get("agent_id")
    if version not in versions:
        diffs.append(f"{LINEAGE}: 报告缺版本 {version!r}（接口呈现了报告未汇聚的版本）")
        return diffs
    entry = versions[version]
    trees = [row for row in response.get("trees") or [] if row.get("agent_id") == scope]
    check(
        "trees",
        sorted(row["tree_id"] for row in trees),
        sorted(entry.get("tree_ids") or []),
    )
    children = _dedup(
        row["version"] for row in response.get("children") or [] if row.get("agent_id") == scope
    )
    check("children", children, sorted(entry.get("child_versions") or []))
    parents = _dedup(
        row["version"] for row in response.get("parents") or [] if row.get("agent_id") == scope
    )
    check("parents", parents, _parent_chain(version, versions))
    if "cross_project" not in response:
        diffs.append(f"{LINEAGE}: 接口响应缺 cross_project（跨项目归属标注）")
    return diffs
