"""部署评估契约聚合（功能 014 US1 / T1417）：证据包与门槛判定 C1~C3 端到端断言。

三份契约之中本批只覆盖 `contracts/evidence-gate.md`（C4~C10 由 US2/US3 的 T1418/T1425 补）：

- **C1 证据包**：三要件 + 前置（无偏性）齐备 / 前置失败 / 要件缺失 / 无 judge 的
  `not_applicable`——口径全部引用既有实现（011 池化对比、005 validation 排名、012 verdict）；
- **C2 门槛判定**：优先级 `forbidden_agent > insufficient_evidence > blocked > eligible`，
  组合矩阵全覆盖 + **双向断言**（满足即放行、不满足即拦截，SC-001/SC-002）；
- **C3 证据快照**：`deployment/evidence/{agent}/{candidate}.{ts}.json`，同判定幂等、
  证据变化新快照、旧快照逐字节不可改写。

机检口径（SC 落点）：遍历全矩阵断言"判定为 eligible 当且仅当三要件同时满足"；证据缺失与
禁止名单 100% 非 eligible；快照重复落盘零字节变化。
"""

import json
from dataclasses import replace

import pytest

from core.deployment import mode as deploy_mode
from core.deployment import shadow as shadow_module
from core.deployment.auto_deploy import DeployAction, evaluate_candidate
from core.deployment.config import ShadowConfig
from core.deployment.errors import DeploymentRecordConflictError, ModeTransitionError
from core.deployment.evidence import collect_evidence
from core.deployment.gate import fingerprint_of, gate, gate_and_record, load_snapshots
from core.deployment.models import (
    DECISION_PRIORITY,
    DeployMode,
    GateDecision,
    HumanDecision,
    RequirementState,
)

AGENT = "visual"
CANDIDATE = "cand-001"


def _collect(cfg, kwargs):
    return collect_evidence(cfg=cfg, **kwargs)


def test_c1_evidence_bundle_scenarios(deployment_evidence_matrix, deployment_config):
    """C1：四类证据形态各就位，且取数只走既有口径（不新造对比/排名/漂移逻辑）。"""
    all_ok = _collect(deployment_config, deployment_evidence_matrix["all_satisfied"]["kwargs"])
    assert all_ok.unbiasedness.state is RequirementState.SATISFIED
    assert [item.state for item in all_ok.requirements] == [RequirementState.SATISFIED] * 3
    for item in all_ok.requirements:
        assert item.value and item.source and item.reason

    failed = _collect(
        deployment_config, deployment_evidence_matrix["unbiasedness_failed"]["kwargs"]
    )
    assert failed.unbiasedness.state is RequirementState.UNSATISFIED

    for case, name in (
        ("missing_reward", "reward_compare"),
        ("missing_validation", "validation_rank"),
        ("missing_drift", "drift_verdict"),
    ):
        bundle = _collect(deployment_config, deployment_evidence_matrix[case]["kwargs"])
        assert bundle.requirement(name).state is RequirementState.MISSING, case

    no_judge = _collect(deployment_config, deployment_evidence_matrix["no_judge"]["kwargs"])
    assert no_judge.requirement("drift_verdict").state is RequirementState.NOT_APPLICABLE


def test_c2_full_matrix_both_ways(deployment_evidence_matrix, deployment_config):
    """C2：矩阵双向断言——eligible ⇔ 前置通过 ∧ 三要件同时满足（缺证据即拦截）。"""
    for case_name, case in deployment_evidence_matrix.items():
        verdict = gate(_collect(deployment_config, case["kwargs"]), deployment_config)
        if verdict.decision is GateDecision.ELIGIBLE:
            # 放行侧：前置通过且三要件同时满足（少一个都不许放行）
            assert verdict.prerequisite.state is RequirementState.SATISFIED, case_name
            assert all(item.state is RequirementState.SATISFIED for item in verdict.requirements), (
                case_name
            )
        else:
            # 拦截侧：理由必须落到具体要件（可归因，不空话）
            assert verdict.reason, case_name
        assert verdict.decision.value == case["expected_decision"], case_name


def test_c2_priority_ordering_is_deterministic(deployment_evidence_matrix, deployment_config):
    """C2：优先级序 forbidden > insufficient > blocked > eligible 在判定值上体现。"""
    assert list(
        sorted(
            DECISION_PRIORITY,
            key=lambda decision: DECISION_PRIORITY[decision],
        )
    ) == [
        GateDecision.FORBIDDEN_AGENT,
        GateDecision.INSUFFICIENT_EVIDENCE,
        GateDecision.BLOCKED,
        GateDecision.ELIGIBLE,
    ]
    forbidden_and_incomplete = dict(deployment_evidence_matrix["forbidden_agent"]["kwargs"])
    forbidden_and_incomplete["unbiasedness"] = None
    forbidden_and_incomplete["validation_rewards"] = None
    verdict = gate(_collect(deployment_config, forbidden_and_incomplete), deployment_config)
    assert verdict.decision is GateDecision.FORBIDDEN_AGENT


