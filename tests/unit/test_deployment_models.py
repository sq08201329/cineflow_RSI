"""部署领域模型单测（功能 014 阶段 2 / T1403，先于实现编写；data-model + 契约 C1~C4）。

覆盖模型层的可机检硬约束：
- 判定优先级枚举（forbidden_agent > insufficient_evidence > blocked > eligible）；
- `GateVerdict` 判定与逐要件状态一致性（eligible 必须前置 + 三要件全满足；
  insufficient_evidence 必须存在 missing 或前置失败；blocked 必须存在未满足且无 missing）；
- 模式迁移合法性（manual→auto 直连拒绝；切换必须带人/时间/理由）与影子计时非负；
- 误入率分子 ≤ 分母且比值与所报数值一致（口径可被证伪，SC-007）；
- 留痕类模型（部署/抽检/回滚）人/时间/理由齐全；回滚 `mode_after` 恒为 manual。

模型层不做配置与文件 I/O（那是 config.py / mode.py 的职责）——此处只断言"非法状态
构造即拒绝"，使机检在数据产生的最早一刻生效。
"""

from dataclasses import FrozenInstanceError

import pytest

from core.deployment.models import (
    DECISION_PRIORITY,
    REQUIREMENTS,
    AutoDeployEvent,
    DeployMode,
    DeployModeState,
    DiffCategory,
    EvidenceBundle,
    EvidenceSnapshot,
    GateDecision,
    GateVerdict,
    HumanDecision,
    RequirementResult,
    RequirementState,
    RollbackEvent,
    RollbackTrigger,
    ShadowEvent,
    ShadowReport,
    SpotCheckConclusion,
    SpotCheckRecord,
    SpotCheckTrigger,
    most_severe_decision,
)
from core.evaluators.errors import ValidationError

_AT = "2026-09-21T10:00:00+00:00"
_AGENT = "visual"
_CANDIDATE = "cand-001"
_DEPLOYED = "dep-000"


def req(name, state=RequirementState.SATISFIED, *, reason="要件取值与口径引用齐全", source="ref"):
    return RequirementResult(
        name=name,
        state=state,
        reason=reason,
        value={"note": name},
        source=source,
    )


def bundle(**overrides):
    """证据包：默认全满足（各要件取值与来源引用齐全）。"""
    payload = {
        "candidate_version": _CANDIDATE,
        "agent_id": _AGENT,
        "deployed_version": _DEPLOYED,
        "unbiasedness": req("unbiasedness"),
        "reward_compare": req("reward_compare"),
        "validation_rank": req("validation_rank"),
        "drift_verdict": req("drift_verdict"),
        "collected_at": _AT,
    }
    payload.update(overrides)
    return EvidenceBundle(**payload)


def verdict(decision=GateDecision.ELIGIBLE, **overrides):
    """判定：默认 eligible + 三要件全满足。"""
    payload = {
        "decision": decision,
        "agent_id": _AGENT,
        "candidate_version": _CANDIDATE,
        "prerequisite": req("unbiasedness"),
        "requirements": tuple(req(name) for name in REQUIREMENTS),
        "reason": f"判定 {decision}",
        "decided_at": _AT,
    }
    payload.update(overrides)
    return GateVerdict(**payload)


def mode_state(**overrides):
    payload = {
        "current": DeployMode.MANUAL,
        "history": (
            {
                "mode": "manual",
                "since": _AT,
                "by": "ops",
                "reason": "初始：全人工审批（现状不变）",
            },
        ),
    }
    payload.update(overrides)
    return DeployModeState(**payload)


# --- 枚举与判定优先级 -------------------------------------------------------


def test_decision_priority_covers_all_decisions():
    """优先级必须覆盖全部判定（枚举新增而漏配 → 机检立刻失败）。"""
    assert set(DECISION_PRIORITY) == set(GateDecision)
    assert (
        DECISION_PRIORITY[GateDecision.FORBIDDEN_AGENT]
        < DECISION_PRIORITY[GateDecision.INSUFFICIENT_EVIDENCE]
    )
    assert (
        DECISION_PRIORITY[GateDecision.INSUFFICIENT_EVIDENCE]
        < DECISION_PRIORITY[GateDecision.BLOCKED]
    )
    assert DECISION_PRIORITY[GateDecision.BLOCKED] < DECISION_PRIORITY[GateDecision.ELIGIBLE]


