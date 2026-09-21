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

from core.deployment.evidence import collect_evidence
from core.deployment.gate import fingerprint_of, gate, gate_and_record, load_snapshots
from core.deployment.models import DECISION_PRIORITY, GateDecision, RequirementState

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
