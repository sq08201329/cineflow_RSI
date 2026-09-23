"""升级判据材料（薄适配：契约 C16~C18，FR-010）。

机制在通用件 `core/degraded/evidence.py`（业务无关）：阈值全量校验、判据项逐项可评价性
（实测值 / `无法评价（来源缺失）` + 缺失原因，无空白无省略）、系统字段不可改写、只增不改。
本模块注入开发线的业务口径：判据项清单、阈值来源（`DevConfig.upgrade_criteria`）、
判定措辞与两类来源缺失登记。

产出 `calibration/upgrade-events/dev/{period}.json`（按 agent 分目录，009 同周期材料互不覆盖）。

**开发 Agent 无 judge 层、无人类锚点**（澄清第 1 条：技术方案只为本环节规定"代理信号"一层，
且 010 明确排除本 Agent——`specs/010-weekly-calibration/spec.md:157`）：故判据材料**必然**
含两类来源缺失，逐项登记为"无法评价（来源缺失）+ 原因"，**不得留空、不得省略、不得据未测量
项达标**。本特性不补齐证据通道（不引入伪 judge、不动 010），只如实登记缺口。

判据项（阈值键全覆盖，缺任一项即报错）：

| 项 | 阈值 | 取值形态 |
| --- | --- | --- |
| `reliability`（信度相关系数） | `correlation_target` | 无法评价（010 排除 ⇒ 无信度数据） |
| `drift`（漂移指标） | `drift_band` | 无法评价（无 judge ⇒ 无 012 漂移材料） |
| `evaluated_nodes`（本周期已评估样本量） | `min_samples` | 实测值 |
| `gate_violation_rate`（门禁违规率） | `gate_violation_max` | 实测值 |
| `proxy_distribution`（代理分量分布） | — | 实测值（含**模拟源标注**，SC-009） |
| `missing_sources`（缺失来源登记） | — | 实测值（登记条目数 + 来源清单） |

**系统结论恒不为"达标"**：取值域 `below`（可评价项未过阈）/ `insufficient`（存在无法评价项）；
继续观察条件 = 待补齐阈值项的量化清单（宪章原则六：维持降级必须记录未达标判据与继续观察
条件）。人工推翻必须留痕（人/时间/理由）且**不改写**系统结论字段（`system_digest` 机检）。
"""

from collections.abc import Sequence
from pathlib import Path

from agents.dev.artifact import SIMULATED_NOTE, simulated_signal_sources
from agents.dev.config import THRESHOLD_KEYS
from core.degraded import evidence as _core_evidence
from core.degraded.evidence import (
    MISSING_SOURCE,
    UpgradeEvidence,
    UpgradeEvidenceError,
)

# 导出面（与 009 侧同构；机制全在 `core/degraded/evidence.py`）
__all__ = [
    "AGENT_ID",
    "CONCLUSIONS",
    "DEFAULT_EVIDENCE_DIR",
    "MISSING_SOURCES",
    "THRESHOLD_KEYS",
    "UpgradeEvidence",
    "UpgradeEvidenceError",
    "build_upgrade_evidence",
    "continuation_conditions",
    "gate_violation_rate_of",
    "load_evidence",
    "override_conclusion",
    "proxy_stats_of",
]

AGENT_ID = "dev"
DEFAULT_EVIDENCE_DIR = Path("calibration/upgrade-events")
# 结论取值域**不含"达标/meets"**（C17）：可评价但未过阈 ⇒ below；存在无法评价项 ⇒ insufficient
CONCLUSIONS = ("below", "insufficient")

# 两类来源缺失（C18）：逐项登记（键 + 阈值键 + 缺失来源 + 原因），不留空
MISSING_SOURCES = (
    {
        "key": "reliability",
        "threshold_key": "correlation_target",
        "source": "010 周校准信度数据",
        "reason": (
            "010 按设计排除开发 Agent（specs/010-weekly-calibration/spec.md:157）"
            "⇒ 无信度数据：本环节无 judge 配对比较对象，信度相关系数无从计算"
        ),
    },
    {
        "key": "drift",
        "threshold_key": "drift_band",
        "source": "012 judge 漂移材料",
        "reason": (
            "无 judge 层（本环节评估器组合 = 二门禁 + 二代理）⇒ 无 012 漂移材料："
            "012 漂移检测范围默认仅 judge 类，本环节无 judge 分量可测"
        ),
    },
)
# 人类锚点层同样缺席（澄清第 1 条：不引入 judge、不引入锚点）——按"零锚点 + 原因"如实落盘，
# 不作为第三类"阈值项来源缺失"登记（锚点不是本环节的阈值项）
ANCHOR_LAYER_NOTE = "本环节无人类锚点层（澄清第 1 条）：human_anchor_count 恒 0，非未测量值"