def test_most_severe_decision_is_deterministic():
    """多判定并存 → 取最严重者（拦截理由的确定性，契约 C2）。"""
    assert (
        most_severe_decision(GateDecision.ELIGIBLE, GateDecision.FORBIDDEN_AGENT)
        is GateDecision.FORBIDDEN_AGENT
    )
    assert (
        most_severe_decision(GateDecision.BLOCKED, GateDecision.INSUFFICIENT_EVIDENCE)
        is GateDecision.INSUFFICIENT_EVIDENCE
    )
    assert most_severe_decision(GateDecision.ELIGIBLE) is GateDecision.ELIGIBLE


# --- 逐要件结果与证据包 -----------------------------------------------------


def test_requirement_result_rejects_incomplete_evidence():
    """有结论必有来源；缺失/不适用必须有理由（不静默、不空白留痕）。"""
    assert req("reward_compare").state is RequirementState.SATISFIED
    with pytest.raises(ValidationError, match="name"):
        RequirementResult(name="", state=RequirementState.SATISFIED, reason="r", source="s")
    with pytest.raises(ValidationError, match="source"):
        RequirementResult(name="reward_compare", state=RequirementState.SATISFIED, reason="r")
    with pytest.raises(ValidationError, match="reason"):
        RequirementResult(name="reward_compare", state=RequirementState.MISSING, reason="")
    with pytest.raises(ValidationError, match="state"):
        RequirementResult(name="reward_compare", state="passed", reason="r")
    with pytest.raises(ValidationError, match="value"):
        RequirementResult(
            name="reward_compare",
            state=RequirementState.SATISFIED,
            reason="r",
            source="s",
            value=None,
        )


def test_evidence_bundle_requires_three_requirements_and_prerequisite():
    """证据包形态固定：前置（无偏性）+ 三要件（reward/validation/drift），缺一即拒。"""
    built = bundle()
    assert [item.name for item in built.requirements] == list(REQUIREMENTS)
    assert built.requirement("drift_verdict").name == "drift_verdict"
    with pytest.raises(ValidationError, match="unbiasedness"):
        bundle(unbiasedness=req("reward_compare"))
    with pytest.raises(ValidationError, match="validation_rank"):
        built_ok = bundle()
        EvidenceBundle(
            candidate_version=_CANDIDATE,
            agent_id=_AGENT,
            deployed_version=_DEPLOYED,
            unbiasedness=built_ok.unbiasedness,
            reward_compare=built_ok.reward_compare,
            validation_rank=req("reward_compare"),
            drift_verdict=built_ok.drift_verdict,
            collected_at=_AT,
        )


def test_evidence_bundle_deployed_version_optional():
    """未部署（无现部署版本）合法：对比对象缺失由要件状态表达（missing），非构造失败。"""
    built = bundle(deployed_version=None)
    assert built.deployed_version is None


# --- 门槛判定一致性 ---------------------------------------------------------


def test_gate_verdict_eligible_requires_all_requirements_satisfied():
    """eligible 必须前置通过 + 三要件同时满足（宪章三要件"同时"的模型层机检）。"""
    verdict()
    with pytest.raises(ValidationError, match="eligible"):
        verdict(prerequisite=req("unbiasedness", RequirementState.MISSING, reason="无结论"))
    with pytest.raises(ValidationError, match="eligible"):
        verdict(
            requirements=(
                req("reward_compare", RequirementState.UNSATISFIED),
                req("validation_rank"),
                req("drift_verdict"),
            )
        )


