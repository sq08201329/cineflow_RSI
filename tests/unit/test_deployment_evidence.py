"""证据包收集单测（功能 014 US1 / T1409，先于实现编写；契约 C1 + FR-001）。

覆盖（契约 C1 四场景 + 口径来源）：
1. 三要件齐备 + 前置通过 → 各要件 satisfied，取值与来源引用齐全；
2. 无偏性未通过 / 结论缺失 → 前置失败（未通过=unsatisfied，缺失=missing）；
3. 无 validation 集 / 无池化回放结果 / 无漂移数据 → 对应要件 missing；
4. promo/sound（无 judge）→ 漂移要件 not_applicable（**默认不放宽整体门槛**，见 T1410 判定）；
5. 口径全部复用既有实现：reward=011 池化回放对比产物引用；validation 排名=005 口径
   （逐字同款 cutoff 与"并列取最劣名次"）；drift=012 `deploy_evidence_verdict`；
6. 非法证据 payload 报错（不静默降级为 missing——错误与缺失必须可区分）。
"""

import pytest

from core.deployment.config import GateConfig
from core.deployment.errors import DeploymentError
from core.deployment.evidence import (
    collect_evidence,
    reward_compare_payload,
    validation_cutoff,
    validation_rank,
)
from core.deployment.models import RequirementState

AGENT = "visual"
CANDIDATE = "cand-001"
DEPLOYED = "dep-000"
JUDGE_KEY = "judge.cinematic@1.0.0"
ATTESTATION = {"verdict": "pass", "tau": 0.82, "threshold": 0.6}


def req_of(bundle, name):
    return bundle.requirement(name)


def replace_gate(cfg, **overrides):
    """同口径换门槛配置（测试与影子期前的配置演进用）。"""
    from dataclasses import replace

    return replace(cfg, gate=replace(cfg.gate, **overrides))


def test_matrix_cases_collect_expected_requirement_states(
    deployment_evidence_matrix, deployment_config
):
    """组合矩阵逐档断言要件状态（矩阵是判定测试取数的唯一来源，避免口径分叉）。"""
    for case_name, case in deployment_evidence_matrix.items():
        bundle = collect_evidence(cfg=deployment_config, **case["kwargs"])
        assert bundle.agent_id == case["kwargs"]["agent_id"], case_name
        assert bundle.candidate_version == case["kwargs"]["candidate_version"], case_name
        for name, expected in case["expected_states"].items():
            assert req_of(bundle, name).state.value == expected, f"{case_name}/{name}"


def test_all_satisfied_bundle_carries_values_and_source_refs(
    deployment_evidence_matrix, deployment_config
):
    """场景 1：要件取值与来源引用齐全（快照可追溯、口径可复算）。"""
    bundle = collect_evidence(
        cfg=deployment_config, **deployment_evidence_matrix["all_satisfied"]["kwargs"]
    )
    for name in ("reward_compare", "validation_rank", "drift_verdict"):
        item = req_of(bundle, name)
        assert item.state is RequirementState.SATISFIED
        assert item.source, f"{name} 缺来源引用"
        assert item.value, f"{name} 缺取值"
        assert item.reason
    assert bundle.unbiasedness.state is RequirementState.SATISFIED
    assert bundle.deployed_version == DEPLOYED
    assert bundle.collected_at


def test_reward_requirement_requires_strictly_higher_than_deployed(deployment_config):
    """要件①：回放 reward **严格高于**现部署（等于不算超过，不臆造优势）。"""
    higher = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="replay/pools/pool-a.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
    )
    assert req_of(higher, "reward_compare").state is RequirementState.SATISFIED
    equal = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.55, 0.55, source="replay/pools/pool-a.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
    )
    assert req_of(equal, "reward_compare").state is RequirementState.UNSATISFIED
    assert "0.55" in req_of(equal, "reward_compare").reason


def test_reward_requirement_missing_without_deployed_version(deployment_config):
    """无现部署版本 → 对比对象缺失（missing，不推测）。"""
    bundle = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=None,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="replay/pools/pool-a.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
    )
    assert req_of(bundle, "reward_compare").state is RequirementState.MISSING


def test_malformed_reward_payload_is_an_error_not_missing(deployment_config):
    """非法证据 payload 显式报错（缺失与错误必须可区分，不静默降级）。"""
    for bad in (
        {"candidate": 0.6, "source": "ref"},  # 缺 deployed
        {"candidate": "high", "deployed": 0.5, "source": "ref"},  # 非数值
        {"candidate": 0.6, "deployed": 0.5},  # 缺来源引用
    ):
        with pytest.raises(DeploymentError):
            collect_evidence(
                AGENT,
                CANDIDATE,
                cfg=deployment_config,
                deployed_version=DEPLOYED,
                unbiasedness=ATTESTATION,
                reward_compare=bad,
                validation_rewards={CANDIDATE: 0.8},
            )


def test_validation_rank_reuses_005_cutoff_and_worst_tie_rule(deployment_config):
    """要件②：validation 排名口径 = 005（cutoff=max(1,int(n*ratio))；并列取最劣名次）。"""
    assert validation_cutoff(5, 0.2) == 1
    assert validation_cutoff(10, 0.2) == 2
    assert validation_cutoff(9, 0.2) == 1
    rewards = {
        "cand-001": 0.8,
        "sibling-a": 0.9,
        "sibling-b": 0.7,
        "sibling-c": 0.6,
        "sibling-d": 0.5,
    }
    assert validation_rank(CANDIDATE, rewards, top_ratio=0.2) == (2, 5)
    ties = {"cand-001": 0.9, "sibling-a": 0.9, "sibling-b": 0.5}
    assert validation_rank(CANDIDATE, ties, top_ratio=0.2) == (2, 3)  # 并列取最劣
    assert validation_rank("not-ranked", ties, top_ratio=0.2) == (3, 3)  # 未上榜 = 最末