def _require_thresholds(cfg) -> dict:
    """阈值读取：缺任一项即报错（不允许静默"无判据"）。"""
    criteria = getattr(cfg, "upgrade_criteria", None)
    if not isinstance(criteria, dict):
        raise UpgradeEvidenceError(
            "升级判据阈值缺失：形态配置无 upgrade_criteria（不允许静默无判据）"
        )
    snapshot = {}
    for key in THRESHOLD_KEYS:
        if criteria.get(key) is None:
            raise UpgradeEvidenceError(f"升级判据阈值缺失：upgrade_criteria.{key}")
        snapshot[key] = criteria[key]
    return snapshot


def gate_violation_rate_of(nodes) -> float:
    """门禁违规率：含 `rule.*` 判 0 分量的**已评估**节点占比（可复现口径）。

    分母 = 已评估节点（FAILED/未评估节点不计——其得分缺失无法判定门禁）；
    分子 = 至少一个门禁分量判 0 的节点。无节点 → 0.0（无违规证据）。口径与 009 侧同款
    （按包复制，不可跨包导入——同一份降级纪律的 Agent 侧口径）。
    """
    evaluated = [node for node in nodes if node.score is not None]
    if not evaluated:
        return 0.0
    violated = 0
    for node in evaluated:
        for key, fragment in (node.eval_breakdown or {}).items():
            if key.rsplit("@", 1)[0].startswith("rule.") and fragment.get("score") == 0.0:
                violated += 1
                break
    return violated / len(evaluated)


def proxy_stats_of(nodes) -> dict:
    """代理分量分布（本周期可复现口径）：均值/极值/样本量 + 逐分量均值。

    只统计**已评估且带代理分量**的节点（门禁判 0 短路的节点无代理分量——其代理未跑，
    不得按 0 分入样本拖底）；无样本 → 全 0 + 空分量表（如实不伪造）。
    """
    per_node: list[float] = []
    grouped: dict[str, list[float]] = {}
    for node in nodes:
        scores = []
        for key, fragment in (node.eval_breakdown or {}).items():
            base = key.rsplit("@", 1)[0]
            if not base.startswith("proxy."):
                continue
            value = float(fragment["score"])
            scores.append(value)
            grouped.setdefault(base, []).append(value)
        if scores and node.score is not None:
            per_node.append(sum(scores) / len(scores))
    if not per_node:
        return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0, "components": {}}
    return {
        "count": len(per_node),
        "mean": sum(per_node) / len(per_node),
        "min": min(per_node),
        "max": max(per_node),
        "components": {
            component: sum(values) / len(values) for component, values in sorted(grouped.items())
        },
    }


def continuation_conditions(threshold_snapshot: dict) -> list[dict]:
    """继续观察条件 = **待补齐阈值项的量化清单**（键 + 阈值键 + 当时阈值）。

    清单随阈值快照取值（材料冻结后配置变更不影响已产材料）；补齐证据通道须另立决议，
    本模块只如实登记缺口（宪章原则六）。
    """
    return [
        {
            "key": entry["key"],
            "threshold_key": entry["threshold_key"],
            "threshold": threshold_snapshot.get(entry["threshold_key"]),
            "source": entry["source"],
        }
        for entry in MISSING_SOURCES
    ]


def _items(*, stats: dict, gate_violation_rate: float, evaluated_nodes: int, signals: dict):
    """判据项（键 + 阈值键 + 提供者）：取值来源在本模块内闭合，机制在通用件。

    每项必有取值形态：取不到数即 `无法评价（来源缺失）` + 原因（不留空、不省略）。
    代理分量分布随 `signal_sources` 一并落盘——模拟源标注的第三处落点（SC-009）。
    """
    sources = list(simulated_signal_sources(signals))
    return (
        {
            "key": "reliability",
            "threshold_key": "correlation_target",
            "provider": lambda: _core_evidence.unavailable(
                MISSING_SOURCES[0]["reason"],
                reliability_missing_source=MISSING_SOURCES[0]["source"],
                reliability_metric=None,
            ),
        },
        {
            "key": "drift",
            "threshold_key": "drift_band",
            "provider": lambda: _core_evidence.unavailable(
                MISSING_SOURCES[1]["reason"],
                drift_missing_source=MISSING_SOURCES[1]["source"],
            ),
        },
        {
            "key": "evaluated_nodes",
            "threshold_key": "min_samples",
            "provider": lambda: _core_evidence.measured(int(evaluated_nodes)),
        },
        {
            "key": "gate_violation_rate",
            "threshold_key": "gate_violation_max",
            "provider": lambda: _core_evidence.measured(float(gate_violation_rate)),
        },
        {
            "key": "proxy_distribution",
            "threshold_key": None,
            "provider": lambda: _core_evidence.measured(
                float(stats["mean"]),
                proxy_components=stats["components"],
                proxy_count=stats["count"],
                proxy_min=stats["min"],
                proxy_max=stats["max"],
                signal_sources=sources,
                signal_note=SIMULATED_NOTE,
            ),
        },
        {
            "key": "missing_sources",
            "threshold_key": None,
            "provider": lambda: _core_evidence.measured(
                len(MISSING_SOURCES),
                missing_source_registry=[
                    {
                        "key": entry["key"],
                        "threshold_key": entry["threshold_key"],
                        "source": entry["source"],
                        "reason": entry["reason"],
                    }
                    for entry in MISSING_SOURCES
                ],
                human_anchor_layer=ANCHOR_LAYER_NOTE,
            ),
        },
    )