def test_gate_verdict_insufficient_requires_missing_or_failed_prerequisite():
    """insufficient_evidence 必须由"证据缺失或前置失败"支撑（不能凭空判证据不足）。"""
    insufficient = verdict(
        GateDecision.INSUFFICIENT_EVIDENCE,
        reason="证据不足",
        requirements=(
            req("reward_compare", RequirementState.MISSING, reason="无池化回放结果"),
            req("validation_rank"),
            req("drift_verdict"),
        ),
    )
    assert insufficient.decision is GateDecision.INSUFFICIENT_EVIDENCE
    verdict(
        GateDecision.INSUFFICIENT_EVIDENCE,
        reason="前置无偏性未通过",
        prerequisite=req("unbiasedness", RequirementState.UNSATISFIED),
    )
    with pytest.raises(ValidationError, match="insufficient_evidence"):
        verdict(GateDecision.INSUFFICIENT_EVIDENCE, reason="无依据")


def test_gate_verdict_blocked_requires_unsatisfied_and_no_missing():
    """blocked = 逐要件不满足（证据齐备）；证据缺失必须走 insufficient_evidence。"""
    verdict(
        GateDecision.BLOCKED,
        reason="reward 未高于现部署",
        requirements=(
            req("reward_compare", RequirementState.UNSATISFIED),
            req("validation_rank"),
            req("drift_verdict"),
        ),
    )
    with pytest.raises(ValidationError, match="blocked"):
        verdict(GateDecision.BLOCKED, reason="全体满足却判拦截")
    with pytest.raises(ValidationError, match="blocked"):
        verdict(
            GateDecision.BLOCKED,
            reason="缺证据却判 blocked",
            requirements=(
                req("reward_compare", RequirementState.MISSING, reason="无数据"),
                req("validation_rank"),
                req("drift_verdict"),
            ),
        )


def test_gate_verdict_forbidden_agent_allows_all_requirements_satisfied():
    """禁止名单优先级最高：即使其余全满足，仍判 forbidden_agent（009 名单）。"""
    built = verdict(
        GateDecision.FORBIDDEN_AGENT,
        reason="Agent 属禁止自动进化名单（009）",
        agent_id="screenplay",
    )
    assert built.agent_id == "screenplay"
    with pytest.raises(ValidationError, match="requirements"):
        verdict(requirements=(req("reward_compare"),))


def test_gate_verdict_rejects_unknown_decision_literal():
    with pytest.raises(ValidationError, match="decision"):
        verdict(decision="pass")


def test_gate_verdict_serialises_requirements_and_reason():
    payload = verdict().to_dict()
    assert payload["decision"] == "eligible"
    assert payload["reason"]
    assert [item["name"] for item in payload["requirements"]] == list(REQUIREMENTS)
    assert payload["prerequisite"]["name"] == "unbiasedness"


# --- 证据快照 ---------------------------------------------------------------


def test_evidence_snapshot_serialises_bundle_and_verdict():
    snapshot = EvidenceSnapshot(
        snapshot_id="snap-visual-cand-001-20260921T100000Z",
        agent_id=_AGENT,
        candidate_version=_CANDIDATE,
        fingerprint="f" * 64,
        bundle=bundle(),
        verdict=verdict(),
        created_at=_AT,
    )
    payload = snapshot.to_dict()
    assert payload["fingerprint"] == "f" * 64
    assert payload["bundle"]["candidate_version"] == _CANDIDATE
    assert payload["verdict"]["decision"] == "eligible"
    with pytest.raises(ValidationError, match="fingerprint"):
        EvidenceSnapshot(
            snapshot_id="snap",
            agent_id=_AGENT,
            candidate_version=_CANDIDATE,
            fingerprint="",
            bundle=bundle(),
            verdict=verdict(),
            created_at=_AT,
        )


# --- 模式状态机 -------------------------------------------------------------


