"""报告侧证据面（功能 018 US3 / 契约 C13）：成本**第三方腿**（网关记账）采样 + 性能画像。

**只读采样，不新增构造点**（E-03）：第三方腿在**环节边界**只读 `LLMGateway.total_cost_usd`
（网关账本，对账三方之一），**不构造 `LLMGateway`**（构造点普查仍 14 处）、不改网关契约、
不改 `cost_breakdown`、不进树——采样装饰只有**一处**（`agents/pilot/stages.py` 的
`build_stage_specs`），增量随 `StageOutcome.detail` 落入运行记录（`detail` 是执行器**不解释**的
透传面，故树模型与网关契约都不动）。

**三方口径的可比性分级**（C13，`STAGE_COST_LEG` 声明）：`dev`/`script` 是 LLM 腿**专属**环节
（`PLATFORM_SLOTS` 不含它们）⇒ 阶段成本与网关增量**逐项相等**；`storyboard`/`visual`/`editing`/
`promo` 是**混合腿**（平台适配器 + judge 层 LLM 调用）⇒ 只断言"阶段成本 ≥ 该环节网关增量"
（平台腿花费无 019 账单面，不进厂商账单对账）；`sound` 无 LLM 调用 ⇒ 平台腿（网关增量恒 0）。
账本腿（`billing/{channel}/ledger.json`）仅在 `window.kind == run` **且窗口实例 == 本次
`run_id`** 时逐项比对；`day`/`period` 窗口是**窗口累计**，如实备注"不作逐项比对"
（不静默比对、也不静默跳过）。

**性能画像落报告侧**（`pilot/profiles/{run_id}.json`）：各环节墙钟耗时（`StageState` 的
`started_at`/`finished_at`）+ 体量指标 + 阈值快照 + 结论词 + 时钟口径。**墙钟只在此处**——
画像**不进**样片包五件套（不参与逐字节比对）。结论词 `meets|below|not_evaluable`：
固定时钟运行恒 `not_evaluable`（那组时间戳是常量，只能证明可复现）；各环节时间戳**全等**时
即便声明系统时钟也强制 `not_evaluable`（声明撒谎被证据推翻）；阈值 `unstandardized`（未标定）
同样 `not_evaluable` 且**不发明数字**。**登记边界（如实）**：单次运行无法自证用过系统时钟，
故接受"入口显式声明 + 退化交叉核验"两层判定，残余风险如实登记。
"""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from core.orchestration.models import RunRecord, StageOutcome

# 时钟口径（入口显式声明，无默认；镜像 `ops/pilot.py` 的 `--fixed-clock` 语义）
CLOCK_MODES = ("system", "fixed")
# 结论词取值域（机读：达标 / 未达标 / 不可评价）
VERDICTS = ("meets", "below", "not_evaluable")
# 第三方腿在各环节的**可比性分级**（单一映射声明；键集 == PILOT_STAGE_IDS，装配期断言）
STAGE_COST_LEG = {
    "dev": "llm_only",
    "script": "llm_only",
    "storyboard": "mixed",
    "visual": "mixed",
    "sound": "platform_only",
    "editing": "mixed",
    "promo": "mixed",
}
COST_LEGS = ("llm_only", "mixed", "platform_only")
# 逐环节第三腿的**比较式**（与"分级"分开登记：分级说"哪条腿"，比较式说"怎么比"）。
# 判断依据（实现时实测，如实留痕）：`dev` 无 judge 层（生成即全部 LLM 花费）；
# `script` 的**运营表只记生成**、judge 计费只进树节点（`agents/screenplay/loop.py` 的
# `_reconcile` 桥）⇒ 等式落在"阶段成本 + 该环节评估器计费增量"上（**仍是等式**，未放宽为 ≥）；
# 混合腿（平台适配器 + judge）只做 ≥（平台腿花费无 019 账单面）；`sound` 无 LLM 调用 ⇒ 恒 0。
STAGE_GATEWAY_COMPARISON = {
    "dev": "equal_recorded",
    "script": "equal_recorded_plus_evaluator",
    "storyboard": "at_least_recorded",
    "visual": "at_least_recorded",
    "sound": "zero_recorded",
    "editing": "at_least_recorded",
    "promo": "at_least_recorded",
}
GATEWAY_COMPARISONS = (
    "equal_recorded",
    "equal_recorded_plus_evaluator",
    "at_least_recorded",
    "zero_recorded",
)
# 采样增量的运行记录键（`StageOutcome.detail` 透传面；不进树、不进网关契约）
GATEWAY_DELTA_KEY = "gateway_delta_usd"
# 报告侧目录（`pilot/profiles/{run_id}.json`；**不新增第六件**）
PROFILES_DIRNAME = "profiles"
DEFAULT_TOLERANCE_USD = 1e-9

