"""影子模式与对照报告单测（功能 014 US2 / T1413，先于实现编写；契约 C5 + FR-005/FR-012）。

覆盖：
- `record_shadow_event`：**拦截的候选也要记**（否则差异分类取不到 `human_pass_sys_block`
  ——这正是"人工放行但系统会拦截"的直接信号）；事件留痕只增不改（jsonl 追加）；
- 差异分类四类齐全（`sys_pass_human_reject` / `human_pass_sys_block` / `agree` /
  `no_human_decision`）且由（若 auto 放行与否 × 人工决策）推导；
- `build_shadow_report(period, cfg)` → `deployment/shadow/{period}.json`：放行/拦截数、
  理由分布、差异分类计数、**误入率分子分母与口径说明**、达标标记；同名报告只增不改；
- `recompute_misadmission_rate(...)`：从留痕重算 == 报告值（SC-007 机检）；
  影子期口径：分子 = "会放行但人工拒绝" ∪ "放行样本中被判定不可接受"（按候选去重），
  分母 = 影子放行候选数（影子期无真实部署，不以部署数为分母）；
- 影子期**指针变更次数 0**（影子只记录、不部署，机检）。
"""

import json
from dataclasses import replace

import pytest

from core.deployment import mode, shadow
from core.deployment.config import ShadowConfig
from core.deployment.errors import DeploymentRecordConflictError
from core.deployment.models import DiffCategory, GateDecision, HumanDecision

PERIOD = "2026-W39"
AGENT = "visual"
T0 = "2026-09-21T00:00:00+00:00"


def _record(
    data_dir,
    candidate,
    decision=GateDecision.ELIGIBLE,
    *,
    human=HumanDecision.NONE,
    unacceptable=False,
    period=PERIOD,
    agent_id=AGENT,
):
    return shadow.record_shadow_event(
        agent_id,
        candidate,
        decision,
        data_dir=data_dir,
        period=period,
        reason=(
            f"判定 {decision}" + ("（拦截理由）" if decision is not GateDecision.ELIGIBLE else "")
        ),
        human_decision=human,
        unacceptable=unacceptable,
        at=T0,
    )


def test_blocked_candidates_are_recorded_too(deployment_data_dir):
    """拦截候选必须留痕：人工放行但系统会拦截 → human_pass_sys_block（门槛太严的信号）。"""
    event = _record(
        deployment_data_dir,
        "cand-blocked",
        GateDecision.BLOCKED,
        human=HumanDecision.ADOPT,
    )
    assert event.would_allow is False
    assert event.diff_category is DiffCategory.HUMAN_PASS_SYS_BLOCK
    events = shadow.load_shadow_events(deployment_data_dir, period=PERIOD)
    assert [item.candidate_version for item in events] == ["cand-blocked"]
    assert events[0].reason


def test_diff_category_four_kinds_are_all_reachable(deployment_data_dir, deployment_config):
    """四类差异分类齐全，且报告计数合计 = 候选数（分类不丢样本）。"""
    _record(deployment_data_dir, "cand-a", GateDecision.ELIGIBLE, human=HumanDecision.REJECT)
    _record(deployment_data_dir, "cand-b", GateDecision.BLOCKED, human=HumanDecision.ADOPT)
    _record(deployment_data_dir, "cand-c", GateDecision.ELIGIBLE, human=HumanDecision.ADOPT)
    _record(
        deployment_data_dir,
        "cand-d",
        GateDecision.INSUFFICIENT_EVIDENCE,
        human=HumanDecision.NONE,
    )
    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert report.passes == 2
    assert report.blocks == 2
    assert report.candidate_count == 4
    assert report.diff_counts == {
        DiffCategory.SYS_PASS_HUMAN_REJECT.value: 1,
        DiffCategory.HUMAN_PASS_SYS_BLOCK.value: 1,
        DiffCategory.AGREE.value: 1,
        DiffCategory.NO_HUMAN_DECISION.value: 1,
    }
    assert report.reason_distribution == {
        GateDecision.BLOCKED.value: 1,
        GateDecision.INSUFFICIENT_EVIDENCE.value: 1,
    }
    payload = json.loads(shadow.report_path(deployment_data_dir, PERIOD).read_text("utf-8"))
    assert payload["period"] == PERIOD and payload["agent_id"] == AGENT


