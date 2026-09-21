"""部署 CLI 单测（功能 014 US2 / T1416，ops/deploy.py）。

只覆盖本批交付的可演示行为：`mode`（查看/切换，门禁拒绝如实回报）、`evaluate`
（manual/shadow 行为；auto 未落地时显式报错退出码 1）、`shadow-report`（报告 + 机检重算）。
风格对齐既有 CLI：JSON 输出 + 退出码（0 成功 / 1 执行失败 / 2 用法错误）。
"""

import json

import pytest

from ops.deploy import main

T0 = "2026-09-21T00:00:00+00:00"


def _run(capsys):
    code = main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else {}
    return code, payload


@pytest.fixture()
def _argv(monkeypatch):
    def _set(argv):
        monkeypatch.setattr("sys.argv", ["deploy.py", *argv])

    return _set


def test_mode_show_reports_state_and_gap(_argv, capsys, deployment_pointer_files):
    pointer = deployment_pointer_files()
    _argv(["mode", "--config", str(pointer["config"]), "--data-dir", str(pointer["data_dir"])])
    code, payload = _run(capsys)
    assert code == 0
    assert payload["current"] == "manual"
    assert payload["recalibration_required"] is False
    assert "缺口" in payload["gap"]
    assert payload["state_path"].endswith("mode.json")