FIXED_CLOCK_NOTE = "耗时为确定性常量、不构成性能证据（固定时钟运行只能证明可复现）"
DEGENERATE_NOTE = "各环节时间戳全等（时间戳退化）：即使声明 system 时钟也被证据推翻，不产出达标结论"
UNSTANDARDIZED_NOTE = (
    "性能阈值未标定（pilot.performance.status=unstandardized）：只出台账与体量，不产出达标结论"
)
LEDGER_WINDOW_NOTE = "窗口累计：不作逐项比对（不静默比对、也不静默跳过）"
LEDGER_RUN_NOTE = "窗口 kind=run 且实例 == 本次 run_id：逐项可比（账本腿参与三方对账）"


class ReportError(Exception):
    """报告侧证据错误（采样缺项 / 三方对账不一致 / 画像身份冲突）。"""


def read_gateway_total(gateway: Any) -> float:
    """只读网关账本（`LLMGateway.total_cost_usd`）：不构造网关、不改网关契约。"""
    return float(gateway.total_cost_usd)


def sample_entrypoint(stage_id: str, entrypoint, *, gateway: Any):
    """环节边界的**只读**采样包装（唯一装饰点由 `build_stage_specs` 施加）。

    增量 = 该环节执行前后 `total_cost_usd` 之差，随 `StageOutcome.detail` 落运行记录
    （`detail` 是执行器不解释的透传面 ⇒ 树与网关契约零改动）。**失败轮次**不产包、第三方腿
    不产出（增量无承载对象），如实登记在 `stage_gateway_deltas` 的缺项拒绝里。
    """

    def _entry(stage_input):
        before = read_gateway_total(gateway)
        outcome = entrypoint(stage_input)
        delta = read_gateway_total(gateway) - before
        if not isinstance(outcome, StageOutcome):
            raise ReportError(f"阶段 {stage_id} 的入口返回非 StageOutcome：{outcome!r}")
        return replace(outcome, detail={**outcome.detail, GATEWAY_DELTA_KEY: delta})

    _entry.__name__ = f"sampled_{stage_id}"
    return _entry


def stage_gateway_deltas(record: RunRecord) -> dict[str, float]:
    """运行记录里各环节的网关记账增量（缺项即报错，**不静默取 0**）。"""
    deltas: dict[str, float] = {}
    for state in record.stages:
        if GATEWAY_DELTA_KEY not in state.detail:
            raise ReportError(
                f"阶段 {state.stage_id} 缺网关记账增量采样（{GATEWAY_DELTA_KEY}）："
                "第三方腿不可用（不静默取 0；采样包装见 build_stage_specs）"
            )
        deltas[state.stage_id] = float(state.detail[GATEWAY_DELTA_KEY])
    return deltas