def _evaluate(snapshot: dict, raw: dict, items: Sequence = ()) -> tuple[str, list[str], list[str]]:
    """系统自动判定：**结论恒不为"达标"**（取值域见 `CONCLUSIONS`）。

    可评价项逐条判阈（未过阈即 `below`）；存在无法评价项 ⇒ `insufficient`（证据不足，
    判据不完整不得判定达标，原则六）；继续观察条件 = 待补齐阈值项的量化清单。
    """
    reasons: list[str] = []
    alerts: list[str] = []
    missing = [item for item in items if getattr(item, "status", None) == MISSING_SOURCE]

    if raw["evaluated_nodes"] < snapshot["min_samples"]:
        reasons.append(
            f"样本不足：本周期已评估节点 {raw['evaluated_nodes']} < "
            f"min_samples={snapshot['min_samples']}"
        )
    if raw["gate_violation_rate"] > snapshot["gate_violation_max"]:
        reasons.append(
            f"门禁违规率 {raw['gate_violation_rate']} > "
            f"gate_violation_max={snapshot['gate_violation_max']}"
        )
    for item in missing:
        reasons.append(f"{item.key} 无法评价（来源缺失）：{item.missing_reason}")

    if any("无法评价" not in reason for reason in reasons):
        conclusion = "below"
        reasons.append(
            "结论 below（不达标）：可评价判据未过阈，不得据此升级为自动进化（须另立决议）"
        )
    else:
        conclusion = "insufficient"
        reasons.append(
            "结论 insufficient（证据不足）：本环节恒含来源缺失判据（无 judge 层 / 010 排除），"
            "不得判定达标（原则六：维持降级必须记录未达标判据与继续观察条件）"
        )
    for entry in continuation_conditions(snapshot):
        alerts.append(
            f"继续观察条件：{entry['key']} 待补齐（阈值 {entry['threshold_key']}="
            f"{entry['threshold']}，来源 {entry['source']}）——补齐证据通道须另立决议"
        )
    return conclusion, reasons, alerts


def build_upgrade_evidence(
    period: str,
    cfg,
    *,
    nodes: Sequence = (),
    data_dir: str | Path = DEFAULT_EVIDENCE_DIR,
    agent_id: str = AGENT_ID,
) -> UpgradeEvidence:
    """生成周期判据材料（不可变快照：同周期已存在即拒绝，落 `{data_dir}/{agent_id}/`）。

    cfg：开发形态配置（`upgrade_criteria` 判据阈值 + `signals` 模拟数据源参数，缺即报错）；
    nodes：本周期已评估节点（门禁违规率、代理分量分布与样本量的取数来源——由调用方按窗口
    从发现树读出；本模块只做阈值快照、逐项取值、判定与留痕）。
    """
    if not isinstance(period, str) or not period:
        raise UpgradeEvidenceError("period 必须为非空字符串（如 2026-W38）")
    snapshot = _require_thresholds(cfg)
    signals = getattr(cfg, "signals", None)
    if not isinstance(signals, dict) or not signals:
        raise UpgradeEvidenceError("形态配置缺少模拟数据源参数（dev.signals）：不得无源产材料")
    stats = proxy_stats_of(nodes)
    return _core_evidence.build_upgrade_evidence(
        period,
        cfg,
        _items(
            stats=stats,
            gate_violation_rate=gate_violation_rate_of(nodes),
            evaluated_nodes=sum(1 for node in nodes if node.score is not None),
            signals=signals,
        ),
        judge=_evaluate,
        snapshot=snapshot,
        conclusions=CONCLUSIONS,
        human_anchor_count=0,  # 本环节无人类锚点层（ANCHOR_LAYER_NOTE 随材料明示）
        agent_id=agent_id,
        data_dir=data_dir,
    )


def load_evidence(period: str, *, data_dir: str | Path = DEFAULT_EVIDENCE_DIR) -> dict:
    """读取周期判据材料（不存在即报错，不静默返回空）。"""
    return _core_evidence.load_evidence(period, agent_id=AGENT_ID, data_dir=data_dir)


def override_conclusion(
    period: str,
    *,
    by: str,
    reason: str,
    data_dir: str | Path = DEFAULT_EVIDENCE_DIR,
) -> UpgradeEvidence:
    """人推翻系统结论：追加留痕（人/时间/理由）——**系统字段逐字节不变**。"""
    return _core_evidence.override_conclusion(
        period, by=by, reason=reason, agent_id=AGENT_ID, data_dir=data_dir
    )