def test_sc002_missing_evidence_and_forbidden_list_never_eligible(
    deployment_evidence_matrix, deployment_config
):
    """SC-002 机检：证据缺失档与禁止名单档 100% 非 eligible（宁可拦截）。"""
    for case in (
        "unbiasedness_missing",
        "unbiasedness_failed",
        "missing_reward",
        "missing_validation",
        "missing_drift",
        "no_judge",
        "forbidden_agent",
    ):
        verdict = gate(
            _collect(deployment_config, deployment_evidence_matrix[case]["kwargs"]),
            deployment_config,
        )
        assert verdict.decision is not GateDecision.ELIGIBLE, case


def test_no_judge_relaxation_is_explicit_and_auditable(
    deployment_evidence_matrix, deployment_config
):
    """无 judge 的保守默认可被配置显式放开，但放开本身必须写在配置里（可审计）。"""
    bundle = _collect(deployment_config, deployment_evidence_matrix["no_judge"]["kwargs"])
    assert gate(bundle, deployment_config).decision is GateDecision.BLOCKED
    waived = replace(
        deployment_config, gate=replace(deployment_config.gate, allow_without_judge=True)
    )
    assert gate(bundle, waived).decision is GateDecision.ELIGIBLE
    assert deployment_config.gate.allow_without_judge is False  # 原配置未被就地修改


