"""唯一入口 `evaluate_candidate` 单测（功能 014 US2 / T1416，契约 C6 + FR-007）。

入口是**唯一**触发点（做梦轮次收口后一处接线），模式**从 `mode_state(data_dir)` 读真实
状态**（不入参——否则调用方可以"顺手"指定 auto）；三种行为：

- `manual`：只落证据快照（现状不变，指针永不自动切换）；
- `shadow`：落快照 + 影子事件 + 影子候选计数（判定照跑、**指针零变更**）；
- `auto`：eligible → 交 `auto_deploy` 部署（本批未落地，显式 NotImplementedError，
  留待 T1422）；blocked → 落快照 + 拦截记录，**不部署**。

机检：manual/shadow 期部署钩子未被调用、configs 指针逐字节不变、deploys 无留痕。
"""

import json
from dataclasses import replace

import pytest

from core.deployment import mode, shadow
from core.deployment.auto_deploy import DeployAction, EvaluationOutcome, evaluate_candidate
from core.deployment.config import ShadowConfig
from core.deployment.errors import DeploymentError
from core.deployment.evidence import reward_compare_payload
from core.deployment.models import (
    AutoDeployEvent,
    DeployMode,
    GateDecision,
    HumanDecision,
)

AGENT = "visual"
CANDIDATE = "cand-001"
DEPLOYED = "dep-000"
PERIOD = "2026-W39"
T0 = "2026-09-21T00:00:00+00:00"
JUDGE = "judge.cinematic@1.0.0"


def _cfg(deployment_config):
    """宽松影子下限（门禁行为由 T1414 专项覆盖，此处只关心入口分派）。"""
    return replace(deployment_config, shadow=ShadowConfig(min_days=0, min_candidates=0))


def _open_shadow(data_dir, cfg):
    return mode.set_mode("shadow", by="ops", reason="开影子期", cfg=cfg, data_dir=data_dir, at=T0)


def _fill_window(data_dir, cfg):
    for _ in range(20):
        mode.record_shadow_candidate(data_dir, at=T0)
    return mode.set_mode("auto", by="ops", reason="影子期达标", cfg=cfg, data_dir=data_dir, at=T0)


def _judged(registry, **overrides):
    """全要件齐备的证据（含 012 漂移登记）：走「放行」路径的取数——缺 judge 数据时漂移
    要件为 not_applicable 且默认保守，会先被判 blocked。"""
    return _evidence(judge_keys=(JUDGE,), drift_registry=registry, **overrides)


def _evidence(**overrides):
    payload = {
        "deployed_version": DEPLOYED,
        "unbiasedness": {"verdict": "pass", "tau": 0.82, "threshold": 0.6},
        "reward_compare": reward_compare_payload(0.62, 0.55, source="replay/pools/pool-a.json"),
        "validation_rewards": {CANDIDATE: 0.8, DEPLOYED: 0.5},
    }
    payload.update(overrides)
    return payload