def gateway_leg(record: RunRecord, *, tolerance_usd: float = DEFAULT_TOLERANCE_USD) -> dict:
    """成本第三方腿的**确定性段**（`cost.json` 用）：逐环节增量 + 可比性分级 + 逐项核对结论。

    按分级判定（不静默取其一）：LLM 腿专属环节逐项**相等**；混合腿只做 `≥`；平台腿环节的
    网关增量必须为 0。任一不符即报错（账目对不上必须报错，原则二）。
    """
    deltas = stage_gateway_deltas(record)
    unknown = sorted(set(deltas) - set(STAGE_COST_LEG))
    if unknown:
        raise ReportError(
            f"第三方腿出现未声明环节 {unknown}：可比性分级声明需同步（STAGE_COST_LEG）"
        )
    rows: dict[str, dict] = {}
    violations: list[str] = []
    for state in record.stages:
        stage_id = state.stage_id
        leg = STAGE_COST_LEG.get(stage_id)
        if leg is None:
            raise ReportError(
                f"环节 {stage_id} 未声明成本腿分级（STAGE_COST_LEG 键集须覆盖七环节）"
            )
        comparison = STAGE_GATEWAY_COMPARISON.get(stage_id)
        if comparison not in GATEWAY_COMPARISONS:
            raise ReportError(
                f"环节 {stage_id} 未声明第三腿比较式（{comparison!r}）：只接受 "
                f"{list(GATEWAY_COMPARISONS)}（缺项即拒绝，不静默放过）"
            )
        recorded = float(state.cost_usd)
        gateway = float(deltas[stage_id])
        reconciliation = dict(state.detail.get("cost_reconciliation") or {})
        if reconciliation and not reconciliation.get("consistent", True):
            violations.append(
                f"{stage_id} 的生成成本对账不自洽（运营表⨯树节点⨯评估器计费）：{reconciliation}"
            )
        evaluator = float(reconciliation.get("evaluator_cost_usd", 0.0))
        rows[stage_id] = {
            "leg": leg,
            "comparison": comparison,
            "recorded_usd": recorded,
            "evaluator_usd": evaluator,
            "tree_total_usd": float(reconciliation.get("tree_total_usd", recorded)),
            "gateway_usd": gateway,
            "delta_usd": recorded + evaluator - gateway,
        }
        if comparison == "equal_recorded" and abs(recorded - gateway) > tolerance_usd:
            violations.append(f"{stage_id}（LLM 腿专属）记录 {recorded} != 网关增量 {gateway}")
        elif (
            comparison == "equal_recorded_plus_evaluator"
            and abs(recorded + evaluator - gateway) > tolerance_usd
        ):
            violations.append(
                f"{stage_id}（LLM 腿专属）记录 {recorded} + 评估器计费 {evaluator} != "
                f"网关增量 {gateway}"
            )
        elif comparison == "at_least_recorded" and recorded + tolerance_usd < gateway:
            violations.append(f"{stage_id}（混合腿）阶段成本 {recorded} < 网关增量 {gateway}")
        elif comparison == "zero_recorded" and gateway > tolerance_usd:
            violations.append(f"{stage_id}（平台腿）网关增量应为 0，实际 {gateway}")
    if violations:
        raise ReportError(
            "成本三方对账不一致（第三方腿 = 网关记账增量，不静默取其一）：" + "；".join(violations)
        )
    return {
        "by_stage": {stage_id: row["gateway_usd"] for stage_id, row in rows.items()},
        "lines": [{"stage_id": stage_id, **row} for stage_id, row in rows.items()],
        "total_usd": sum(row["gateway_usd"] for row in rows.values()),
        "comparability": {stage_id: row["leg"] for stage_id, row in rows.items()},
        "comparisons": {stage_id: row["comparison"] for stage_id, row in rows.items()},
        "reconciled": True,
        "note": (
            "第三方腿 = 环节边界的只读采样（网关账本）：LLM 腿专属环节（dev/script）逐项**相等**"
            "——dev 取阶段成本、script 取阶段成本 + 该环节评估器计费增量（运营表只记生成、"
            "judge 计费只进树节点，见 screenplay loop 的 `_reconcile` 桥；**仍是等式**）；"
            "混合腿（storyboard/visual/editing/promo）只做 ≥（平台腿花费无 019 账单面，"
            "不进厂商账单对账）；平台腿（sound）网关增量恒 0。账本窗口可比性口径随报告侧登记。"
        ),
    }


def ledger_leg(record: RunRecord, runtime: Any) -> dict:
    """账本腿（`billing/{channel}/ledger.json`）：窗口可比时逐项、累计窗口时**如实备注**。

    `window.kind == run` 且窗口实例 == 本次 `run_id` ⇒ 逐项可比（与第三方腿合计核对）；
    `day`/`period` 窗口是窗口累计 ⇒ `reconciled: null` + 备注"不作逐项比对"
    （不静默比对、也不静默跳过）。未接 019 门禁 ⇒ 如实标注"无来源"。
    """
    guard = getattr(runtime.gateway, "spend_guard", None)
    if guard is None:
        return {
            "comparability": "not_available",
            "reconciled": None,
            "by_tier": {},
            "note": "本次运行未接 019 预算门禁（账本腿无来源）",
        }
    payload = guard.ledger.read()
    tiers = payload.get("tiers") or {}
    rows = {
        str(tier_id): {
            "spent_usd": float(record_value.get("spent_usd", 0.0)),
            "reserved_usd": float(record_value.get("reserved_usd", 0.0)),
            "refusals": int(record_value.get("refusals", 0)),
            "window_key": list(record_value.get("window_key") or []),
        }
        for tier_id, record_value in sorted(tiers.items())
    }
    windows = sorted({row["window_key"][0] for row in rows.values() if row["window_key"]})
    comparable = (
        bool(rows)
        and all(
            len(row["window_key"]) == 2 and row["window_key"][0] == "run" for row in rows.values()
        )
        and sorted({row["window_key"][1] for row in rows.values()}) == [record.run_id]
    )
    leg = {
        "channel_id": str(getattr(guard, "channel_id", "")),
        "by_tier": rows,
        "windows": windows,
        "with_refusals": sorted(tier_id for tier_id, row in rows.items() if row["refusals"]),
    }
    if not comparable:
        leg.update(
            {
                "comparability": "window_accumulated",
                "reconciled": None,
                "note": f"{LEDGER_WINDOW_NOTE}（窗口 kind={windows or ['<未声明>']}）",
            }
        )
        return leg
    ledger_total = sum(row["spent_usd"] for row in rows.values())
    gateway_total = sum(stage_gateway_deltas(record).values())
    if abs(ledger_total - gateway_total) > DEFAULT_TOLERANCE_USD:
        raise ReportError(
            f"账本腿与第三方腿不一致（run 窗口）：账本合计 {ledger_total} vs 网关合计 "
            f"{gateway_total}（逐项可比时不得静默放过）"
        )
    leg.update(
        {
            "comparability": "run_window",
            "reconciled": True,
            "total_usd": ledger_total,
            "note": LEDGER_RUN_NOTE,
        }
    )
    return leg


