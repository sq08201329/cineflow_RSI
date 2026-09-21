"""门槛判定单测（功能 014 US1 / T1410，先于实现编写；契约 C2/C3 + FR-002/FR-003）。

覆盖：
- 组合矩阵双向断言（全满足 → eligible；单要件不满足 → blocked + 理由；证据缺失/前置失败
  → insufficient_evidence；禁止名单 → forbidden_agent）；
- 优先级机检：`forbidden_agent` > `insufficient_evidence` > `blocked` > `eligible`；
- 无 judge 的 Agent + `allow_without_judge=false`（默认）→ blocked（保守）；
  显式放开（true）→ 该要件不构成拦截；
- 证据快照落盘 `deployment/evidence/{agent}/{candidate}.{ts}.json`：判定幂等（同证据同判定
  → 同一文件、逐字节不变）、证据变化 → 新快照且旧快照保留（不可改写，原则二）。
"""

import json
from dataclasses import replace

import pytest

from core.deployment.errors import DeploymentEvidenceError
from core.deployment.evidence import collect_evidence, reward_compare_payload
from core.deployment.gate import fingerprint_of, gate, gate_and_record, load_snapshots
from core.deployment.models import GateDecision, RequirementState

AGENT = "visual"
CANDIDATE = "cand-001"
DEPLOYED = "dep-000"
ATTESTATION = {"verdict": "pass", "tau": 0.82, "threshold": 0.6}


def _bundle(cfg, matrix, case):
    return collect_evidence(cfg=cfg, **matrix[case]["kwargs"])


def test_matrix_decisions_and_priority(deployment_evidence_matrix, deployment_config):
    """组合矩阵：每档判定与期望一致，理由非空，逐要件状态齐全（双向断言）。"""
    for case_name, case in deployment_evidence_matrix.items():
        verdict = gate(
            _bundle(deployment_config, deployment_evidence_matrix, case_name), deployment_config
        )
        assert verdict.decision.value == case["expected_decision"], case_name
        assert verdict.reason, case_name
        assert verdict.prerequisite.name == "unbiasedness"
        assert {item.name for item in verdict.requirements} == {
            "reward_compare",
            "validation_rank",
            "drift_verdict",
        }
        assert verdict.candidate_version == CANDIDATE
        assert verdict.decided_at


def test_blocked_reason_names_the_failing_requirement(
    deployment_evidence_matrix, deployment_config
):
    """单要件不满足 → blocked 且理由点名该要件（拦截理由必须可归因）。"""
    for case, expected_name in (
        ("reward_unsatisfied", "reward_compare"),
        ("validation_unsatisfied", "validation_rank"),
        ("drift_unsatisfied", "drift_verdict"),
    ):
        verdict = gate(
            _bundle(deployment_config, deployment_evidence_matrix, case), deployment_config
        )
        assert verdict.decision is GateDecision.BLOCKED
        assert expected_name in verdict.reason


def test_insufficient_evidence_reason_marks_missing_evidence(
    deployment_evidence_matrix, deployment_config
):
    """缺证据即拦截：判定为 insufficient_evidence 且理由注明"证据不足"与具体缺口。"""
    for case in (
        "unbiasedness_missing",
        "unbiasedness_failed",
        "missing_reward",
        "missing_validation",
        "missing_drift",
    ):
        verdict = gate(
            _bundle(deployment_config, deployment_evidence_matrix, case), deployment_config
        )
        assert verdict.decision is GateDecision.INSUFFICIENT_EVIDENCE, case
        assert "证据不足" in verdict.reason, case


def test_forbidden_agent_outranks_insufficient_evidence(
    deployment_evidence_matrix, deployment_config
):
    """禁止名单优先级最高：即使证据缺失也判 forbidden_agent（009 名单不可被绕过）。"""
    forbidden = dict(deployment_evidence_matrix["forbidden_agent"]["kwargs"])
    forbidden["drift_registry"] = None  # 证据同时缺失
    verdict = gate(collect_evidence(cfg=deployment_config, **forbidden), deployment_config)
    assert verdict.decision is GateDecision.FORBIDDEN_AGENT
    assert "名单" in verdict.reason
    assert verdict.prerequisite.state is RequirementState.SATISFIED


def test_agent_without_judge_is_blocked_by_default_but_configurable(
    deployment_evidence_matrix, deployment_config
):
    """无 judge 的 Agent：默认保守拦截；`allow_without_judge=true` 显式放开才放行。"""
    bundle = _bundle(deployment_config, deployment_evidence_matrix, "no_judge")
    assert bundle.agent_id == "promo"
    blocked = gate(bundle, deployment_config)
    assert blocked.decision is GateDecision.BLOCKED
    assert "judge" in blocked.reason
    waived = replace(
        deployment_config, gate=replace(deployment_config.gate, allow_without_judge=True)
    )
    eligible = gate(bundle, waived)
    assert eligible.decision is GateDecision.ELIGIBLE
    assert "not_applicable" in eligible.reason or "不适用" in eligible.reason