class _FakeDeploy:
    """部署钩子替身（US3 的 auto_deploy 落地前的分派机检）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return AutoDeployEvent(
            agent_id=kwargs["agent_id"],
            candidate_version=kwargs["candidate_version"],
            from_version=kwargs["from_version"],
            evidence_snapshot=str(kwargs["snapshot_path"]),
            deployed_at=kwargs["at"] or T0,
            pointer_before=kwargs["from_version"],
            pointer_after=kwargs["candidate_version"],
            reason="门槛 eligible",
        )


def test_manual_mode_only_records_snapshot(
    deployment_data_dir, deployment_config, deployment_drift_registry
):
    """manual（默认现状不变）：只落证据快照，不动影子/部署任何留痕。"""
    hook = _FakeDeploy()
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=_cfg(deployment_config),
        data_dir=deployment_data_dir,
        deploy=hook,
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert isinstance(outcome, EvaluationOutcome)
    assert outcome.mode is DeployMode.MANUAL
    assert outcome.action is DeployAction.SNAPSHOT_ONLY
    assert outcome.verdict.decision is GateDecision.ELIGIBLE
    assert outcome.snapshot_path.is_file()
    assert outcome.shadow_event is None
    assert outcome.deploy_event is None
    assert hook.calls == []
    assert shadow.load_shadow_events(deployment_data_dir) == []
    assert not (deployment_data_dir / "mode.json").exists()


def test_shadow_mode_records_event_and_never_deploys(
    deployment_data_dir, deployment_config, deployment_drift_registry
):
    """shadow：判定照跑 + 影子事件 + 候选计数；**指针零变更**、部署钩子不被调用。"""
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    hook = _FakeDeploy()
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=hook,
        at=T0,
        period=PERIOD,
        **_judged(deployment_drift_registry("normal")),
    )
    assert outcome.mode is DeployMode.SHADOW
    assert outcome.action is DeployAction.SHADOW_RECORDED
    assert outcome.shadow_event is not None
    assert outcome.shadow_event.would_allow is True
    assert outcome.deploy_event is None
    assert hook.calls == []
    state = mode.load_mode_state(deployment_data_dir)
    assert state.current is DeployMode.SHADOW
    assert state.shadow_candidate_count == 1
    events = shadow.load_shadow_events(deployment_data_dir, period=PERIOD)
    assert [item.candidate_version for item in events] == [CANDIDATE]
    assert not list((deployment_data_dir / "deploys").glob("*.json"))


def test_shadow_mode_records_blocked_candidates_with_human_decision(
    deployment_data_dir, deployment_config, deployment_drift_registry
):
    """影子期拦截候选同样留痕，并带同期人工决策 → 差异分类 human_pass_sys_block。"""
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=_FakeDeploy(),
        at=T0,
        period=PERIOD,
        human_decision=HumanDecision.ADOPT,
        deployed_version=DEPLOYED,
        unbiasedness={"verdict": "pass", "tau": 0.82},
        reward_compare=reward_compare_payload(0.55, 0.55, source="replay/pools/pool-a.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
        judge_keys=(JUDGE,),
        drift_registry=deployment_drift_registry("normal"),
    )
    assert outcome.verdict.decision is GateDecision.BLOCKED
    assert outcome.action is DeployAction.SHADOW_RECORDED
    assert outcome.shadow_event.human_decision is HumanDecision.ADOPT
    assert outcome.shadow_event.diff_category.value == "human_pass_sys_block"


def test_shadow_period_defaults_to_iso_week(
    deployment_data_dir, deployment_config, deployment_drift_registry
):
    """周期不入参时按判定时刻推 ISO 周（与 010/012 报表同款口径）。"""
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=_FakeDeploy(),
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert outcome.shadow_event.period == "2026-W39"


def test_auto_mode_blocked_candidate_is_not_deployed(deployment_data_dir, deployment_config):
    """auto + blocked：落快照 + 拦截（部署钩子不被调用——不满足门槛的自动部署次数为 0）。"""
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    _fill_window(deployment_data_dir, cfg)
    hook = _FakeDeploy()
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=hook,
        at=T0,
        unbiasedness=None,  # 前置缺失 → 证据不足
        reward_compare=reward_compare_payload(0.62, 0.55, source="replay/pools/pool-a.json"),
        validation_rewards={CANDIDATE: 0.8, DEPLOYED: 0.5},
    )
    assert outcome.mode is DeployMode.AUTO
    assert outcome.verdict.decision is GateDecision.INSUFFICIENT_EVIDENCE
    assert outcome.action is DeployAction.BLOCKED
    assert hook.calls == []
    assert outcome.snapshot_path.is_file()
    assert shadow.load_shadow_events(deployment_data_dir) == []  # auto 期不产影子事件


def test_auto_mode_eligible_candidate_is_deployed(
    deployment_data_dir, deployment_config, deployment_drift_registry, deployment_pointer_files
):
    """auto + eligible：交 `auto_deploy`（此处注入替身），返回部署事件与 deployed 动作。"""
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    _fill_window(deployment_data_dir, cfg)
    hook = _FakeDeploy()
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        config_path=deployment_pointer_files()["config"],
        deploy=hook,
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert outcome.action is DeployAction.DEPLOYED
    assert outcome.deploy_event is not None
    assert outcome.deploy_event.source == "auto"
    assert outcome.deploy_event.pointer_after == CANDIDATE
    assert len(hook.calls) == 1
    call = hook.calls[0]
    assert call["candidate_version"] == CANDIDATE
    assert call["from_version"] == DEPLOYED
    assert call["snapshot_path"] == outcome.snapshot_path
    assert call["agent_id"] == AGENT
    assert call["config_path"] == deployment_pointer_files()["config"]


def test_auto_mode_requires_config_path_for_pointer_rewrite(
    deployment_data_dir, deployment_config, deployment_drift_registry
):
    """auto 期放行必须先落快照再要求指针改写目标：缺 config_path → 显式报错（不静默跳过部署）。"""
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    _fill_window(deployment_data_dir, cfg)
    with pytest.raises(DeploymentError, match="config_path"):
        evaluate_candidate(
            AGENT,
            CANDIDATE,
            cfg=cfg,
            data_dir=deployment_data_dir,
            deploy=_FakeDeploy(),
            at=T0,
            **_judged(deployment_drift_registry("normal")),
        )


def test_default_deploy_path_is_the_real_auto_deploy(
    deployment_data_dir, deployment_config, deployment_drift_registry, deployment_pointer_files
):
    """不注入钩子时走真实部署实现（T1422 已落地）：指针更新 + 部署事件 + 快照同批留痕。"""
    pointer = deployment_pointer_files(AGENT, current_version=DEPLOYED)
    cfg = _cfg(deployment_config)
    _open_shadow(deployment_data_dir, cfg)
    _fill_window(deployment_data_dir, cfg)
    outcome = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert outcome.action is DeployAction.DEPLOYED
    assert outcome.deploy_event is not None
    from core.deployment.auto_deploy import read_pointer

    assert read_pointer(pointer["config"], AGENT) == CANDIDATE
    # 判定与快照先在（留痕先于部署）
    snapshots = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (deployment_data_dir / "evidence" / AGENT).glob(f"{CANDIDATE}.*.json")
    ]
    assert len(snapshots) == 1
    assert snapshots[0]["verdict"]["decision"] == "eligible"


def test_mode_is_read_from_state_not_from_arguments(
    deployment_data_dir, deployment_config, deployment_drift_registry
):
    """模式不可入参（防调用方"顺手"指定 auto）：同一调用在状态切换后行为随之改变。"""
    cfg = _cfg(deployment_config)
    first = evaluate_candidate(
        AGENT,
        CANDIDATE,
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=_FakeDeploy(),
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert first.action is DeployAction.SNAPSHOT_ONLY
    _open_shadow(deployment_data_dir, cfg)
    second = evaluate_candidate(
        AGENT,
        "cand-002",
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=_FakeDeploy(),
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert second.action is DeployAction.SHADOW_RECORDED
    third = evaluate_candidate(
        AGENT,
        "cand-003",
        cfg=cfg,
        data_dir=deployment_data_dir,
        deploy=_FakeDeploy(),
        at=T0,
        **_judged(deployment_drift_registry("normal")),
    )
    assert third.action is DeployAction.SHADOW_RECORDED
    assert mode.load_mode_state(deployment_data_dir).current is DeployMode.SHADOW


def test_entry_never_touches_pointer_in_manual_and_shadow_modes(
    deployment_data_dir, deployment_config, deployment_pointer_files, deployment_drift_registry
):
    """manual/shadow 全程指针逐字节不变（SC-003 机检：影子期指针变更次数 0）。"""
    pointer = deployment_pointer_files()
    before = pointer["config"].read_bytes()
    cfg = _cfg(deployment_config)
    for candidate in ("cand-a", "cand-b"):
        evaluate_candidate(
            AGENT,
            candidate,
            cfg=cfg,
            data_dir=deployment_data_dir,
            deploy=_FakeDeploy(),
            at=T0,
            **_judged(deployment_drift_registry("normal")),
        )
    _open_shadow(deployment_data_dir, cfg)
    for candidate in ("cand-c", "cand-d"):
        evaluate_candidate(
            AGENT,
            candidate,
            cfg=cfg,
            data_dir=deployment_data_dir,
            deploy=_FakeDeploy(),
            at=T0,
            **_judged(deployment_drift_registry("normal")),
        )
    assert pointer["config"].read_bytes() == before
    assert not list((deployment_data_dir / "deploys").glob("*.json"))
