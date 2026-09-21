"""抽检与否决回滚单测（功能 014 US3 / T1420，先于实现编写；契约 C8/C9 + FR-008/FR-009）。

覆盖：
- C8 渐进抽检：前 `first_n` 次自动部署**全量**产复核任务；之后按 `ratio` 比例产任务
  （未抽中即如实返回 None，不产任务）；同一部署事件重复开任务幂等（不新增文件）；
- C8 长期未复核 → 告警（**不自动视为通过**）；
- C9 否决回滚 = **三件事一个逻辑事务**（机检三断言）：①指针回滚到前一部署版本
  ②模式回 `manual` ③写 `recalibration_required` 标记；回滚事件留痕；
- C9 失败路径：回滚目标工件缺失 → 显式报错且模式已回 `manual`（绝不停留在不确定状态）；
- 抽检通过 → 留痕（人/时间/结论）且模式保持 auto；
- 清重标定标记后仍需影子期满足才能再开 auto（不因清标记跳过影子）。
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.deployment import mode, spot_check
from core.deployment.auto_deploy import auto_deploy, read_pointer
from core.deployment.errors import (
    DeploymentError,
    RollbackTargetMissingError,
)
from core.deployment.models import (
    DeployMode,
    RollbackTrigger,
    SpotCheckConclusion,
    SpotCheckTrigger,
)

AGENT = "visual"
DEPLOYED = "dep-001"
PREVIOUS = "dep-000"
T0 = "2026-09-21T00:00:00+00:00"
T1 = "2026-09-22T00:00:00+00:00"


def _at(days: float) -> str:
    return (datetime(2026, 9, 21, tzinfo=UTC) + timedelta(days=days)).isoformat()


def _cfg(deployment_config, *, first_n=5, ratio=0.2):
    from dataclasses import replace

    from core.deployment.config import SpotCheckConfig

    return replace(deployment_config, spot_check=SpotCheckConfig(first_n=first_n, ratio=ratio))


def _deploy(data_dir, pointer, *, candidate, snapshot, at=T0):
    return auto_deploy(
        agent_id=AGENT,
        candidate_version=candidate,
        snapshot_path=snapshot,
        from_version=pointer["current_version"],
        cfg=pointer["cfg"],
        data_dir=data_dir,
        config_path=pointer["config"],
        at=at,
    )


def _snapshot(tmp_path, name):
    path = tmp_path / name
    path.write_text("{}", encoding="utf-8")
    return path


def _prepare_auto(data_dir, cfg, pointer, *, at=T0):
    """把模式推到 auto（影子期下限置 0）并完成一次自动部署（deploys 序号 = 1）。"""
    from dataclasses import replace

    from core.deployment.config import ShadowConfig

    relaxed = replace(cfg, shadow=ShadowConfig(min_days=0, min_candidates=0))
    pointer["cfg"] = relaxed
    mode.set_mode(
        DeployMode.SHADOW, by="ops", reason="开影子", cfg=relaxed, data_dir=data_dir, at=T0
    )
    mode.set_mode(
        DeployMode.AUTO, by="ops", reason="影子达标", cfg=relaxed, data_dir=data_dir, at=T0
    )
    _deploy(
        data_dir,
        pointer,
        candidate="cand-000",
        snapshot=_snapshot(Path(data_dir).parent, "snap-0.json"),
        at=at,
    )
    pointer["current_version"] = "cand-000"


def test_first_n_deployments_all_get_review_tasks(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """C8：前 first_n 次自动部署全量产复核任务（渐进策略：样本少时全量信息价值最高）。"""
    cfg = _cfg(deployment_config)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    events = spot_check.deploy_events(deployment_data_dir, AGENT)
    assert len(events) == 1
    for seq in (1,):
        record = spot_check.open_spot_check(
            events[seq - 1]["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0
        )
        assert record is not None
        assert record.trigger is SpotCheckTrigger.FIRST_N
        assert record.seq == seq
        assert record.conclusion is SpotCheckConclusion.PENDING
    path = spot_check.spot_check_records(deployment_data_dir, AGENT)[0]["_path"]
    assert Path(path).is_file()
    assert Path(path).parent == spot_check.spot_checks_dir(deployment_data_dir)


def test_tasks_after_first_n_follow_ratio(
    deployment_pointer_files, deployment_data_dir, deployment_config, tmp_path
):
    """C8：第 first_n 次之后按比例产任务（1/5 → 每 5 次抽 1 次），未抽中即 None。"""
    cfg = _cfg(deployment_config, first_n=2, ratio=0.2)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer, at=T0)
    for index in range(1, 7):
        _deploy(
            deployment_data_dir,
            pointer,
            candidate=f"cand-{index:03d}",
            snapshot=_snapshot(tmp_path, f"s{index}.json"),
            at=_at(index),
        )
        pointer["current_version"] = f"cand-{index:03d}"
    events = spot_check.deploy_events(deployment_data_dir, AGENT)
    assert len(events) == 7
    triggers = {}
    for event in events:
        record = spot_check.open_spot_check(
            event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0
        )
        triggers[event["candidate_version"]] = None if record is None else record.trigger.value
    assert triggers["cand-000"] == "first_n"
    assert triggers["cand-001"] == "first_n"
    assert triggers["cand-002"] is None
    assert triggers["cand-003"] is None
    assert triggers["cand-004"] is None
    assert triggers["cand-006"] == "ratio"  # seq=7：(7-2) % 5 == 0 → 抽中（每 5 次抽 1 次）
    tasks = spot_check.spot_check_records(deployment_data_dir, AGENT)
    assert len(tasks) == 3


def test_open_spot_check_is_idempotent(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """同一部署事件重复开任务 → 幂等（不新增文件，返回既有任务）。"""
    cfg = _cfg(deployment_config)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    first = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)
    second = spot_check.open_spot_check(
        event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0
    )
    assert first == second
    assert len(spot_check.spot_check_records(deployment_data_dir, AGENT)) == 1


def test_pending_checks_alert_but_never_auto_pass(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """C8：长期未复核 → 告警（不自动通过）——超期待复核任务可被列出，结论仍为 pending。"""
    cfg = _cfg(deployment_config)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    task = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)
    assert spot_check.pending_spot_checks(deployment_data_dir, agent_id=AGENT)
    stale = spot_check.stale_pending_checks(
        deployment_data_dir, agent_id=AGENT, max_age_days=3, at=_at(7)
    )
    assert [item["candidate_version"] for item in stale] == [event["candidate_version"]]
    fresh = spot_check.stale_pending_checks(
        deployment_data_dir, agent_id=AGENT, max_age_days=30, at=_at(7)
    )
    assert fresh == []
    assert task.conclusion is SpotCheckConclusion.PENDING  # 未复核不会被自动置为通过


def test_pass_conclusion_keeps_auto_mode(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """抽检通过 → 留痕（人/时间/理由）且模式保持 auto。"""
    cfg = _cfg(deployment_config)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    task = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)
    record = spot_check.decide_spot_check(
        spot_check.record_path_from(task, deployment_data_dir),
        conclusion=SpotCheckConclusion.PASS,
        by="reviewer",
        reason="产出质量达标",
        data_dir=deployment_data_dir,
        at=T1,
    )
    assert record.conclusion is SpotCheckConclusion.PASS
    assert record.by == "reviewer"
    assert mode.load_mode_state(deployment_data_dir).current is DeployMode.AUTO
    assert spot_check.pending_spot_checks(deployment_data_dir, agent_id=AGENT) == []


def test_human_conclusion_requires_real_author(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """人工结论必须由人签署（by 不得为 system/空）——系统不代签。"""
    cfg = _cfg(deployment_config)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    task = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)
    for by in ("", "system"):
        with pytest.raises(Exception, match="人"):
            spot_check.decide_spot_check(
                spot_check.record_path_from(task, deployment_data_dir),
                conclusion=SpotCheckConclusion.PASS,
                by=by,
                reason="代签",
                data_dir=deployment_data_dir,
                at=T1,
            )


def test_veto_rollback_three_things_at_once(
    deployment_pointer_files,
    deployment_data_dir,
    deployment_config,
    deployment_history_root,
):
    """C9 机检三断言：①指针回滚到前一版本 ②模式回 manual ③重标定标记写入。"""
    cfg = _cfg(deployment_config)
    history_root = deployment_history_root(AGENT, PREVIOUS)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    assert read_pointer(pointer["config"], AGENT) == event["candidate_version"]
    task = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)

    rollback = spot_check.veto_and_rollback(
        spot_check.record_path_from(task, deployment_data_dir),
        by="reviewer",
        reason="产出质量不达线（抽检否决）",
        data_dir=deployment_data_dir,
        cfg=cfg,
        config_path=pointer["config"],
        history_root=history_root,
        at=T1,
    )
    # ① 指针回滚
    assert read_pointer(pointer["config"], AGENT) == PREVIOUS
    assert rollback.to_version == PREVIOUS
    assert rollback.from_version == event["candidate_version"]
    # ② 模式回 manual
    state = mode.load_mode_state(deployment_data_dir)
    assert state.current is DeployMode.MANUAL
    assert rollback.mode_after is DeployMode.MANUAL
    # ③ 重标定标记
    assert state.recalibration_required is True
    assert "抽检否决" in state.recalibration_reason
    assert rollback.recalibration_required is True
    assert rollback.trigger is RollbackTrigger.SPOT_CHECK_VETO
    # 留痕：否决结论 + 回滚事件
    veto_records = [
        item
        for item in spot_check.spot_check_records(deployment_data_dir, AGENT)
        if item["conclusion"] == "veto"
    ]
    assert len(veto_records) == 1
    rollback_path = spot_check.rollback_events(deployment_data_dir, AGENT)[0]["_path"]
    assert json.loads(Path(rollback_path).read_text(encoding="utf-8"))["trigger"] == (
        "spot_check_veto"
    )


def test_veto_rollback_missing_target_errors_but_stays_manual(
    deployment_pointer_files, deployment_data_dir, deployment_config, tmp_path
):
    """C9 场景 2：回滚目标工件缺失 → 显式报错，模式已回 manual（不停留在不确定状态）。"""
    cfg = _cfg(deployment_config)
    history_root = tmp_path / "policies" / "history"  # 空目录：目标工件不存在
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    task = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)

    with pytest.raises(RollbackTargetMissingError) as excinfo:
        spot_check.veto_and_rollback(
            spot_check.record_path_from(task, deployment_data_dir),
            by="reviewer",
            reason="抽检否决",
            data_dir=deployment_data_dir,
            cfg=cfg,
            config_path=pointer["config"],
            history_root=history_root,
            at=T1,
        )
    assert PREVIOUS in str(excinfo.value)
    state = mode.load_mode_state(deployment_data_dir)
    assert state.current is DeployMode.MANUAL  # 已恢复全人工（不确定状态被排除）
    assert state.recalibration_required is True
    assert read_pointer(pointer["config"], AGENT) == event["candidate_version"]  # 指针未改
    assert spot_check.rollback_events(deployment_data_dir, AGENT) == []


def test_reopen_auto_after_veto_requires_shadow_window_again(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """C9 场景 3：清重标定标记后仍需影子期满足（不因清标记跳过影子验证）。"""
    from dataclasses import replace

    from core.deployment.config import ShadowConfig
    from core.deployment.errors import ModeTransitionError

    cfg = replace(deployment_config, shadow=ShadowConfig(min_days=14, min_candidates=20))
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)
    mode.set_recalibration(deployment_data_dir, required=True, reason="抽检否决", at=T1)
    mode.clear_recalibration(deployment_data_dir, by="ops", reason="门槛已重标定", at=_at(3))
    with pytest.raises(ModeTransitionError, match="manual"):
        mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="清标记后直接开自动",
            cfg=cfg,
            data_dir=deployment_data_dir,
            at=_at(4),
        )
    mode.set_mode(
        DeployMode.SHADOW,
        by="ops",
        reason="重跑影子期",
        cfg=cfg,
        data_dir=deployment_data_dir,
        at=_at(4),
    )
    with pytest.raises(ModeTransitionError, match="影子期"):
        mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="影子期未满",
            cfg=cfg,
            data_dir=deployment_data_dir,
            at=_at(5),
        )


def test_veto_requires_a_veto_conclusion_source(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """否决必须来自**未复核的任务**（防重复否决同一部署刷留痕）。"""
    cfg = _cfg(deployment_config)
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    _prepare_auto(deployment_data_dir, cfg, pointer)
    event = spot_check.deploy_events(deployment_data_dir, AGENT)[0]
    task = spot_check.open_spot_check(event["_path"], data_dir=deployment_data_dir, cfg=cfg, at=T0)
    spot_check.decide_spot_check(
        spot_check.record_path_from(task, deployment_data_dir),
        conclusion=SpotCheckConclusion.PASS,
        by="reviewer",
        reason="通过",
        data_dir=deployment_data_dir,
        at=T1,
    )
    with pytest.raises(DeploymentError, match="已复核"):
        spot_check.veto_and_rollback(
            spot_check.record_path_from(task, deployment_data_dir),
            by="reviewer",
            reason="重复否决",
            data_dir=deployment_data_dir,
            cfg=cfg,
            config_path=pointer["config"],
            at=_at(2),
        )