def test_gate_is_pure_and_recording_writes_snapshot(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """判定纯函数（不落盘）；落盘由 `gate_and_record` 显式触发（快照可追溯）。"""
    bundle = _bundle(deployment_config, deployment_evidence_matrix, "all_satisfied")
    verdict = gate(bundle, deployment_config)
    assert not list((deployment_data_dir / "evidence").rglob("*.json"))
    recorded, path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    assert recorded.decision is verdict.decision
    assert recorded.requirements == verdict.requirements
    assert recorded.prerequisite == verdict.prerequisite
    assert path.is_file()
    assert path.parent == deployment_data_dir / "evidence" / AGENT
    assert path.name.startswith(f"{CANDIDATE}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["fingerprint"] == fingerprint_of(bundle, recorded)
    assert payload["verdict"]["decision"] == "eligible"
    assert payload["bundle"]["candidate_version"] == CANDIDATE


def test_snapshot_is_idempotent_for_same_evidence_and_decision(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """C3：同候选同证据重复判定 → 幂等（同一文件、逐字节不变、不新增文件）。"""
    bundle = _bundle(deployment_config, deployment_evidence_matrix, "all_satisfied")
    first_verdict, first_path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    before = (first_path.stat().st_size, first_path.read_text(encoding="utf-8"))
    second_verdict, second_path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    assert second_path == first_path
    assert second_verdict.decision is first_verdict.decision
    assert (first_path.stat().st_size, first_path.read_text(encoding="utf-8")) == before
    assert len(load_snapshots(deployment_data_dir, AGENT, CANDIDATE)) == 1


def test_changed_evidence_creates_new_snapshot_and_keeps_history(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """C3：证据变化 → 新快照；旧快照保留（只增不改）。"""
    case = deployment_evidence_matrix["all_satisfied"]["kwargs"]
    bundle = collect_evidence(cfg=deployment_config, **case)
    _, first_path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    original = first_path.read_text(encoding="utf-8")

    changed = dict(case)
    changed["reward_compare"] = reward_compare_payload(
        0.95, 0.55, source="replay/pools/pool-b.json"
    )
    changed_bundle = collect_evidence(cfg=deployment_config, **changed)
    verdict, second_path = gate_and_record(changed_bundle, deployment_config, deployment_data_dir)
    assert verdict.decision is GateDecision.ELIGIBLE
    assert second_path != first_path
    assert first_path.read_text(encoding="utf-8") == original
    snapshots = load_snapshots(deployment_data_dir, AGENT, CANDIDATE)
    assert len(snapshots) == 2
    assert {item["fingerprint"] for item in snapshots} == {
        fingerprint_of(bundle, gate(bundle, deployment_config)),
        fingerprint_of(changed_bundle, verdict),
    }


def test_snapshot_records_interception_decision_too(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """拦截也必须留痕（影子对照与误入率归因的取数来源）。"""
    bundle = _bundle(deployment_config, deployment_evidence_matrix, "drift_unsatisfied")
    verdict, path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    assert verdict.decision is GateDecision.BLOCKED
    payload = json.loads(path.read_text(encoding="utf-8"))
    states = {item["name"]: item["state"] for item in payload["verdict"]["requirements"]}
    assert states["drift_verdict"] == "unsatisfied"
    assert payload["verdict"]["reason"]


def test_snapshots_are_partitioned_per_agent(
    deployment_data_dir, deployment_evidence_matrix, deployment_config
):
    """快照按 Agent 分目录（多 Agent 部署评估互不串档）。"""
    bundle = _bundle(deployment_config, deployment_evidence_matrix, "all_satisfied")
    _, path = gate_and_record(bundle, deployment_config, deployment_data_dir)
    assert load_snapshots(deployment_data_dir, "promo", CANDIDATE) == []
    snapshot_ids = [
        item["snapshot_id"] for item in load_snapshots(deployment_data_dir, AGENT, CANDIDATE)
    ]
    assert snapshot_ids == [json.loads(path.read_text(encoding="utf-8"))["snapshot_id"]]


def test_load_snapshots_rejects_missing_agent_dir(deployment_data_dir):
    """无该 Agent 快照 → 空列表（不报错，也不伪造证据）。"""
    assert load_snapshots(deployment_data_dir, "sound", "cand-x") == []


def test_gate_rejects_malformed_bundle(deployment_config):
    with pytest.raises(DeploymentEvidenceError, match="EvidenceBundle"):
        gate({"not": "a bundle"}, deployment_config)