def test_c3_snapshot_idempotent_and_immutable(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """C3：同判定重复 → 幂等；证据变化 → 新快照；旧快照逐字节不变（不可改写）。"""
    kwargs = deployment_evidence_matrix["all_satisfied"]["kwargs"]
    bundle = _collect(deployment_config, kwargs)
    verdict, path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    assert verdict.decision is GateDecision.ELIGIBLE
    first_bytes = path.read_bytes()
    assert path.parent.name == AGENT and path.name.startswith(f"{CANDIDATE}.")

    again_verdict, again_path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    assert again_path == path
    assert again_verdict.decision is verdict.decision
    assert path.read_bytes() == first_bytes
    assert len(load_snapshots(deployment_data_dir, AGENT, CANDIDATE)) == 1

    payload = json.loads(first_bytes.decode("utf-8"))
    assert payload["fingerprint"] == fingerprint_of(bundle, verdict)
    assert set(payload) >= {
        "snapshot_id",
        "agent_id",
        "candidate_version",
        "created_at",
        "bundle",
        "verdict",
    }
    assert payload["verdict"]["decided_at"] == verdict.decided_at


def test_c3_interception_is_also_snapshotted(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """C3：拦截判定同样留痕（影子对照与误入率归因的取数基础）。"""
    for case in ("drift_unsatisfied", "unbiasedness_missing"):
        bundle = _collect(deployment_config, deployment_evidence_matrix[case]["kwargs"])
        verdict, path = gate_and_record(bundle, deployment_config, deployment_data_dir)
        assert verdict.decision is not GateDecision.ELIGIBLE, case
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["verdict"]["reason"], case
        assert payload["verdict"]["decision"] == verdict.decision.value


def test_c3_snapshot_records_prerequisite_and_threshold_sources(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """C3：快照自描述口径（各要件来源引用 + 判定理由），供事后复算与审计。"""
    bundle = _collect(deployment_config, deployment_evidence_matrix["all_satisfied"]["kwargs"])
    _, path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    bundle_payload = payload["bundle"]
    sources = {
        name: bundle_payload[name]["source"]
        for name in ("unbiasedness", "reward_compare", "validation_rank", "drift_verdict")
    }
    assert set(sources) == {
        "unbiasedness",
        "reward_compare",
        "validation_rank",
        "drift_verdict",
    }
    assert all(value for value in sources.values())


# ---------------------------------------------------------------------------
# shadows-mode 段（功能 014 US2 / T1418）：契约 C4（模式状态机与影子期门禁）
# 与 C5（影子事件 / 对照报告 / 误入率可重算）端到端聚合。
#
# 机检口径：①影子期部署指针变更次数 0（configs 副本逐字节 + deploys 零留痕）；
# ②影子期未满开启 auto 100% 被拒（含缺口说明，拒绝零副作用）；
# ③误入率从事件留痕重算 == 报告值（SC-007）；④拦截候选同样留痕（差异分类取得到
# human_pass_sys_block）。影子期运行统一走唯一入口 evaluate_candidate，避免旁路口径。
# ---------------------------------------------------------------------------

SHADOW_PERIOD = "2026-W39"
SHADOW_T0 = "2026-09-21T00:00:00+00:00"
SHADOW_AT = "2026-10-05T00:00:00+00:00"  # +14 天（影子期时长下限）
JUDGE_KEY = "judge.cinematic@1.0.0"


def _shadow_cfg(deployment_config, *, min_days=0, min_candidates=0):
    """宽松影子下限档（门禁行为另有真实下限用例覆盖）。"""
    return replace(
        deployment_config, shadow=ShadowConfig(min_days=min_days, min_candidates=min_candidates)
    )


def _passing_evidence(registry, candidate, deployed):
    return {
        "deployed_version": deployed,
        "unbiasedness": {"verdict": "pass", "tau": 0.82, "threshold": 0.6},
        "reward_compare": {
            "candidate": 0.62,
            "deployed": 0.55,
            "source": "replay/pools/pool-a.json",
        },
        "validation_rewards": {candidate: 0.8, deployed: 0.5},
        "judge_keys": (JUDGE_KEY,),
        "drift_registry": registry,
    }


def test_c4_mode_machine_refuses_direct_auto_and_short_window(
    deployment_data_dir, deployment_config
):
    """C4：manual → auto 禁止直连；影子期未满拒绝并注明缺口；拒绝零副作用。"""
    with pytest.raises(ModeTransitionError, match="manual"):
        deploy_mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="直接开自动",
            cfg=deployment_config,
            data_dir=deployment_data_dir,
            at=SHADOW_T0,
        )
    assert not deploy_mode.mode_path(deployment_data_dir).exists()

    deploy_mode.set_mode(
        DeployMode.SHADOW,
        by="ops",
        reason="开影子期",
        cfg=deployment_config,
        data_dir=deployment_data_dir,
        at=SHADOW_T0,
    )
    before = deploy_mode.mode_path(deployment_data_dir).read_bytes()
    with pytest.raises(ModeTransitionError) as excinfo:
        deploy_mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="影子期未满即申请",
            cfg=deployment_config,
            data_dir=deployment_data_dir,
            at=SHADOW_T0,
        )
    assert "14" in str(excinfo.value) and "20" in str(excinfo.value)
    assert deploy_mode.mode_path(deployment_data_dir).read_bytes() == before


def test_c4_auto_allowed_only_with_both_limits_and_no_recalibration(
    deployment_data_dir, deployment_config
):
    """C4：双下限满足才允许 auto；重标定标记存在时一律拒绝（FR-009）。"""
    cfg = _shadow_cfg(deployment_config, min_days=1, min_candidates=2)
    deploy_mode.set_mode(
        DeployMode.SHADOW,
        by="ops",
        reason="开影子期",
        cfg=cfg,
        data_dir=deployment_data_dir,
        at=SHADOW_T0,
    )
    for _ in range(2):
        deploy_mode.record_shadow_candidate(deployment_data_dir, at=SHADOW_T0)
    deploy_mode.set_recalibration(
        deployment_data_dir, required=True, reason="抽检否决：门槛需重新标定", at=SHADOW_T0
    )
    with pytest.raises(ModeTransitionError, match="重标定"):
        deploy_mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="重标定期间申请",
            cfg=cfg,
            data_dir=deployment_data_dir,
            at="2026-09-22T00:00:00+00:00",
        )
    deploy_mode.clear_recalibration(
        deployment_data_dir, by="ops", reason="新阈值已重标定", at="2026-09-22T00:00:00+00:00"
    )
    with pytest.raises(ModeTransitionError, match="影子期"):
        deploy_mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="清标记后直接开自动",
            cfg=cfg,
            data_dir=deployment_data_dir,
            at="2026-09-22T00:00:00+00:00",
        )
    # 清标记后仍在影子期，但计时已清零 → 重跑影子期（重新累计时长与候选数）
    for _ in range(2):
        deploy_mode.record_shadow_candidate(deployment_data_dir, at="2026-09-22T00:00:00+00:00")
    state = deploy_mode.set_mode(
        DeployMode.AUTO,
        by="ops",
        reason="影子期重跑达标",
        cfg=cfg,
        data_dir=deployment_data_dir,
        at="2026-09-23T00:00:00+00:00",
    )
    assert state.current is DeployMode.AUTO


