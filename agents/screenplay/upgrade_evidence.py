"""升级判据材料（薄适配：合同 screenplay-upgrade-evidence.md C15，FR-010）。

机制在通用件 `core/degraded/evidence.py`（业务无关）：阈值全量校验、判据项逐项可评价性
（实测值 / `无法评价（来源缺失）` + 缺失原因，无空白无省略）、系统字段不可改写、只增不改。
本模块注入剧本线的业务口径：判据项清单（judge 信度 / 样本量 / 漂移 / 门禁违规率 / 人评
锚点数）、阈值来源（`ScreenplayConfig.upgrade_criteria` + 010 校准的 `reliability_target`）
与判定措辞。

产出 `calibration/upgrade-events/screenplay/{period}.json`：**按 agent 分目录**——009 原路径
不含 agent id，两个降级 Agent 同 ISO 周写入同一路径会互相覆盖（审计证据失效）；本特性一并
迁移，**不读旧路径**（避免同周期出现两份材料的二义性；旧文件原地保留，不可变快照不可回写）。

判定口径（澄清 Q1：阈值配置化 + 系统自动判定；四条齐达才达标）：
1. judge 信度相关系数 ≥ `judge_r_target`（相关系数/样本量来源 010 周校准台账）；
2. judge 配对样本量 ≥ `min_samples`；
3. 漂移指标在 `drift_band` 内（**未测量不得达标**——判据不完整如实标注，原则六）；
4. 门禁违规率 ≤ `gate_violation_max`。

阈值缺失即报错（不允许静默"无判据"）。**系统结论是唯一自动写入的部分**：人可推翻但
必须留痕（人/时间/理由），推翻**不改变系统结论字段**（`system_digest` 内容哈希机检，
手工篡改系统字段即拒绝）。不达标必须如实标注（不得暗示可升级）——升级为自动进化不在
本特性范围，须另立决议并修订宪章原则六相关表述。
"""

from pathlib import Path

from core.degraded import evidence as _core_evidence
from core.degraded.evidence import (
    UpgradeEvidence,
    UpgradeEvidenceError,
)

# 导出面（009 公共 API 逐字不变；机制全在 `core/degraded/evidence.py`）
__all__ = [
    "AGENT_ID",
    "BLIND_REVIEW_OBSERVATION_MATCH",
    "CONCLUSIONS",
    "DEFAULT_EVIDENCE_DIR",
    "JUDGE_EVALUATOR_ID",
    "UpgradeEvidence",
    "UpgradeEvidenceError",
    "build_upgrade_evidence",
    "gate_violation_rate_of",
    "judge_reliability",
    "load_evidence",
    "override_conclusion",
]

AGENT_ID = "screenplay"
DEFAULT_EVIDENCE_DIR = Path("calibration/upgrade-events")
# 010 盲评对象口径（C16 / 技术方案 §2.2 锚点）：剧本线只盲评**大纲阶段**产出
# （judge 只作用于大纲阶段，人评与之对齐）；010 `build_blind_list` 以通用观测槽
# 精确匹配机制消费本口径（不特化任何 Agent）。
BLIND_REVIEW_OBSERVATION_MATCH = {"stage": "outline"}
CONCLUSIONS = ("meets", "below")
JUDGE_EVALUATOR_ID = "judge.dramatic_tension"
_REQUIRED_THRESHOLDS = ("judge_r_target", "min_samples", "drift_band", "gate_violation_max")


def _require_thresholds(cfg) -> dict:
    """阈值读取：缺任一项即报错（不允许静默"无判据"）。"""
    criteria = getattr(cfg, "upgrade_criteria", None)
    if not isinstance(criteria, dict):
        raise UpgradeEvidenceError(
            "升级判据阈值缺失：形态配置无 upgrade_criteria（不允许静默无判据）"
        )
    snapshot = {}
    for key in _REQUIRED_THRESHOLDS:
        if criteria.get(key) is None:
            raise UpgradeEvidenceError(f"升级判据阈值缺失：upgrade_criteria.{key}")
        snapshot[key] = criteria[key]
    return snapshot


def _require_reliability_target(calibration) -> float:
    """010 信度目标（对账口径）：缺即报错（判据快照必须可与周校准信度口径对账）。"""
    target = getattr(calibration, "reliability_target", None)
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        raise UpgradeEvidenceError(
            "缺少 010 信度目标（calibration.reliability_target），拒绝产材料"
        )
    return float(target)


def judge_reliability(ledger: dict | None) -> dict:
    """judge 信度（相关系数与样本量）从 010 台账记录读取；缺记录如实返回样本 0。"""
    if not isinstance(ledger, dict):
        return {
            "correlation": None,
            "samples": 0,
            "metric": None,
            "note": "010 台账无本周期 judge 记录",
        }
    samples = int(ledger.get("samples", 0) or 0)
    if ledger.get("kendall_tau") is not None:
        metric, correlation = "kendall_tau", float(ledger["kendall_tau"])
    elif ledger.get("pearson_r") is not None:
        metric, correlation = "pearson_r", float(ledger["pearson_r"])
    else:
        metric, correlation = None, None
    return {
        "correlation": correlation,
        "samples": samples,
        "metric": metric,
        "note": str(ledger.get("note", "") or ""),
    }