def volume_snapshot(record: RunRecord, runtime: Any) -> dict:
    """体量指标（全部取自既有明细，**不新造测量**；画像与包内 `state.volume` **同源**）。

    镜头数（分镜明细）/ 片段数（视觉明细）/ 成片时长（剪辑 `reel`）/ 页数（剧本工件
    `page_count(lines_per_page)`）。成片时长取**排练档覆盖后的生效值**，并与剧本目标的
    `× 60` 折算一致（SC-012① 已在预检硬校验，此处复核，不一致即报错）。
    """
    from agents.pilot.pilot import DURATION_TOLERANCE_S

    storyboard = record.stage("storyboard").detail
    visual = record.stage("visual").detail
    reel = dict(record.stage("editing").detail.get("reel") or {})
    target_s = float(runtime.configs.editing.target_duration_s)
    minutes = float(
        runtime.pilot.effective_script_target_minutes(
            float(runtime.configs.screenplay.target_duration_min)
        )
    )
    if abs(target_s - minutes * 60.0) > DURATION_TOLERANCE_S:
        raise ReportError(
            f"生效成片时长口径不一致：editing.target_duration_s {target_s} s vs "
            f"剧本目标 {minutes} 分钟 × 60 = {minutes * 60.0} s（拒绝产出体量结论）"
        )
    return {
        "shot_count": int(storyboard.get("shot_count") or 0),
        "clip_count": int(visual.get("clip_count") or 0),
        "reel_duration_s": round(int(reel.get("duration_ms") or 0) / 1000.0, 6),
        "page_count": _page_count(record, runtime),
        "target_duration_s": target_s,
        "script_target_minutes": minutes,
        "note": "体量取自既有明细（分镜/视觉/剪辑/剧本工件），成片时长为排练档覆盖后的生效值",
    }


def _page_count(record: RunRecord, runtime: Any) -> float | None:
    """剧本页数（`ScriptArtifact.page_count(lines_per_page)`）：缺工件如实返回 None。"""
    from agents.screenplay.artifact import ScriptArtifact

    artifact_hash = record.stage("script").detail.get("artifact_hash")
    if not artifact_hash:
        return None
    artifact = ScriptArtifact.from_dict(json.loads(runtime.artifacts.get(artifact_hash)))
    return round(artifact.page_count(int(runtime.configs.screenplay.lines_per_page)), 6)


def build_profile(record: RunRecord, runtime: Any, *, clock_mode: str) -> dict:
    """性能画像（确定性段 + 墙钟只在此处）：各环节耗时 + 体量 + 阈值快照 + 结论 + 时钟口径。

    `clock_mode` **无默认**（入口须显式声明，镜像 `ops/pilot.py` 的 `--fixed-clock` 语义）。
    """
    if clock_mode not in CLOCK_MODES:
        raise ReportError(
            f"时钟口径非法 {clock_mode!r}：只接受 {list(CLOCK_MODES)}"
            "（入口显式声明，无默认——固定时钟运行不得产出达标结论）"
        )
    stages: dict[str, dict] = {}
    for state in record.stages:
        delta = state.detail.get(GATEWAY_DELTA_KEY)
        stages[state.stage_id] = {
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "elapsed_s": _elapsed_seconds(state.started_at, state.finished_at),
            "cost_usd": float(state.cost_usd),
            "gateway_delta_usd": None if delta is None else float(delta),
            "leg": STAGE_COST_LEG.get(state.stage_id),
        }
    thresholds = {
        "status": str(runtime.pilot.performance_status),
        "stage_seconds": {key: float(value) for key, value in runtime.pilot.stage_seconds.items()},
        "source": "pilot.performance",
    }
    verdict, reason = _verdict(clock_mode, stages, thresholds)
    return {
        "run_id": record.run_id,
        "form": record.form,
        "clock_mode": clock_mode,
        "stages": stages,
        "volume": volume_snapshot(record, runtime),
        "thresholds": thresholds,
        "verdict": verdict,
        "verdict_reason": reason,
        "ledger": ledger_leg(record, runtime),
        "note": (
            "报告侧证据：墙钟只在此处（画像不入样片包五件套，不参与逐字节比对）；"
            "单次运行无法自证用过系统时钟，故为「入口声明 + 时间戳退化交叉核验」两层判定"
        ),
    }