def test_c5_shadow_run_records_events_and_keeps_pointer_byte_identical(
    deployment_data_dir, deployment_config, deployment_drift_registry, deployment_pointer_files
):
    """C5 + SC-003 机检：影子期跑 N 个候选（含拦截）→ 事件留痕齐全、指针变更 0。"""
    pointer = deployment_pointer_files()
    before = pointer["config"].read_bytes()
    cfg = _shadow_cfg(deployment_config)
    deploy_mode.set_mode(
        DeployMode.SHADOW,
        by="ops",
        reason="开影子期",
        cfg=cfg,
        data_dir=deployment_data_dir,
        at=SHADOW_T0,
    )
    registry = deployment_drift_registry("normal")
    for candidate, human in (
        ("cand-pass", HumanDecision.ADOPT),
        ("cand-rejected", HumanDecision.REJECT),
        ("cand-blocked", HumanDecision.ADOPT),
    ):
        evidence = _passing_evidence(registry, candidate, "dep-000")
        if candidate == "cand-blocked":
            evidence["reward_compare"] = {
                "candidate": 0.5,
                "deployed": 0.55,
                "source": "replay/pools/pool-a.json",
            }
        outcome = evaluate_candidate(
            "visual",
            candidate,
            cfg=cfg,
            data_dir=deployment_data_dir,
            period=SHADOW_PERIOD,
            human_decision=human,
            at=SHADOW_T0,
            **evidence,
        )
        assert outcome.action is DeployAction.SHADOW_RECORDED
        assert outcome.deploy_event is None
    assert pointer["config"].read_bytes() == before
    assert not list((deployment_data_dir / "deploys").glob("*.json"))
    report = shadow_module.build_shadow_report(
        SHADOW_PERIOD, cfg, data_dir=deployment_data_dir, agent_id="visual", at=SHADOW_AT
    )
    assert report.candidate_count == 3
    assert report.passes == 2 and report.blocks == 1
    assert report.diff_counts["sys_pass_human_reject"] == 1
    assert report.diff_counts["agree"] == 1
    assert report.diff_counts["human_pass_sys_block"] == 1
    assert report.reason_distribution == {"blocked": 1}
    assert report.note  # 口径说明与缺口/达标说明
    state = deploy_mode.load_mode_state(deployment_data_dir)
    assert state.shadow_candidate_count == 3
    assert state.shadow_days_accumulated == pytest.approx(0.0)  # 仍在影子期（切出才结算）


def test_c5_misadmission_recompute_equals_report(deployment_data_dir, deployment_config):
    """SC-007 机检：误入率可从事件留痕重算，且与报告值逐字段一致。"""
    cfg = _shadow_cfg(deployment_config)
    for candidate, human, unacceptable in (
        ("cand-a", HumanDecision.REJECT, False),
        ("cand-b", HumanDecision.ADOPT, True),
        ("cand-c", HumanDecision.ADOPT, False),
    ):
        shadow_module.record_shadow_event(
            "visual",
            candidate,
            "eligible",
            data_dir=deployment_data_dir,
            period=SHADOW_PERIOD,
            reason="判定 eligible",
            human_decision=human,
            unacceptable=unacceptable,
            at=SHADOW_T0,
        )
    report = shadow_module.build_shadow_report(
        SHADOW_PERIOD, cfg, data_dir=deployment_data_dir, agent_id="visual", at=SHADOW_T0
    )
    recomputed = shadow_module.recompute_misadmission_rate(
        deployment_data_dir, SHADOW_PERIOD, agent_id="visual"
    )
    assert report.misadmission_numerator == 2
    assert report.misadmission_denominator == 3
    assert recomputed["numerator"] == report.misadmission_numerator
    assert recomputed["denominator"] == report.misadmission_denominator
    assert recomputed["rate"] == pytest.approx(report.misadmission_rate)
    assert recomputed["definition"] == shadow_module.MISADMISSION_DEFINITION


def test_c5_report_is_append_only(deployment_data_dir, deployment_config):
    """C5：对照报告不可改写（内容变化即拒绝覆盖，旧报告逐字节保留）。"""
    cfg = _shadow_cfg(deployment_config)
    shadow_module.record_shadow_event(
        "visual",
        "cand-a",
        "eligible",
        data_dir=deployment_data_dir,
        period=SHADOW_PERIOD,
        reason="判定 eligible",
        at=SHADOW_T0,
    )
    shadow_module.build_shadow_report(
        SHADOW_PERIOD, cfg, data_dir=deployment_data_dir, agent_id="visual", at=SHADOW_T0
    )
    path = shadow_module.report_path(deployment_data_dir, SHADOW_PERIOD)
    original = path.read_bytes()
    shadow_module.record_shadow_event(
        "visual",
        "cand-b",
        "blocked",
        data_dir=deployment_data_dir,
        period=SHADOW_PERIOD,
        reason="要件不满足",
        at=SHADOW_T0,
    )
    with pytest.raises(DeploymentRecordConflictError):
        shadow_module.build_shadow_report(
            SHADOW_PERIOD, cfg, data_dir=deployment_data_dir, agent_id="visual", at=SHADOW_AT
        )
    assert path.read_bytes() == original
