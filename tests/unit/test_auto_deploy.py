"""自动部署执行单测（功能 014 US3 / T1419，先于实现编写；契约 C7 + FR-007/FR-011）。

覆盖：
- 正常部署：证据快照前置 → 指针**定点改写**（`core/yaml_edit.upsert_section_entries`，
  注释与其他段逐字节保留）→ 部署事件留痕 `deployment/deploys/{ts}-{agent}.json`；
- **谱系 `source=auto` 的落点 = 部署事件引用候选版本**：不重写 005 的
  `policies/history/{agent}/{version}.meta.json`（其只增不改，与 009/011 采纳留痕同款口径）；
- **指针一致性检测**（防外部绕过）：指针与部署留痕不一致 / 调用方视图与真实指针不符 →
  拒绝 + 告警留痕，指针与留痕都不动；
- 同周期多候选按 reward 择一（其余如实记录，不批量连推）；
- 历史节点零修改：部署只写指针与留痕，树库与策略工件零变更（机检）。
"""

import json
from pathlib import Path

import pytest

from core.deployment import auto_deploy as deploy_module
from core.deployment.auto_deploy import (
    PointerMismatchError,
    auto_deploy,
    deploy_event_path,
    deploy_events,
    read_pointer,
    select_period_candidate,
)
from core.deployment.errors import DeploymentError
from core.deployment.models import AutoDeployEvent

AGENT = "visual"
CANDIDATE = "cand-new"
PREVIOUS = "dep-000"
T0 = "2026-09-21T00:00:00+00:00"
T1 = "2026-09-22T00:00:00+00:00"