def _verdict(
    clock_mode: str, stages: Mapping[str, Mapping], thresholds: Mapping
) -> tuple[str, str]:
    """结论词（`meets|below|not_evaluable`）：三层前置判定 + 逐环节阈值对照。"""
    stamps = sorted(
        {str(row["started_at"]) for row in stages.values()}
        | {str(row["finished_at"]) for row in stages.values()}
    )
    if clock_mode == "fixed":
        return "not_evaluable", FIXED_CLOCK_NOTE
    if len(stamps) == 1:
        return "not_evaluable", DEGENERATE_NOTE
    if thresholds.get("status") != "declared":
        return "not_evaluable", UNSTANDARDIZED_NOTE
    limits = thresholds.get("stage_seconds") or {}
    elapsed = {stage_id: row["elapsed_s"] for stage_id, row in stages.items()}
    if any(value is None for value in elapsed.values()):
        return "not_evaluable", "环节起止时间缺失：无可对照的耗时（不产出达标结论）"
    exceeded = [
        f"{stage_id} {float(elapsed[stage_id]):g}s > {float(limits[stage_id]):g}s"
        for stage_id in sorted(elapsed)
        if stage_id in limits and float(elapsed[stage_id]) > float(limits[stage_id])
    ]
    missing = sorted(stage_id for stage_id in elapsed if stage_id not in limits)
    if missing:
        return "not_evaluable", f"阈值缺环节声明 {missing}（缺项即不可评价，不发明数字）"
    if exceeded:
        return "below", "超阈值环节：" + "；".join(exceeded)
    return "meets", "各环节耗时均在配置声明的阈值内"


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
    except ValueError:  # 时间戳形状非法（非本特性引入）：如实返回 None（不编造数字）
        return None
    return round((finish - start).total_seconds(), 6)


def profile_path(data_dir: str | Path, run_id: str) -> Path:
    return Path(data_dir) / PROFILES_DIRNAME / f"{run_id}.json"


def write_profile(data_dir: str | Path, profile: Mapping[str, Any]) -> Path:
    """画像落盘（`pilot/profiles/{run_id}.json`，报告侧 append-only）。

    身份 = `(run_id, clock_mode)`：同一时钟口径重复生成即覆盖同一键（重算同一证据，幂等）；
    同一 `run_id` 若已有**另一时钟口径**的画像 ⇒ 拒绝（不同口径混写会让"固定时钟恒
    `not_evaluable`"被悄悄覆盖）。画像**不进样片包**、不参与包内逐字节比对。
    """
    run_id = str(profile["run_id"])
    target = profile_path(data_dir, run_id)
    if target.is_file():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("clock_mode") != profile["clock_mode"]:
            raise ReportError(
                f"性能画像已存在且时钟口径不同（{existing.get('clock_mode')!r} != "
                f"{profile['clock_mode']!r}）：报告侧 append-only——换时钟口径请换数据根/run_id"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target


__all__ = [
    "CLOCK_MODES",
    "COST_LEGS",
    "DEFAULT_TOLERANCE_USD",
    "DEGENERATE_NOTE",
    "FIXED_CLOCK_NOTE",
    "GATEWAY_DELTA_KEY",
    "LEDGER_RUN_NOTE",
    "LEDGER_WINDOW_NOTE",
    "PROFILES_DIRNAME",
    "ReportError",
    "STAGE_COST_LEG",
    "UNSTANDARDIZED_NOTE",
    "VERDICTS",
    "build_profile",
    "gateway_leg",
    "ledger_leg",
    "profile_path",
    "read_gateway_total",
    "sample_entrypoint",
    "stage_gateway_deltas",
    "volume_snapshot",
    "write_profile",
]
