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


def test_evaluate_auto_is_not_implemented_yet_but_snapshot_survives(
    _argv, capsys, deployment_pointer_files, tmp_path, deployment_config, deployment_drift_registry
):
    """auto 期放行后部署未落地 → 退出码 1 且明确指出，判定快照仍已落盘（不丢留痕）。"""
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
    assert code == 1
    assert "T1422" in payload["error"]
    snapshots = list((pointer["data_dir"] / "evidence" / "visual").glob("cand-001.*.json"))
    assert len(snapshots) == 1


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