def test_mode_set_requires_by_and_reason(_argv, capsys, deployment_pointer_files):
    pointer = deployment_pointer_files()
    _argv(
        [
            "mode",
            "--set",
            "shadow",
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 2
    assert "--reason" in payload["error"]
    assert not (pointer["data_dir"] / "mode.json").exists()


def test_mode_set_manual_to_auto_is_rejected_with_reason(_argv, capsys, deployment_pointer_files):
    """门禁在 core 内判定，CLI 只如实回报（退出码 1，模式不变）。"""
    pointer = deployment_pointer_files()
    _argv(
        [
            "mode",
            "--set",
            "auto",
            "--by",
            "ops",
            "--reason",
            "直接开自动",
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 1
    assert "manual" in payload["error"]
    assert not (pointer["data_dir"] / "mode.json").exists()


def test_evaluate_manual_records_snapshot_only(_argv, capsys, deployment_pointer_files, tmp_path):
    pointer = deployment_pointer_files()
    unbiasedness = tmp_path / "unbiasedness.json"
    unbiasedness.write_text(json.dumps({"verdict": "pass", "tau": 0.82}), encoding="utf-8")
    _argv(
        [
            "evaluate",
            "--agent",
            "screenplay",
            "--candidate",
            "fa6b7bca77ed",
            "--unbiasedness",
            str(unbiasedness),
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["mode"] == "manual"
    assert payload["action"] == "snapshot_only"
    # screenplay 属禁止自动进化名单（009）：优先级最高的判定落在快照里
    assert payload["decision"] == "forbidden_agent"
    assert payload["snapshot_path"]


def test_evaluate_rejects_malformed_evidence_file(
    _argv, capsys, deployment_pointer_files, tmp_path
):
    pointer = deployment_pointer_files()
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    _argv(
        [
            "evaluate",
            "--agent",
            "visual",
            "--candidate",
            "cand-001",
            "--unbiasedness",
            str(bad),
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 2
    assert "JSON 对象" in payload["error"]


def test_evaluate_auto_mode_deploys_and_updates_pointer(
    _argv, capsys, deployment_pointer_files, tmp_path, deployment_config, deployment_drift_registry
):
    """auto + eligible：退出码 0，指针更新 + 部署事件留痕 + action=deployed（T1422 已落地）。"""
    from dataclasses import replace

    from core.deployment import mode
    from core.deployment.config import ShadowConfig

    pointer = deployment_pointer_files()
    from core.yaml_edit import upsert_section_entries

    pointer["config"].write_text(
        upsert_section_entries(
            pointer["config"].read_text(encoding="utf-8"),
            ("deployment", "visual"),
            {"current_policy_version": "dep-000"},
        ),
        encoding="utf-8",
    )
    cfg = replace(deployment_config, shadow=ShadowConfig(min_days=0, min_candidates=0))
    mode.set_mode("shadow", by="ops", reason="开影子", cfg=cfg, data_dir=pointer["data_dir"], at=T0)
    mode.set_mode("auto", by="ops", reason="影子达标", cfg=cfg, data_dir=pointer["data_dir"], at=T0)
    unbiasedness = tmp_path / "attestation.json"
    unbiasedness.write_text(json.dumps({"verdict": "pass", "tau": 0.82}), encoding="utf-8")
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps({"cand-001": 0.8, "dep-000": 0.5}), encoding="utf-8")
    _argv(
        [
            "evaluate",
            "--agent",
            "visual",
            "--candidate",
            "cand-001",
            "--unbiasedness",
            str(unbiasedness),
            "--validation",
            str(validation),
            "--judges",
            "judge.cinematic@1.0.0",
            "--drift-dir",
            str(deployment_drift_registry("normal").data_dir),
            "--reward-candidate",
            "0.62",
            "--reward-deployed",
            "0.55",
            "--reward-source",
            "replay/pools/pool-a.json",
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["action"] == "deployed"
    assert payload["decision"] == "eligible"
    from core.deployment.auto_deploy import read_pointer

    assert read_pointer(pointer["config"], "visual") == "cand-001"
    snapshots = list((pointer["data_dir"] / "evidence" / "visual").glob("cand-001.*.json"))
    assert len(snapshots) == 1
    assert (pointer["data_dir"] / "deploys").glob("*-visual.json")


def test_shadow_report_prints_report_and_recomputed_rate(
    _argv, capsys, deployment_pointer_files, tmp_path
):
    """shadow-report：报告字段齐全 + 误入率机检重算（SC-007）。"""
    from core.deployment import shadow

    pointer = deployment_pointer_files()
    shadow.record_shadow_event(
        "visual",
        "cand-001",
        "eligible",
        data_dir=pointer["data_dir"],
        period="2026-W39",
        reason="判定 eligible",
        human_decision="reject",
        at=T0,
    )
    shadow.record_shadow_event(
        "visual",
        "cand-002",
        "blocked",
        data_dir=pointer["data_dir"],
        period="2026-W39",
        reason="要件不满足",
        human_decision="adopt",
        at=T0,
    )
    _argv(
        [
            "shadow-report",
            "--period",
            "2026-W39",
            "--agent",
            "visual",
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    report = payload["report"]
    assert report["candidate_count"] == 2
    assert report["passes"] == 1 and report["blocks"] == 1
    assert report["misadmission_numerator"] == 1
    assert report["misadmission_denominator"] == 1
    assert payload["recomputed_misadmission"]["rate"] == pytest.approx(report["misadmission_rate"])
    assert payload["report_path"].endswith("2026-W39.json")


def test_shadow_report_without_events_fails_loudly(_argv, capsys, deployment_pointer_files):
    pointer = deployment_pointer_files()
    _argv(
        [
            "shadow-report",
            "--period",
            "2026-W39",
            "--agent",
            "visual",
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 1
    assert "影子事件" in payload["error"]


# --- US3（T1424）：spot-check / veto / assess-drift 子命令 --------------------


def _to_auto_with_deploy(pointer, deployment_config, *, target_versions=("dep-000",)):
    """把模式推到 auto 并完成一次自动部署；返回（cfg, 部署事件 payload）。"""
    from dataclasses import replace as dc_replace

    from core.deployment import mode
    from core.deployment.auto_deploy import auto_deploy, deploy_events
    from core.deployment.config import ShadowConfig

    cfg = dc_replace(deployment_config, shadow=ShadowConfig(min_days=0, min_candidates=0))
    for target in target_versions:
        (pointer["data_dir"].parent / f"artifact-{target}.py").write_text("", encoding="utf-8")
    mode.set_mode("shadow", by="ops", reason="开影子", cfg=cfg, data_dir=pointer["data_dir"], at=T0)
    mode.set_mode("auto", by="ops", reason="影子达标", cfg=cfg, data_dir=pointer["data_dir"], at=T0)
    snapshot = pointer["data_dir"].parent / "snap.json"
    snapshot.write_text("{}", encoding="utf-8")
    auto_deploy(
        agent_id=pointer["agent_id"],
        candidate_version="cand-000",
        snapshot_path=snapshot,
        from_version=pointer["current_version"],
        cfg=cfg,
        data_dir=pointer["data_dir"],
        config_path=pointer["config"],
        at=T0,
    )
    return cfg, deploy_events(pointer["data_dir"], pointer["agent_id"])[0]


def test_spot_check_creates_task_and_lists_pending(
    _argv, capsys, deployment_pointer_files, deployment_config
):
    pointer = deployment_pointer_files("visual", current_version="dep-000")
    _, event = _to_auto_with_deploy(pointer, deployment_config)
    _argv(
        [
            "spot-check",
            "--deploy-event",
            event["_path"],
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["task"]["trigger"] == "first_n"
    assert payload["task"]["seq"] == 1
    assert payload["task"]["conclusion"] == "pending"

    _argv(
        [
            "spot-check",
            "--list",
            "--agent",
            "visual",
            "--pending-alert-days",
            "0",
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["pending_count"] == 1
    assert payload["pending"][0]["candidate_version"] == "cand-000"
    assert payload["stale"]
    assert "不自动视为通过" in payload["alert"]


def test_veto_cli_rolls_back_and_recovers_manual(
    _argv, capsys, tmp_path, deployment_pointer_files, deployment_config, deployment_history_root
):
    """veto：三件事同时生效（指针回滚 + 模式 manual + 重标定标记），退出码 0。"""
    from core.deployment import spot_check
    from core.deployment.auto_deploy import read_pointer

    pointer = deployment_pointer_files("visual", current_version="dep-000")
    cfg, event = _to_auto_with_deploy(pointer, deployment_config)
    history_root = deployment_history_root("visual", "dep-000")
    task = spot_check.open_spot_check(event["_path"], data_dir=pointer["data_dir"], cfg=cfg, at=T0)
    record_path = spot_check.record_path_from(task, pointer["data_dir"])
    _argv(
        [
            "veto",
            "--record",
            str(record_path),
            "--by",
            "reviewer",
            "--reason",
            "产出质量不达线",
            "--history-root",
            str(history_root),
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["rollback"]["trigger"] == "spot_check_veto"
    assert payload["rollback"]["to_version"] == "dep-000"
    assert read_pointer(pointer["config"], "visual") == "dep-000"
    import json as _json

    state = _json.loads((pointer["data_dir"] / "mode.json").read_text(encoding="utf-8"))
    assert state["current"] == "manual"
    assert state["recalibration_required"] is True


def test_veto_cli_reports_missing_target_with_alert(
    _argv, capsys, tmp_path, deployment_pointer_files, deployment_config
):
    """回滚目标工件缺失 → 退出码 1 且告警明确（模式已回 manual，不停留在不确定状态）。"""
    from core.deployment import spot_check

    pointer = deployment_pointer_files("visual", current_version="dep-000")
    cfg, event = _to_auto_with_deploy(pointer, deployment_config)
    task = spot_check.open_spot_check(event["_path"], data_dir=pointer["data_dir"], cfg=cfg, at=T0)
    _argv(
        [
            "veto",
            "--record",
            str(spot_check.record_path_from(task, pointer["data_dir"])),
            "--by",
            "reviewer",
            "--reason",
            "抽检否决",
            "--history-root",
            str(tmp_path / "empty-history"),
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 1
    assert payload["alert"] is True
    assert "回滚目标版本工件缺失" in payload["error"]


def test_assess_drift_cli_records_assessment_without_pointer_change(
    _argv, capsys, deployment_pointer_files, deployment_config, deployment_drift_registry
):
    """assess-drift：suspect → 评估记录落盘、指针不动；无漂移 → 无需评估。"""
    from core.deployment.auto_deploy import read_pointer

    pointer = deployment_pointer_files("visual", current_version="dep-000")
    _to_auto_with_deploy(pointer, deployment_config)
    registry = deployment_drift_registry("suspect")
    _argv(
        [
            "assess-drift",
            "--agent",
            "visual",
            "--drift-dir",
            str(registry.data_dir),
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["assessments"]
    assert payload["assessments"][0]["trigger"] == "drift_assessment"
    assert payload["pointer_unchanged"] is True
    assert read_pointer(pointer["config"], "visual") == "cand-000"

    normal = deployment_drift_registry("normal")
    _argv(
        [
            "assess-drift",
            "--agent",
            "visual",
            "--drift-dir",
            str(normal.data_dir),
            "--config",
            str(pointer["config"]),
            "--data-dir",
            str(pointer["data_dir"]),
        ]
    )
    code, payload = _run(capsys)
    assert code == 0
    assert payload["assessments"] == []
    assert "无需回滚评估" in payload["note"]