def test_mode_transitions_legal_paths():
    """manual ⇄ shadow ⇄ auto：manual→shadow→auto 合法；auto→manual / auto→shadow 合法。"""
    state = mode_state()
    shadow = state.transition(
        DeployMode.SHADOW, at="2026-09-21T11:00:00+00:00", by="ops", reason="开影子"
    )
    assert shadow.current is DeployMode.SHADOW
    assert shadow.shadow_since == "2026-09-21T11:00:00+00:00"
    auto = shadow.transition(
        DeployMode.AUTO, at="2026-10-05T11:00:00+00:00", by="ops", reason="影子期达标"
    )
    assert auto.current is DeployMode.AUTO
    back = auto.transition(
        DeployMode.MANUAL, at="2026-10-06T11:00:00+00:00", by="ops", reason="抽检否决"
    )
    assert back.current is DeployMode.MANUAL
    assert len(back.history) == 4


def test_mode_transition_rejects_manual_to_auto_direct():
    """manual → auto 禁止直连（宪章：必须先影子期）。"""
    state = mode_state()
    with pytest.raises(Exception, match="manual"):
        state.transition(DeployMode.AUTO, at="2026-09-21T11:00:00+00:00", by="ops", reason="直接开")
    assert state.current is DeployMode.MANUAL


def test_mode_transition_requires_human_audit_fields():
    state = mode_state()
    with pytest.raises(ValidationError, match="by"):
        state.transition(DeployMode.SHADOW, at=_AT, by="", reason="开影子")
    with pytest.raises(ValidationError, match="reason"):
        state.transition(DeployMode.SHADOW, at=_AT, by="ops", reason="")
    with pytest.raises(ValidationError, match="since"):
        state.transition(DeployMode.SHADOW, at="", by="ops", reason="缺时间")
    with pytest.raises(Exception, match="非法模式迁移"):
        state.transition(DeployMode.MANUAL, at=_AT, by="ops", reason="原地不动不是迁移")


def test_shadow_interval_accumulation_across_mode_switches():
    """影子计时按模式区间累计：切换即暂停，再进入累加（跨变更不重置）。"""
    state = mode_state()
    day = 24 * 3600
    start = "2026-09-21T00:00:00+00:00"
    shadow = state.transition(DeployMode.SHADOW, at=start, by="ops", reason="开影子")
    left = shadow.transition(
        DeployMode.MANUAL, at="2026-09-24T00:00:00+00:00", by="ops", reason="暂停影子"
    )
    assert left.shadow_days_accumulated == pytest.approx(3.0)
    assert left.shadow_since is None
    again = left.transition(
        DeployMode.SHADOW, at="2026-09-30T00:00:00+00:00", by="ops", reason="恢复"
    )
    ended = again.transition(
        DeployMode.MANUAL, at="2026-10-02T12:00:00+00:00", by="ops", reason="再暂停"
    )
    assert ended.shadow_days_accumulated == pytest.approx(5.5)
    assert ended.shadow_candidate_count == 0
    assert day  # 影子期以天为单位累计（秒 → 天）


def test_shadow_counters_reject_negative_values():
    """影子计时非负（时长与候选数）——负累计即构造失败。"""
    with pytest.raises(ValidationError, match="shadow_days_accumulated"):
        mode_state(shadow_days_accumulated=-0.1)
    with pytest.raises(ValidationError, match="shadow_candidate_count"):
        mode_state(shadow_candidate_count=-1)


def test_shadow_candidate_counting_only_in_shadow_mode():
    """影子候选计数只在 shadow 模式进行（manual/auto 期不得计数）。"""
    state = mode_state()
    with pytest.raises(Exception, match="shadow"):
        state.record_shadow_candidate()
    shadow = state.transition(DeployMode.SHADOW, at=_AT, by="ops", reason="开影子")
    assert shadow.record_shadow_candidate().shadow_candidate_count == 1


def test_shadow_window_gap_reports_missing_days_and_candidates():
    """影子期缺口必须可读（时长/候选数双下限，契约 C4 场景 3）。"""
    state = mode_state(shadow_days_accumulated=2.0, shadow_candidate_count=3)
    gap = state.shadow_window_gap(min_days=14, min_candidates=20)
    assert "14" in gap and "20" in gap and "2" in gap and "3" in gap
    ready = mode_state(shadow_days_accumulated=14.0, shadow_candidate_count=20)
    assert ready.shadow_window_gap(min_days=14, min_candidates=20) == ""