def test_events_log_is_append_only(deployment_data_dir):
    """影子事件留痕只增不改（jsonl 追加）：既有行逐字节保留。"""
    _record(deployment_data_dir, "cand-a")
    first = shadow.events_path(deployment_data_dir).read_text(encoding="utf-8")
    _record(deployment_data_dir, "cand-b")
    second = shadow.events_path(deployment_data_dir).read_text(encoding="utf-8")
    assert second.startswith(first)
    assert len(second.strip().splitlines()) == 2
    assert shadow.load_shadow_events(deployment_data_dir) == shadow.load_shadow_events(
        deployment_data_dir, period=PERIOD
    )


def test_report_met_flag_tracks_shadow_window(deployment_data_dir, deployment_config):
    """报告达标标记 = 影子期双下限（时长累计 + 覆盖候选数），未达标时缺口写入 note。"""
    _record(deployment_data_dir, "cand-a")
    not_met = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert not_met.met is False
    assert not_met.shadow_days == 0.0
    assert "缺口" in not_met.note or "未满" in not_met.note

    mode.set_mode(
        "shadow",
        by="ops",
        reason="开影子",
        cfg=deployment_config,
        data_dir=deployment_data_dir,
        at=T0,
    )
    for _ in range(20):
        mode.record_shadow_candidate(deployment_data_dir, at=T0)
    _record(deployment_data_dir, "cand-b", period="2026-W40")
    met = shadow.build_shadow_report(
        "2026-W40",
        deployment_config,
        data_dir=deployment_data_dir,
        agent_id=AGENT,
        at="2026-10-05T00:00:00+00:00",
    )
    assert met.met is True
    assert met.shadow_days == pytest.approx(14.0)
    assert "已达标" in met.note


def test_report_is_append_only(deployment_data_dir, deployment_config):
    """同名报告只增不改：同刻同内容幂等；内容变化即拒绝覆盖（既有报告逐字节不变）。"""
    _record(deployment_data_dir, "cand-a")
    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    path = shadow.report_path(deployment_data_dir, PERIOD)
    original = path.read_text(encoding="utf-8")
    assert (
        shadow.build_shadow_report(
            PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
        )
        == report
    )
    assert path.read_text(encoding="utf-8") == original

    _record(deployment_data_dir, "cand-b")
    with pytest.raises(DeploymentRecordConflictError, match="只增不改"):
        shadow.build_shadow_report(
            PERIOD,
            deployment_config,
            data_dir=deployment_data_dir,
            agent_id=AGENT,
            at="2026-09-22T00:00:00+00:00",
        )
    assert path.read_text(encoding="utf-8") == original


def test_misadmission_definition_is_shadow_period_specific(deployment_data_dir, deployment_config):
    """影子期误入率口径入报告（口径必须可被证伪、可复算）。"""
    _record(deployment_data_dir, "cand-a", GateDecision.ELIGIBLE, human=HumanDecision.REJECT)
    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert report.misadmission_numerator == 1
    assert report.misadmission_denominator == 1
    assert report.misadmission_rate == pytest.approx(1.0)
    assert "影子" in shadow.MISADMISSION_DEFINITION
    assert "分母" in shadow.MISADMISSION_DEFINITION


def test_misadmission_numerator_unions_reject_and_unacceptable(
    deployment_data_dir, deployment_config
):
    """分子 = 会放行但人工拒绝 ∪ 放行样本中被判不可接受（按候选去重，不重复计数）。"""
    _record(deployment_data_dir, "cand-reject", GateDecision.ELIGIBLE, human=HumanDecision.REJECT)
    _record(
        deployment_data_dir,
        "cand-bad",
        GateDecision.ELIGIBLE,
        human=HumanDecision.ADOPT,
        unacceptable=True,
    )
    _record(
        deployment_data_dir,
        "cand-both",
        GateDecision.ELIGIBLE,
        human=HumanDecision.REJECT,
        unacceptable=True,
    )
    _record(deployment_data_dir, "cand-ok", GateDecision.ELIGIBLE, human=HumanDecision.ADOPT)
    _record(deployment_data_dir, "cand-blocked", GateDecision.BLOCKED, human=HumanDecision.REJECT)

    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert report.misadmission_denominator == 4  # 放行候选数
    assert report.misadmission_numerator == 3  # cand-reject / cand-bad / cand-both（去重）
    assert report.misadmission_rate == pytest.approx(0.75)
    recomputed = shadow.recompute_misadmission_rate(deployment_data_dir, PERIOD, agent_id=AGENT)
    assert recomputed["numerator"] == report.misadmission_numerator
    assert recomputed["denominator"] == report.misadmission_denominator
    assert recomputed["rate"] == pytest.approx(report.misadmission_rate)
    assert recomputed["definition"] == shadow.MISADMISSION_DEFINITION


