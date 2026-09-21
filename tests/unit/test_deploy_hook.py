"""做梦收口接线单测（功能 014 US2 / T1416，契约 C6「一处接线」）。

- `run_dream_round(..., deploy_hook=...)`：**轮次收口后**调用一次钩子，摘要记入
  `diagnostics["deployment"]`；钩子抛异常时如实记录（不阻断做梦主流程、不静默吞掉）；
- `dreaming.deploy_hook.deployment_hook(...)`：把胜出者交给唯一入口 `evaluate_candidate`，
  模式从 `deployment/mode.json` 读（manual/shadow 不部署）；无胜出者的轮次如实跳过；
- 指针读取只认 configs（与 009 部署指针同源），无指针 → 无现部署版本（判拦截）。
"""

import json

import pytest

from core.deployment import mode
from core.deployment.models import DeployMode
from dreaming.deploy_hook import current_policy_version, deployment_hook
from dreaming.pipeline import DreamRound

T0 = "2026-09-21T00:00:00+00:00"


def _round(winner="cand-001", *, status="completed"):
    return DreamRound(
        round_id="2026-09-21-001",
        agent_id="visual",
        champion_version="dep-000",
        digest={},
        digest_sha="d" * 64,
        candidates=[],
        winner_version=winner,
        status=status,
    )


class _ScriptGenerator:
    """候选生成器桩（不调 LLM）：返回给定源码列表。"""

    def __init__(self, sources):
        self._sources = sources

    def generate(self, champion_source, digest, m):
        return list(self._sources)[:m]


def _pool(multi_tree_pool):
    from core.replay.pool import SimulatorPool

    trees, store = multi_tree_pool(3)
    pool = SimulatorPool(store)
    for tree in trees:
        pool.add_tree(tree)
    return pool


def _run_round(champion, pool, dream_config, tmp_path, **kwargs):
    from dreaming.pipeline import in_process_replay, run_dream_round

    return run_dream_round(
        "visual",
        champion,
        _ScriptGenerator([champion]),
        pool,
        None,  # 网关（生成器桩不用 LLM）
        dream_config,
        replay_fn=in_process_replay,
        history_root=tmp_path,
        m=1,
        **kwargs,
    )


def test_round_close_records_deployment_summary(
    champion_source, multi_tree_pool, dream_config, tmp_path, deployment_pointer_files
):
    """真实管线：收口后调用钩子一次，摘要写进轮次报告（含落盘文件）。"""
    pointer = deployment_pointer_files()
    hook = deployment_hook(data_dir=pointer["data_dir"], config_path=pointer["config"], at=T0)
    result = _run_round(
        champion_source(), _pool(multi_tree_pool), dream_config, tmp_path, deploy_hook=hook
    )
    assert result.status == "completed"
    summary = result.diagnostics["deployment"]
    assert summary["status"] == "evaluated"
    assert summary["round_id"] == result.round_id
    assert summary["action"] == "snapshot_only"
    payload = json.loads((tmp_path / "visual" / f"{result.round_id}.json").read_text("utf-8"))
    assert payload["diagnostics"]["deployment"]["round_id"] == result.round_id


def test_deployment_hook_evaluates_winner_in_manual_mode(
    deployment_data_dir, deployment_pointer_files
):
    """manual（默认）：只落证据快照；摘要含轮次号与判定（证据缺失 → 拦截，如实记录）。"""
    pointer = deployment_pointer_files()
    hook = deployment_hook(
        data_dir=pointer["data_dir"],
        config_path=pointer["config"],
        at=T0,
    )
    summary = hook(_round())
    assert summary["status"] == "evaluated"
    assert summary["round_id"] == "2026-09-21-001"
    assert summary["mode"] == "manual"
    assert summary["action"] == "snapshot_only"
    # 未注入证据 → 前置无偏性缺失 → 证据不足（缺证据即拦截，不推测放行）
    assert summary["decision"] == "insufficient_evidence"
    assert summary["reason"]
    assert summary["snapshot_path"].endswith(".json")


def test_deployment_hook_skips_round_without_winner(deployment_data_dir, deployment_pointer_files):
    """无胜出者的轮次（全灭/全 UNKNOWN）不评估：如实记为 skipped，不伪造判定。"""
    pointer = deployment_pointer_files()
    hook = deployment_hook(data_dir=pointer["data_dir"], config_path=pointer["config"], at=T0)
    summary = hook(_round(winner=None, status="failed_all_unknown"))
    assert summary["status"] == "skipped"
    assert "failed_all_unknown" in summary["note"]
    assert not list((pointer["data_dir"] / "evidence").rglob("*.json"))