def test_recalibration_flag_requires_reason_and_round_trips():
    """重标定标记（门槛需重新标定）：标记必须带来源理由，可读可清。"""
    state = mode_state()
    marked = state.set_recalibration(True, reason="抽检否决（部署 evt-001）")
    assert marked.recalibration_required is True
    assert "抽检否决" in marked.recalibration_reason
    cleared = marked.set_recalibration(False)
    assert cleared.recalibration_required is False
    assert cleared.recalibration_reason == ""
    with pytest.raises(ValidationError, match="recalibration_reason"):
        mode_state(recalibration_required=True)


def test_mode_state_history_shape_is_validated():
    """变更历史只增不改且形态固定（mode/since/by/reason 齐全，末条 = 当前态）。"""
    with pytest.raises(ValidationError, match="history"):
        mode_state(history=())
    with pytest.raises(ValidationError, match="history"):
        mode_state(
            history=({"mode": "shadow", "since": _AT, "by": "ops", "reason": "与当前态不符"},)
        )
    with pytest.raises(ValidationError, match="by"):
        mode_state(history=({"mode": "manual", "since": _AT, "by": "", "reason": "缺人"},))


def test_mode_state_round_trips_through_dict():
    state = mode_state(shadow_days_accumulated=1.5, shadow_candidate_count=4)
    restored = DeployModeState.from_dict(state.to_dict())
    assert restored == state
    with pytest.raises(ValidationError, match="mode"):
        DeployModeState.from_dict({**state.to_dict(), "current": "paused"})


# --- 影子事件与对照报告 -----------------------------------------------------


def test_shadow_event_diff_classification_four_categories():
    """差异分类四类：系统放行人拒 / 人放行系统拦 / 一致 / 无人工决策。"""
    assert ShadowEvent.classify(True, HumanDecision.REJECT) is DiffCategory.SYS_PASS_HUMAN_REJECT
    assert ShadowEvent.classify(False, HumanDecision.ADOPT) is DiffCategory.HUMAN_PASS_SYS_BLOCK
    assert ShadowEvent.classify(True, HumanDecision.ADOPT) is DiffCategory.AGREE
    assert ShadowEvent.classify(False, HumanDecision.REJECT) is DiffCategory.AGREE
    assert ShadowEvent.classify(True, HumanDecision.NONE) is DiffCategory.NO_HUMAN_DECISION


def test_shadow_event_rejects_inconsistent_verdict_and_category():
    """ "若 auto 会放行与否"必须由判定推导；差异分类必须与双方决策一致（防手填）。"""
    event = ShadowEvent(
        period="2026-W39",
        agent_id=_AGENT,
        candidate_version=_CANDIDATE,
        verdict=GateDecision.ELIGIBLE,
        would_allow=True,
        human_decision=HumanDecision.REJECT,
        diff_category=DiffCategory.SYS_PASS_HUMAN_REJECT,
        reason="判定 eligible；人工拒绝",
        recorded_at=_AT,
    )
    assert event.would_allow is True
    with pytest.raises(ValidationError, match="would_allow"):
        ShadowEvent(
            period="2026-W39",
            agent_id=_AGENT,
            candidate_version=_CANDIDATE,
            verdict=GateDecision.BLOCKED,
            would_allow=True,
            human_decision=HumanDecision.REJECT,
            diff_category=DiffCategory.AGREE,
            reason="判定与放行标记不符",
            recorded_at=_AT,
        )
    with pytest.raises(ValidationError, match="diff_category"):
        ShadowEvent(
            period="2026-W39",
            agent_id=_AGENT,
            candidate_version=_CANDIDATE,
            verdict=GateDecision.ELIGIBLE,
            would_allow=True,
            human_decision=HumanDecision.REJECT,
            diff_category=DiffCategory.AGREE,
            reason="分类与决策不符",
            recorded_at=_AT,
        )


