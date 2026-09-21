"""部署后漂移的回滚评估单测（功能 014 US3 / T1421，契约 C10 + FR-010）。

覆盖：
- 自动部署后 judge 状态转 `suspect` / `confirmed_drift` → 产生**回滚评估记录**
  （`RollbackEvent`，`trigger=drift_assessment`）落 `deployment/rollbacks/`；
- **不自动回滚**：部署指针不动、模式不变、重标定标记不变（按 012 处置留痕流程由人工定夺）；
- 无漂移（normal / false_alarm）→ 不产记录（`None`，不制造噪音证据）；
- 无自动部署留痕 / 无部署指针 → 显式报错（不伪造评估对象）；
- 同刻同内容重复评估 → 幂等（留痕只增不改）。
"""

from dataclasses import replace
from pathlib import Path

import pytest

from core.deployment import mode, spot_check
from core.deployment.auto_deploy import auto_deploy, read_pointer
from core.deployment.errors import DeploymentError
from core.deployment.models import DeployMode, RollbackTrigger

AGENT = "visual"
PREVIOUS = "dep-000"
CANDIDATE = "cand-000"
T0 = "2026-09-21T00:00:00+00:00"
T1 = "2026-09-22T00:00:00+00:00"
JUDGE = "judge.cinematic@1.0.0"


def _auto_mode_pointer(deployment_pointer_files, deployment_config, deployment_data_dir):
    from core.deployment.config import ShadowConfig

    cfg = replace(deployment_config, shadow=ShadowConfig(min_days=0, min_candidates=0))
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = cfg
    mode.set_mode(
        DeployMode.SHADOW, by="ops", reason="开影子", cfg=cfg, data_dir=deployment_data_dir, at=T0
    )
    mode.set_mode(
        DeployMode.AUTO, by="ops", reason="影子达标", cfg=cfg, data_dir=deployment_data_dir, at=T0
    )
    snapshot = deployment_data_dir.parent / "snap.json"
    snapshot.write_text("{}", encoding="utf-8")
    auto_deploy(
        agent_id=AGENT,
        candidate_version=CANDIDATE,
        snapshot_path=snapshot,
        from_version=PREVIOUS,
        cfg=cfg,
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T0,
    )
    pointer["current_version"] = CANDIDATE
    return cfg, pointer


def _status(state: str, *, evaluator_key=JUDGE):
    return {"evaluator_key": evaluator_key, "status": state, "since": T1}


def test_drift_after_auto_deploy_produces_assessment_record(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """C10：suspect → 评估记录落盘（trigger=drift_assessment），指针与模式都不动。"""
    cfg, pointer = _auto_mode_pointer(
        deployment_pointer_files, deployment_config, deployment_data_dir
    )
    record = spot_check.record_drift_assessment(
        AGENT,
        _status("suspect"),
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T1,
    )
    assert record is not None
    assert record.trigger is RollbackTrigger.DRIFT_ASSESSMENT
    assert record.from_version == CANDIDATE
    assert record.to_version == PREVIOUS  # 拟回退目标（记录建议，不执行）
    assert record.recalibration_required is False
    assert "不自动回滚" in record.note
    assert JUDGE in record.reason and "suspect" in record.reason

    path = spot_check.rollback_events(deployment_data_dir, AGENT)[0]["_path"]
    assert Path(path).parent == spot_check.rollbacks_dir(deployment_data_dir)
    # 指针不动、模式不变、标记不变（不自动回滚，按 012 人工处置流程）
    assert read_pointer(pointer["config"], AGENT) == CANDIDATE
    state = mode.load_mode_state(deployment_data_dir)
    assert state.current is DeployMode.AUTO
    assert state.recalibration_required is False


def test_confirmed_drift_also_produces_assessment(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """confirmed_drift（人工已确认）同样产评估记录（处置动作仍由人执行）。"""
    cfg, pointer = _auto_mode_pointer(
        deployment_pointer_files, deployment_config, deployment_data_dir
    )
    record = spot_check.record_drift_assessment(
        AGENT,
        _status("confirmed_drift"),
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T1,
    )
    assert record is not None
    assert "confirmed_drift" in record.reason
    assert read_pointer(pointer["config"], AGENT) == CANDIDATE


def test_non_drift_states_produce_no_record(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """normal / false_alarm → 不产记录（补丁式评估会淹没真正的漂移信号）。"""
    cfg, pointer = _auto_mode_pointer(
        deployment_pointer_files, deployment_config, deployment_data_dir
    )
    for state in ("normal", "false_alarm"):
        assert (
            spot_check.record_drift_assessment(
                AGENT,
                _status(state),
                data_dir=deployment_data_dir,
                config_path=pointer["config"],
                at=T1,
            )
            is None
        )
    assert spot_check.rollback_events(deployment_data_dir, AGENT) == []


def test_assessment_requires_auto_deploy_ledger(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """无自动部署留痕 → 显式报错（漂移评估必须有评估对象，不伪造记录）。"""
    pointer = deployment_pointer_files(AGENT, current_version=CANDIDATE)
    with pytest.raises(DeploymentError, match="部署留痕"):
        spot_check.record_drift_assessment(
            AGENT,
            _status("suspect"),
            data_dir=deployment_data_dir,
            config_path=pointer["config"],
            at=T1,
        )


def test_assessment_requires_deployment_pointer(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """无部署指针 → 显式报错（不知道该评估哪个部署）。"""
    cfg, pointer = _auto_mode_pointer(
        deployment_pointer_files, deployment_config, deployment_data_dir
    )
    from core.yaml_edit import replace_section_entries

    pointer["config"].write_text(
        replace_section_entries(
            pointer["config"].read_text(encoding="utf-8"),
            ("deployment", AGENT),
            {"current_policy_version": ""},
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentError, match="指针"):
        spot_check.record_drift_assessment(
            AGENT,
            _status("suspect"),
            data_dir=deployment_data_dir,
            config_path=pointer["config"],
            at=T1,
        )


def test_assessment_is_idempotent_for_same_state_and_moment(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """同刻同内容重复评估 → 幂等（留痕只增不改）。"""
    cfg, pointer = _auto_mode_pointer(
        deployment_pointer_files, deployment_config, deployment_data_dir
    )
    first = spot_check.record_drift_assessment(
        AGENT,
        _status("suspect"),
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T1,
    )
    second = spot_check.record_drift_assessment(
        AGENT,
        _status("suspect"),
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T1,
    )
    assert first == second
    assert len(spot_check.rollback_events(deployment_data_dir, AGENT)) == 1


def test_assessment_record_is_append_only_for_different_states(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """状态演进（suspect → confirmed_drift）→ 新记录；旧记录逐字节保留。"""
    cfg, pointer = _auto_mode_pointer(
        deployment_pointer_files, deployment_config, deployment_data_dir
    )
    spot_check.record_drift_assessment(
        AGENT,
        _status("suspect"),
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at=T1,
    )
    first_path = spot_check.rollback_events(deployment_data_dir, AGENT)[0]["_path"]
    original = Path(first_path).read_bytes()
    spot_check.record_drift_assessment(
        AGENT,
        _status("confirmed_drift"),
        data_dir=deployment_data_dir,
        config_path=pointer["config"],
        at="2026-09-23T00:00:00+00:00",
    )
    assert len(spot_check.rollback_events(deployment_data_dir, AGENT)) == 2
    assert Path(first_path).read_bytes() == original