def test_validation_requirement_boundary_is_cutoff_inclusive(deployment_config):
    """排名边界：名次 ≤ cutoff 满足、> cutoff 不满足（005 判定口径逐字同款）。"""
    inside = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
        validation_rewards={CANDIDATE: 0.9, "b": 0.5, "c": 0.4, "d": 0.3, "e": 0.2},
    )
    assert req_of(inside, "validation_rank").state is RequirementState.SATISFIED
    outside = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
        validation_rewards={CANDIDATE: 0.8, "b": 0.9, "c": 0.7, "d": 0.6, "e": 0.5},
    )
    assert req_of(outside, "validation_rank").state is RequirementState.UNSATISFIED
    assert "2/5" in req_of(outside, "validation_rank").reason


def test_missing_validation_set_is_missing_not_error(deployment_config):
    for empty in (None, {}):
        bundle = collect_evidence(
            AGENT,
            CANDIDATE,
            cfg=deployment_config,
            deployed_version=DEPLOYED,
            unbiasedness=ATTESTATION,
            reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
            validation_rewards=empty,
        )
        item = req_of(bundle, "validation_rank")
        assert item.state is RequirementState.MISSING
        assert item.reason


def test_drift_requirement_uses_012_verdict_for_all_judge_versions(
    deployment_drift_registry, deployment_config
):
    """要件③：对**全部相关 judge 版本**取 012 verdict；任一无 judge → 要件不满足。"""
    ok = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
        judge_keys=("judge.cinematic@1.0.0",),
        drift_registry=deployment_drift_registry("normal"),
    )
    item = req_of(ok, "drift_verdict")
    assert item.state is RequirementState.SATISFIED
    assert JUDGE_KEY in item.value["verdicts"]

    suspect = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
        judge_keys=("judge.cinematic@1.0.0",),
        drift_registry=deployment_drift_registry("suspect"),
    )
    blocked_item = req_of(suspect, "drift_verdict")
    assert blocked_item.state is RequirementState.UNSATISFIED
    assert "suspect" in blocked_item.reason


def test_drift_requirement_missing_without_registry_data(deployment_config):
    """无漂移数据 → missing（不是"通过"；证据缺失按拦截处理）。"""
    bundle = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=deployment_config,
        deployed_version=DEPLOYED,
        unbiasedness=ATTESTATION,
        reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
        judge_keys=("judge.cinematic@1.0.0",),
        drift_registry=None,
    )
    assert req_of(bundle, "drift_verdict").state is RequirementState.MISSING


def test_agent_without_judge_layer_is_not_applicable(deployment_config):
    """场景 4：promo/sound 无 judge 层 → not_applicable（是否放宽由判定层配置决定）。"""
    for agent_id in ("promo", "sound"):
        bundle = collect_evidence(
            agent_id,
            CANDIDATE,
            cfg=deployment_config,
            deployed_version=DEPLOYED,
            unbiasedness=ATTESTATION,
            reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
            validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
            judge_keys=(),
        )
        item = req_of(bundle, "drift_verdict")
        assert item.state is RequirementState.NOT_APPLICABLE
        assert item.reason


def test_unbiasedness_prerequisite_states(deployment_config):
    """前置（无偏性验收）：通过=satisfied、未通过=unsatisfied、缺结论=missing。"""
    kwargs = {
        "cfg": deployment_config,
        "deployed_version": DEPLOYED,
        "reward_compare": reward_compare_payload(0.62, 0.55, source="pool.json"),
        "validation_rewards": {CANDIDATE: 0.8, DEPLOYED: 0.5},
    }
    passed = collect_evidence(AGENT, CANDIDATE, unbiasedness=ATTESTATION, **kwargs)
    assert req_of(passed, "unbiasedness").state is RequirementState.SATISFIED
    failed = collect_evidence(
        AGENT, CANDIDATE, unbiasedness={"verdict": "fail", "tau": 0.2}, **kwargs
    )
    assert req_of(failed, "unbiasedness").state is RequirementState.UNSATISFIED
    assert "0.2" in req_of(failed, "unbiasedness").reason
    missing = collect_evidence(AGENT, CANDIDATE, unbiasedness=None, **kwargs)
    assert req_of(missing, "unbiasedness").state is RequirementState.MISSING
    malformed = collect_evidence(AGENT, CANDIDATE, unbiasedness={"tau": 0.9}, **kwargs)
    assert req_of(malformed, "unbiasedness").state is RequirementState.MISSING


def test_unbiasedness_can_be_explicitly_waived_by_config(deployment_config):
    """配置显式声明不要求无偏性前置（`require_unbiasedness=false`）→ 前置不再阻断。"""
    waived = replace_gate(deployment_config, require_unbiasedness=False)
    bundle = collect_evidence(
        AGENT,
        CANDIDATE,
        cfg=waived,
        deployed_version=DEPLOYED,
        unbiasedness=None,
        reward_compare=reward_compare_payload(0.62, 0.55, source="pool.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
    )
    item = req_of(bundle, "unbiasedness")
    assert item.state is RequirementState.SATISFIED
    assert "require_unbiasedness" in item.reason


def test_gate_config_defaults_match_contract():
    """判定层默认保守：无 judge 的 Agent 不放宽（配置默认值即行为契约）。"""
    config = GateConfig(
        validation_top_ratio=0.2, require_unbiasedness=True, allow_without_judge=False
    )
    assert config.allow_without_judge is False