def report(**overrides):
    payload = {
        "period": "2026-W39",
        "agent_id": _AGENT,
        "shadow_days": 14.0,
        "candidate_count": 3,
        "passes": 1,
        "blocks": 2,
        "reason_distribution": {"reward_compare": 1, "drift_verdict": 1},
        "diff_counts": {
            "sys_pass_human_reject": 1,
            "human_pass_sys_block": 0,
            "agree": 1,
            "no_human_decision": 1,
        },
        "misadmission_numerator": 1,
        "misadmission_denominator": 1,
        "misadmission_rate": 1.0,
        "met": True,
        "note": "影子期下限已满足",
        "generated_at": _AT,
    }
    payload.update(overrides)
    return ShadowReport(**payload)


def test_shadow_report_counts_must_close():
    """放行 + 拦截 = 候选数；差异分类计数合计 = 候选数（报表数字必须自洽）。"""
    report()
    with pytest.raises(ValidationError, match="passes"):
        report(passes=0, blocks=0)
    with pytest.raises(ValidationError, match="diff_counts"):
        report(diff_counts={"agree": 1})


def test_shadow_report_misadmission_fraction_and_rate():
    """误入率：分子 ≤ 分母；比值必须与所报数值一致（口径可被证伪，SC-007）。"""
    report(misadmission_numerator=0, misadmission_denominator=4, misadmission_rate=0.0)
    with pytest.raises(ValidationError, match="misadmission"):
        report(misadmission_numerator=2, misadmission_denominator=1, misadmission_rate=2.0)
    with pytest.raises(ValidationError, match="misadmission_rate"):
        report(misadmission_numerator=1, misadmission_denominator=4, misadmission_rate=0.5)
    zero_denominator = report(
        misadmission_numerator=0, misadmission_denominator=0, misadmission_rate=None
    )
    assert zero_denominator.misadmission_rate is None
    with pytest.raises(ValidationError, match="misadmission_rate"):
        report(misadmission_numerator=0, misadmission_denominator=0, misadmission_rate=0.0)


# --- 部署/抽检/回滚留痕 -----------------------------------------------------


def test_auto_deploy_event_pointer_and_source_consistency():
    """部署留痕：指针改写前 = 现部署版本、改写后 = 候选版本；来源恒为 auto（谱系口径）。"""
    event = AutoDeployEvent(
        agent_id=_AGENT,
        candidate_version=_CANDIDATE,
        from_version=_DEPLOYED,
        evidence_snapshot="deployment/evidence/visual/cand-001.20260921T100000Z.json",
        deployed_at=_AT,
        pointer_before=_DEPLOYED,
        pointer_after=_CANDIDATE,
        reason="门槛 eligible",
    )
    assert event.source == "auto"
    with pytest.raises(ValidationError, match="pointer_after"):
        AutoDeployEvent(
            agent_id=_AGENT,
            candidate_version=_CANDIDATE,
            from_version=_DEPLOYED,
            evidence_snapshot="snap.json",
            deployed_at=_AT,
            pointer_before=_DEPLOYED,
            pointer_after=_DEPLOYED,
            reason="指针未切到候选",
        )
    with pytest.raises(ValidationError, match="pointer_before"):
        AutoDeployEvent(
            agent_id=_AGENT,
            candidate_version=_CANDIDATE,
            from_version="other-000",
            evidence_snapshot="snap.json",
            deployed_at=_AT,
            pointer_before=_DEPLOYED,
            pointer_after=_CANDIDATE,
            reason="留痕与指针不符",
        )
    with pytest.raises(ValidationError, match="source"):
        AutoDeployEvent(
            agent_id=_AGENT,
            candidate_version=_CANDIDATE,
            from_version=_DEPLOYED,
            evidence_snapshot="snap.json",
            deployed_at=_AT,
            pointer_before=_DEPLOYED,
            pointer_after=_CANDIDATE,
            reason="来源非法",
            source="manual",
        )