def test_recompute_matches_report_without_reading_report(deployment_data_dir, deployment_config):
    """重算只读事件留痕（删掉报告仍可复算）——口径不依赖报告自身（SC-007）。"""
    _record(deployment_data_dir, "cand-a", GateDecision.ELIGIBLE, human=HumanDecision.REJECT)
    _record(deployment_data_dir, "cand-b", GateDecision.BLOCKED)
    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    shadow.report_path(deployment_data_dir, PERIOD).unlink()
    recomputed = shadow.recompute_misadmission_rate(deployment_data_dir, PERIOD, agent_id=AGENT)
    assert recomputed["numerator"] == report.misadmission_numerator
    assert recomputed["denominator"] == report.misadmission_denominator


def test_zero_denominator_reports_none_rate(deployment_data_dir, deployment_config):
    """全部拦截 → 分母 0 → 误入率如实为 None（不伪造 0）。"""
    _record(deployment_data_dir, "cand-a", GateDecision.BLOCKED)
    _record(deployment_data_dir, "cand-b", GateDecision.INSUFFICIENT_EVIDENCE)
    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert report.misadmission_denominator == 0
    assert report.misadmission_rate is None
    recomputed = shadow.recompute_misadmission_rate(deployment_data_dir, PERIOD, agent_id=AGENT)
    assert recomputed["rate"] is None


def test_later_event_for_same_candidate_wins(deployment_data_dir, deployment_config):
    """同候选后记事件为准（人工决策晚于判定到达）：报告按候选去重，不重复计样本。"""
    _record(deployment_data_dir, "cand-a", GateDecision.ELIGIBLE, human=HumanDecision.NONE)
    _record(deployment_data_dir, "cand-a", GateDecision.ELIGIBLE, human=HumanDecision.REJECT)
    report = shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert report.candidate_count == 1
    assert report.diff_counts[DiffCategory.SYS_PASS_HUMAN_REJECT.value] == 1
    assert report.misadmission_numerator == 1


def test_report_requires_events_and_known_agent(deployment_data_dir, deployment_config):
    """无事件的周期不产报告（不伪造对照证据）。"""
    with pytest.raises(Exception, match="影子"):
        shadow.build_shadow_report(
            PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
        )


def test_shadow_recording_never_touches_deployment_pointer(
    deployment_data_dir, deployment_config, deployment_pointer_files
):
    """影子期指针变更次数 0 的机制保证：影子路径没有任何指针写入口（机检）。"""
    pointer = deployment_pointer_files()
    before = pointer["config"].read_bytes()
    for index in range(3):
        _record(deployment_data_dir, f"cand-{index}")
    mode.set_mode(
        "shadow",
        by="ops",
        reason="开影子",
        cfg=deployment_config,
        data_dir=deployment_data_dir,
        at=T0,
    )
    mode.record_shadow_candidate(deployment_data_dir, at=T0)
    shadow.build_shadow_report(
        PERIOD, deployment_config, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert pointer["config"].read_bytes() == before
    assert not list((deployment_data_dir / "deploys").glob("*.json"))
    assert not hasattr(shadow, "update_pointer")


def test_report_period_default_derives_iso_week(deployment_data_dir, deployment_config):
    """周期标签默认取 ISO 周（与 010/012 报表同款口径）。"""
    assert shadow.default_period("2026-09-21T00:00:00+00:00") == "2026-W39"
    _record(deployment_data_dir, "cand-a", period=shadow.default_period(T0))
    report = shadow.build_shadow_report(
        shadow.default_period(T0),
        deployment_config,
        data_dir=deployment_data_dir,
        agent_id=AGENT,
        at=T0,
    )
    assert report.period == "2026-W39"


def test_cfg_shadow_limits_are_used_for_met_flag(deployment_data_dir, deployment_config):
    """达标标记用配置下限（显式 0 下限 = 立即达标，配置即口径）。"""
    relaxed = replace(deployment_config, shadow=ShadowConfig(min_days=0, min_candidates=0))
    _record(deployment_data_dir, "cand-a")
    report = shadow.build_shadow_report(
        PERIOD, relaxed, data_dir=deployment_data_dir, agent_id=AGENT, at=T0
    )
    assert report.met is True