def _snapshot(tmp_path, name="snap.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"snapshot_id": "snap-1"}), encoding="utf-8")
    return path


def _deploy(data_dir, pointer, *, candidate=CANDIDATE, snapshot=None, at=T0, from_version=None):
    return auto_deploy(
        agent_id=AGENT,
        candidate_version=candidate,
        snapshot_path=snapshot or Path("missing.json"),
        from_version=from_version or pointer["current_version"],
        cfg=pointer["cfg"],
        data_dir=data_dir,
        config_path=pointer["config"],
        at=at,
    )


def test_normal_deploy_rewrites_pointer_and_records_event(
    deployment_pointer_files, deployment_data_dir, tmp_path, deployment_config
):
    """正常部署：指针定点改写 + 部署事件留痕 + 谱系 source=auto + 历史零修改。"""
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    config_before = pointer["config"].read_text(encoding="utf-8")
    snapshot = _snapshot(tmp_path)

    event = _deploy(deployment_data_dir, pointer, snapshot=snapshot)
    assert isinstance(event, AutoDeployEvent)
    assert event.source == "auto"  # 谱系来源（部署事件即谱系事件）
    assert event.pointer_before == PREVIOUS
    assert event.pointer_after == CANDIDATE
    assert event.evidence_snapshot == str(snapshot)

    # ① 指针已改：注释与其他段逐字节保留（只换指针行的值）
    after = pointer["config"].read_text(encoding="utf-8")
    assert f"current_policy_version: {CANDIDATE}" in after
    assert after == config_before.replace(
        f"current_policy_version: {PREVIOUS}", f"current_policy_version: {CANDIDATE}"
    )
    assert read_pointer(pointer["config"], AGENT) == CANDIDATE

    # ② 部署事件留痕：deploys/{ts}-{agent}.json，只增不改
    path = deploy_event_path(deployment_data_dir, event)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["candidate_version"] == CANDIDATE
    assert payload["from_version"] == PREVIOUS
    assert payload["source"] == "auto"
    assert payload["evidence_snapshot"] == str(snapshot)
    # 只增不改：同刻同内容幂等；同刻不同内容拒绝覆盖
    before = path.read_bytes()
    assert deploy_module.write_deploy_event(deployment_data_dir, event) == path
    assert path.read_bytes() == before
    conflicting = AutoDeployEvent(
        agent_id=AGENT,
        candidate_version="cand-other",
        from_version=PREVIOUS,
        evidence_snapshot=str(snapshot),
        deployed_at=event.deployed_at,
        pointer_before=PREVIOUS,
        pointer_after="cand-other",
        reason="同刻另一次部署",
    )
    with pytest.raises(DeploymentError, match="只增不改"):
        deploy_module.write_deploy_event(deployment_data_dir, conflicting)
    assert path.read_bytes() == before


def test_deploy_requires_evidence_snapshot(
    deployment_pointer_files, deployment_data_dir, deployment_config
):
    """C7 ①：证据快照必须先落盘（无快照不得部署——留痕先于部署）。"""
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    with pytest.raises(DeploymentError, match="快照"):
        _deploy(deployment_data_dir, pointer, snapshot=Path("nope.json"))
    assert read_pointer(pointer["config"], AGENT) == PREVIOUS


def test_pointer_mismatch_with_deploy_ledger_is_refused_with_alert(
    deployment_pointer_files, deployment_data_dir, tmp_path, deployment_config, write_deploy_events
):
    """C7 ②：指针与部署留痕不一致（外部绕过）→ 拒绝 + 告警留痕，指针与留痕都不动。"""
    pointer = deployment_pointer_files(AGENT, current_version="tampered-999")
    pointer["cfg"] = deployment_config
    write_deploy_events(
        [
            {
                "agent_id": AGENT,
                "candidate_version": "cand-old",
                "from_version": "dep--1",
                "pointer_before": "dep--1",
                "pointer_after": "cand-old",
                "deployed_at": T0,
            }
        ]
    )
    config_before = pointer["config"].read_bytes()
    snapshot = _snapshot(tmp_path)
    with pytest.raises(PointerMismatchError) as excinfo:
        _deploy(deployment_data_dir, pointer, snapshot=snapshot)
    assert "防外部绕过" in str(excinfo.value)
    assert read_pointer(pointer["config"], AGENT) == "tampered-999"
    assert pointer["config"].read_bytes() == config_before
    alerts = (deployment_data_dir / "deploys" / "alerts.jsonl").read_text(encoding="utf-8")
    assert "tampered-999" in alerts and "cand-old" in alerts
    assert len(deploy_events(deployment_data_dir, AGENT)) == 1  # 未新增部署事件


def test_stale_caller_view_is_refused(
    deployment_pointer_files, deployment_data_dir, tmp_path, deployment_config
):
    """调用方视图与真实指针不符（竞态/手工改动）→ 拒绝（不把过期视图写上去）。"""
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    with pytest.raises(PointerMismatchError, match="不一致"):
        _deploy(
            deployment_data_dir,
            pointer,
            snapshot=_snapshot(tmp_path),
            from_version="stale-000",
        )
    assert read_pointer(pointer["config"], AGENT) == PREVIOUS


def test_second_deploy_chains_on_previous_event(
    deployment_pointer_files, deployment_data_dir, tmp_path, deployment_config
):
    """连续部署：第二次的 from_version 必须等于第一次留痕的 pointer_after（链式一致）。"""
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    first = _deploy(deployment_data_dir, pointer, snapshot=_snapshot(tmp_path, "s1.json"), at=T0)
    pointer["current_version"] = CANDIDATE
    second = _deploy(
        deployment_data_dir,
        pointer,
        candidate="cand-2",
        snapshot=_snapshot(tmp_path, "s2.json"),
        at=T1,
    )
    assert second.pointer_before == first.pointer_after
    assert [item["candidate_version"] for item in deploy_events(deployment_data_dir, AGENT)] == [
        CANDIDATE,
        "cand-2",
    ]


def test_redeploying_same_version_is_refused(
    deployment_pointer_files, deployment_data_dir, tmp_path, deployment_config
):
    """候选已是当前部署版本 → 拒绝（不做无变化部署，留痕不膨胀）。"""
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    _deploy(deployment_data_dir, pointer, snapshot=_snapshot(tmp_path, "s1.json"))
    pointer["current_version"] = CANDIDATE
    with pytest.raises(DeploymentError, match="已是当前部署版本"):
        _deploy(
            deployment_data_dir,
            pointer,
            candidate=CANDIDATE,
            snapshot=_snapshot(tmp_path, "s2.json"),
            at=T1,
        )


def test_select_period_candidate_picks_highest_reward(
    deployment_data_dir, deployment_config, deployment_pointer_files
):
    """同周期多候选按 reward 择一；其余如实记录（不批量连推）。"""
    record = select_period_candidate(
        [
            {"version": "cand-a", "reward": 0.52},
            {"version": "cand-b", "reward": 0.61},
            {"version": "cand-c", "reward": 0.55},
        ],
        agent_id=AGENT,
        period="2026-W39",
        data_dir=deployment_data_dir,
        at=T0,
    )
    assert record["selected"] == "cand-b"
    assert record["period"] == "2026-W39"
    assert [item["version"] for item in record["rejected"]] == ["cand-c", "cand-a"]
    assert all("reward" in item for item in record["rejected"])
    path = deployment_data_dir / "deploys" / f"selection-{AGENT}-2026-W39.json"
    assert json.loads(path.read_text(encoding="utf-8"))["selected"] == "cand-b"


def test_select_period_candidate_breaks_ties_by_version(deployment_data_dir):
    """平分按版本号升序取（确定性，不靠字典序随机）。"""
    record = select_period_candidate(
        [{"version": "cand-z", "reward": 0.6}, {"version": "cand-a", "reward": 0.6}],
        agent_id=AGENT,
        period="2026-W39",
        data_dir=deployment_data_dir,
        at=T0,
    )
    assert record["selected"] == "cand-a"


def test_other_ledger_files_do_not_pollute_deploy_events(
    deployment_pointer_files, deployment_data_dir, tmp_path, deployment_config
):
    """同族留痕（同周期择一记录）不得被当成部署事件（否则"最后一条部署"会漂移）。"""
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    event = _deploy(deployment_data_dir, pointer, snapshot=_snapshot(tmp_path))
    select_period_candidate(
        [{"version": CANDIDATE, "reward": 0.6}],
        agent_id=AGENT,
        period="2026-W39",
        data_dir=deployment_data_dir,
        at=T0,
    )
    ledger = deploy_events(deployment_data_dir, AGENT)
    assert [item["candidate_version"] for item in ledger] == [event.candidate_version]
    assert all("pointer_after" in item for item in ledger)


def test_select_period_candidate_requires_candidates(deployment_data_dir):
    with pytest.raises(DeploymentError, match="候选"):
        select_period_candidate(
            [], agent_id=AGENT, period="2026-W39", data_dir=deployment_data_dir, at=T0
        )


def test_historical_nodes_and_policy_artifacts_are_untouched(
    deployment_pointer_files,
    deployment_data_dir,
    tmp_path,
    deployment_config,
    deployment_history_root,
    multi_tree_pool,
):
    """历史节点零修改（机检）：部署前后树库行数与策略工件逐字节不变。"""
    history_root = deployment_history_root(AGENT, PREVIOUS)
    trees, store = multi_tree_pool(3)
    before_rows = len(store.trees_by(agent_id=AGENT)) + sum(
        len(store.nodes_of(tree.tree_id)) for tree in trees
    )
    before_artifacts = {
        path: path.read_bytes() for path in sorted(history_root.rglob("*")) if path.is_file()
    }
    pointer = deployment_pointer_files(AGENT, current_version=PREVIOUS)
    pointer["cfg"] = deployment_config
    _deploy(deployment_data_dir, pointer, snapshot=_snapshot(tmp_path))

    after_rows = len(store.trees_by(agent_id=AGENT)) + sum(
        len(store.nodes_of(tree.tree_id)) for tree in trees
    )
    after_artifacts = {
        path: path.read_bytes() for path in sorted(history_root.rglob("*")) if path.is_file()
    }
    assert after_rows == before_rows
    assert after_artifacts == before_artifacts


def test_deploy_module_has_no_db_or_history_write_paths():
    """静态机检：部署模块不碰 DB、不重写 005 的 meta.json（谱系只经部署事件留痕）。"""
    source = Path(deploy_module.__file__).read_text(encoding="utf-8")
    for banned in (
        "import sqlalchemy",
        "create_engine(",
        "write_meta(",
        "record_policy(",
        '"meta.json"',  # 只查字符串字面量（文档说明可提及，代码不得写）
        "meta.json'",
    ):
        assert banned not in source, banned