def test_spot_check_record_requires_human_audit_fields():
    """抽检留痕：序号 ≥1、触发方式枚举、结论枚举、人/时间/理由齐全。"""
    record = SpotCheckRecord(
        agent_id=_AGENT,
        deploy_event="deployment/deploys/20260921T100000Z-visual.json",
        seq=1,
        trigger=SpotCheckTrigger.FIRST_N,
        conclusion=SpotCheckConclusion.PASS,
        by="ops",
        at=_AT,
        reason="前 5 次全量复核通过",
    )
    assert record.trigger is SpotCheckTrigger.FIRST_N
    with pytest.raises(ValidationError, match="seq"):
        SpotCheckRecord(
            agent_id=_AGENT,
            deploy_event="evt.json",
            seq=0,
            trigger=SpotCheckTrigger.RATIO,
            conclusion=SpotCheckConclusion.PASS,
            by="ops",
            at=_AT,
            reason="序号从 1 起",
        )
    with pytest.raises(ValidationError, match="conclusion"):
        SpotCheckRecord(
            agent_id=_AGENT,
            deploy_event="evt.json",
            seq=2,
            trigger=SpotCheckTrigger.RATIO,
            conclusion="maybe",
            by="ops",
            at=_AT,
            reason="结论非法",
        )
    with pytest.raises(ValidationError, match="by"):
        SpotCheckRecord(
            agent_id=_AGENT,
            deploy_event="evt.json",
            seq=2,
            trigger=SpotCheckTrigger.RATIO,
            conclusion=SpotCheckConclusion.VETO,
            by="",
            at=_AT,
            reason="缺复核人",
        )


def test_rollback_event_forces_manual_mode_and_fresh_versions():
    """三件事之一：回滚后模式恒为 manual；回滚必须换版本；标记重标定。"""
    event = RollbackEvent(
        agent_id=_AGENT,
        from_version=_CANDIDATE,
        to_version=_DEPLOYED,
        trigger=RollbackTrigger.SPOT_CHECK_VETO,
        at=_AT,
        mode_after=DeployMode.MANUAL,
        recalibration_required=True,
        by="ops",
        reason="抽检否决：产出质量不达线",
        note="三件事：指针回滚 + 模式 manual + 门槛待重标定",
    )
    assert event.mode_after is DeployMode.MANUAL
    with pytest.raises(ValidationError, match="mode_after"):
        RollbackEvent(
            agent_id=_AGENT,
            from_version=_CANDIDATE,
            to_version=_DEPLOYED,
            trigger=RollbackTrigger.SPOT_CHECK_VETO,
            at=_AT,
            mode_after=DeployMode.AUTO,
            recalibration_required=True,
            by="ops",
            reason="回滚后不得停在 auto",
        )
    with pytest.raises(ValidationError, match="to_version"):
        RollbackEvent(
            agent_id=_AGENT,
            from_version=_CANDIDATE,
            to_version=_CANDIDATE,
            trigger=RollbackTrigger.MANUAL,
            at=_AT,
            mode_after=DeployMode.MANUAL,
            recalibration_required=False,
            by="ops",
            reason="版本须变化",
        )
    with pytest.raises(ValidationError, match="trigger"):
        RollbackEvent(
            agent_id=_AGENT,
            from_version=_CANDIDATE,
            to_version=_DEPLOYED,
            trigger="timeout",
            at=_AT,
            mode_after=DeployMode.MANUAL,
            recalibration_required=False,
            by="ops",
            reason="触发源非法",
        )


def test_models_are_frozen():
    """全部模型 frozen（留痕不可改写）。"""
    event = AutoDeployEvent(
        agent_id=_AGENT,
        candidate_version=_CANDIDATE,
        from_version=_DEPLOYED,
        evidence_snapshot="snap.json",
        deployed_at=_AT,
        pointer_before=_DEPLOYED,
        pointer_after=_CANDIDATE,
        reason="门槛 eligible",
    )
    with pytest.raises(FrozenInstanceError):
        event.pointer_after = "other"
    state = mode_state()
    with pytest.raises(FrozenInstanceError):
        state.current = DeployMode.AUTO