def test_deployment_hook_records_shadow_event_and_keeps_pointer(
    deployment_data_dir, deployment_pointer_files, deployment_config, deployment_drift_registry
):
    """shadow 期收口评估：影子事件落痕 + 指针逐字节不变（影子不部署）。"""
    pointer = deployment_pointer_files()
    # 用既有定点改写补一条 visual 指针（与 009 部署指针同源；本测试不牵动仓库配置）
    from core.yaml_edit import upsert_section_entries

    config_text = pointer["config"].read_text(encoding="utf-8")
    pointer["config"].write_text(
        upsert_section_entries(
            config_text, ("deployment", "visual"), {"current_policy_version": "dep-000"}
        ),
        encoding="utf-8",
    )
    before = pointer["config"].read_bytes()
    mode.set_mode(
        DeployMode.SHADOW,
        by="ops",
        reason="开影子期",
        cfg=deployment_config,
        data_dir=pointer["data_dir"],
        at=T0,
    )
    hook = deployment_hook(
        data_dir=pointer["data_dir"],
        config_path=pointer["config"],
        cfg=deployment_config,
        unbiasedness={"verdict": "pass", "tau": 0.82},
        reward_compare={
            "candidate": 0.62,
            "deployed": 0.55,
            "source": "replay/pools/pool-a.json",
        },
        validation_rewards={"cand-001": 0.8, "dep-000": 0.5},
        judge_keys=("judge.cinematic@1.0.0",),
        drift_registry=deployment_drift_registry("normal"),
        at=T0,
    )
    summary = hook(_round())
    assert summary["mode"] == "shadow"
    assert summary["action"] == "shadow_recorded"
    assert summary["decision"] == "eligible"
    assert summary["shadow_event"]["would_allow"] is True
    assert pointer["config"].read_bytes() == before
    assert not list((pointer["data_dir"] / "deploys").glob("*.json"))


def test_current_policy_version_reads_pointer(deployment_pointer_files):
    """指针读取与 009 同源；未知 Agent → None（不编造现部署版本）。"""
    pointer = deployment_pointer_files()
    assert current_policy_version("screenplay", pointer["config"]) == pointer["current_version"]
    assert current_policy_version("visual", pointer["config"]) is None


def test_hook_failure_is_recorded_and_does_not_break_round(
    champion_source, multi_tree_pool, dream_config, tmp_path
):
    """钩子失败如实记录且不阻断做梦：回放成果与轮次报告都不被评估故障吞掉。"""

    def bad_hook(round_):
        raise RuntimeError("模拟部署评估故障")

    result = _run_round(
        champion_source(), _pool(multi_tree_pool), dream_config, tmp_path, deploy_hook=bad_hook
    )
    summary = result.diagnostics["deployment"]
    assert summary["status"] == "error"
    assert "模拟部署评估故障" in summary["note"]
    assert result.status == "completed"
    assert result.winner_version
    assert (tmp_path / "visual" / f"{result.round_id}.json").is_file()


def test_round_without_hook_is_unchanged(champion_source, multi_tree_pool, dream_config, tmp_path):
    """默认不接线（deploy_hook=None）：轮次诊断里不出现 deployment（现状不变）。"""
    result = _run_round(champion_source(), _pool(multi_tree_pool), dream_config, tmp_path)
    assert "deployment" not in result.diagnostics


def test_pipeline_signature_accepts_deploy_hook():
    """接线点唯一：`run_dream_round` 显式接受 deploy_hook（默认 None = 现状不变）。"""
    import inspect

    from dreaming.pipeline import run_dream_round

    signature = inspect.signature(run_dream_round)
    assert "deploy_hook" in signature.parameters
    assert signature.parameters["deploy_hook"].default is None


@pytest.mark.parametrize("status", ["failed_all_rejected", "failed_all_unknown"])
def test_hook_skipped_statuses_are_not_evaluated(
    status, deployment_data_dir, deployment_pointer_files
):
    """失败轮次（全灭/全 UNKNOWN）无胜出者 → 跳过评估（无候选可评估，不制造判定）。"""
    pointer = deployment_pointer_files()
    hook = deployment_hook(data_dir=pointer["data_dir"], config_path=pointer["config"], at=T0)
    assert hook(_round(winner=None, status=status))["status"] == "skipped"