def gate_violation_rate_of(nodes) -> float:
    """门禁违规率：含 `rule.*` 判 0 分量的**已评估**节点占比（可复现口径）。

    分母 = 已评估节点（FAILED/未评估节点不计——其得分缺失无法判定门禁）；
    分子 = 至少一个门禁分量判 0 的节点。无节点 → 0.0（无违规证据）。
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


def _items(
    *, reliability: dict, drift, gate_violation_rate, human_anchor_count
) -> tuple[dict, ...]:
    """判据项（键 + 阈值键 + 提供者）：取值来源在本模块内闭合，机制在通用件。

    每项必有取值形态：取不到数即 `无法评价（来源缺失）` + 原因（不留空、不省略）。
    """
    return (
        {
            "key": "judge_correlation",
            "threshold_key": "judge_r_target",
            "provider": lambda: (
                _core_evidence.measured(
                    reliability["correlation"],
                    judge_metric=reliability["metric"],
                    judge_metric_note=reliability["note"],
                )
                if reliability["correlation"] is not None
                else _core_evidence.unavailable(
                    reliability["note"] or "010 台账无本周期 judge 记录",
                    judge_metric=reliability["metric"],
                    judge_metric_note=reliability["note"],
                )
            ),
        },
        {
            "key": "judge_samples",
            "threshold_key": "min_samples",
            "provider": lambda: _core_evidence.measured(reliability["samples"]),
        },
        {
            "key": "drift",
            "threshold_key": "drift_band",
            "provider": lambda: (
                _core_evidence.measured(float(drift))
                if drift is not None
                else _core_evidence.unavailable("未测量（调用方未提供漂移指标）")
            ),
        },
        {
            "key": "gate_violation_rate",
            "threshold_key": "gate_violation_max",
            "provider": lambda: _core_evidence.measured(
                0.0 if gate_violation_rate is None else float(gate_violation_rate)
            ),
        },
        {
            "key": "human_anchor_count",
            "threshold_key": None,
            "provider": lambda: _core_evidence.measured(int(human_anchor_count)),
        },
    )


def _evaluate(snapshot: dict, raw: dict, items=()) -> tuple[str, list[str], list[str]]:
    """系统自动判定：四条齐达才 meets；不达标逐条给出原因（并产告警）。

    `items` 由通用件传入（逐项取值形态的载体）；判定只读 `raw` 的数值口径。
    """
    reasons: list[str] = []
    alerts: list[str] = []
    if raw["judge_samples"] < snapshot["min_samples"]:
        reasons.append(
            f"样本不足：judge 配对样本 {raw['judge_samples']} < "
            f"min_samples={snapshot['min_samples']}"
        )
    if raw["judge_correlation"] is None:
        reasons.append(f"judge 信度缺失：{raw['judge_metric_note'] or '010 台账无本周期记录'}")
    else:
        if raw["judge_correlation"] < 0:
            alerts.append(f"judge 信度负相关（{raw['judge_metric']}={raw['judge_correlation']}）")
            reasons.append(
                f"judge 信度为负相关（{raw['judge_metric']}={raw['judge_correlation']}）："
                "信号质量不达标（不得据此升级）"
            )
        elif raw["judge_correlation"] < snapshot["judge_r_target"]:
            reasons.append(
                f"judge 信度 {raw['judge_correlation']} < judge_r_target="
                f"{snapshot['judge_r_target']}"
            )
    if raw["drift"] is None:
        reasons.append("漂移指标缺失（未测量）：判据不完整不得判定达标（F7 不在本特性）")
    elif abs(raw["drift"]) > snapshot["drift_band"]:
        reasons.append(f"漂移指标越带：|{raw['drift']}| > drift_band={snapshot['drift_band']}")
    if raw["gate_violation_rate"] > snapshot["gate_violation_max"]:
        reasons.append(
            f"门禁违规率 {raw['gate_violation_rate']} > gate_violation_max="
            f"{snapshot['gate_violation_max']}"
        )
    conclusion = "meets" if not reasons else "below"
    if conclusion == "below":
        reasons.append("结论 below：信号尚未达标，不得据此升级为自动进化（须另立决议）")
    return conclusion, reasons, alerts


def _system_digest(payload: dict) -> str:
    """系统写入部分的内容哈希（推翻不改写；手工篡改即机检失败）。"""
    return _core_evidence.system_digest(payload)


def build_upgrade_evidence(
    period: str,
    cfg,
    ledger: dict | None,
    calibration,
    *,
    drift: float | None = None,
    gate_violation_rate: float | None = None,
    human_anchor_count: int = 0,
    data_dir: str | Path = DEFAULT_EVIDENCE_DIR,
    agent_id: str = AGENT_ID,
) -> UpgradeEvidence:
    """生成周期判据材料（不可变快照：同周期已存在即拒绝，落 `{data_dir}/{agent_id}/`）。

    cfg：剧本形态配置（`upgrade_criteria` 判据阈值，缺即报错）；
    ledger：010 周校准台账的 judge 分量记录（相关系数与样本量来源）；
    calibration：010 校准配置（提供 `reliability_target` 对账口径）；
    drift / gate_violation_rate / human_anchor_count：漂移指标、门禁违规率、人评锚点数
    （由调用方从树与锚点表计算——本模块只做阈值快照、自动判定与留痕）。
    """
    if not isinstance(period, str) or not period:
        raise UpgradeEvidenceError("period 必须为非空字符串（如 2026-W38）")
    snapshot = _require_thresholds(cfg)
    snapshot["reliability_target"] = _require_reliability_target(calibration)
    reliability = judge_reliability(ledger)
    return _core_evidence.build_upgrade_evidence(
        period,
        cfg,
        _items(
            reliability=reliability,
            drift=drift,
            gate_violation_rate=gate_violation_rate,
            human_anchor_count=human_anchor_count,
        ),
        judge=_evaluate,
        snapshot=snapshot,
        conclusions=CONCLUSIONS,
        human_anchor_count=int(human_anchor_count),
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
    """人推翻系统结论：追加留痕（人/时间/理由）——**系统结论字段逐字节不变**。"""
    return _core_evidence.override_conclusion(
        period, by=by, reason=reason, agent_id=AGENT_ID, data_dir=data_dir
    )
