"""人工审批闸门单测（US2 / T414，contracts/approval.md）。

审批单字段完整（diff 摘要 + 双池 reward 对比）、approve/reject 落盘、
部署指针仅 approved 可更新（SC-005 机检）、拒绝后指针不变。
"""

import json

import pytest
import yaml

from dreaming.approve import (
    ApprovalError,
    create_approval_ticket,
    current_policy_version,
    decide,
)
from dreaming.pipeline import run_dream_round


class _PoolFactory:
    """从夹具树构建池的轻封装（审批单双池回放用）。"""

    def __init__(self, store, trees):
        self.store = store
        self.trees = trees

    def build_pool(self, trees):
        from core.replay.pool import SimulatorPool

        pool = SimulatorPool(self.store)
        for tree in trees:
            pool.add_tree(tree)
        return pool


@pytest.fixture()
def dream_env(champion_source, multi_tree_pool, dream_config, tmp_path):
    """跑完一轮做梦（胜者=冠军变异），返回 (dream_round, champion, env)。"""
    from dreaming.candidates import MutatorGenerator

    trees, store = multi_tree_pool(3)
    factory = _PoolFactory(store, trees)
    pool = factory.build_pool(trees)
    champion = champion_source()
    result = run_dream_round(
        "agent-dream",
        champion,
        MutatorGenerator("dream-agent-dream-1"),
        pool,
        None,
        dream_config,
        replay_fn=_in_process,
        history_root=tmp_path / "dreaming",
        m=8,
    )
    assert result.status == "completed"
    return {
        "round": result,
        "champion": champion,
        "factory": factory,
        "trees": trees,
        "config_path": tmp_path / "movie.yaml",
        "history_root": tmp_path / "policies",
        "tickets_dir": tmp_path / "tickets",
    }


def _in_process(source, pool):
    from dreaming.pipeline import in_process_replay

    return in_process_replay(source, pool)


def _write_config(config_path, version=None):
    config = yaml.safe_load(
        (
            __import__("pathlib").Path(__file__).resolve().parents[2] / "configs" / "movie.yaml"
        ).read_text(encoding="utf-8")
    )
    if version is not None:
        config.setdefault("deployment", {}).setdefault("agent-dream", {})[
            "current_policy_version"
        ] = version
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


class Test审批单:
    def test_审批单字段完整(self, dream_env):
        env = dream_env
        _write_config(env["config_path"])
        ticket = create_approval_ticket(
            env["round"],
            env["round"].champion_version,
            env["factory"].build_pool(env["trees"]),
            tickets_dir=env["tickets_dir"],
            replay_fn=_in_process,
        )
        data = json.loads(ticket.path.read_text(encoding="utf-8"))
        assert data["winner_version"] == env["round"].winner_version
        assert data["champion_version"] == env["round"].champion_version
        assert data["diff_summary"]["changed_lines"] >= 0  # 与当期最优的 diff 摘要
        assert "train" in data["reward_compare"] and "validation" in data["reward_compare"]
        assert data["candidate_diagnostics"]  # 候选诊断
        assert data["winner_source"]  # 胜出源码（approve 落盘用）


class Test审批落盘:
    def test_approve_落盘_meta_与源码_指针更新(self, dream_env):
        env = dream_env
        _write_config(env["config_path"])
        ticket = create_approval_ticket(
            env["round"],
            env["round"].champion_version,
            env["factory"].build_pool(env["trees"]),
            tickets_dir=env["tickets_dir"],
            replay_fn=_in_process,
        )
        record = decide(
            ticket.path,
            approver="张三",
            decision="approved",
            reason="泛化良好",
            history_root=env["history_root"],
            config_path=env["config_path"],
        )
        meta = record
        assert meta["approval"]["decision"] == "approved"
        assert meta["approval"]["approver"] == "张三"
        assert meta["parent_version"] == env["round"].champion_version
        assert meta["source"] == "dreaming"
        # 源码幂等落盘 + meta.json（policies/history/{agent}/{version}.*）
        version = env["round"].winner_version
        assert (env["history_root"] / "agent-dream" / f"{version}.py").exists()
        assert (env["history_root"] / "agent-dream" / f"{version}.meta.json").exists()
        # 部署指针更新
        pointer = current_policy_version(
            "agent-dream", env["config_path"], history_root=env["history_root"]
        )
        assert pointer == version

    def test_reject_记录落盘_指针不变(self, dream_env):
        env = dream_env
        _write_config(env["config_path"], version="existing-champion")
        # 既有冠军有 approved 记录（SC-005：指针版本必须可过机检）
        from dreaming.lineage import write_meta

        write_meta(
            env["history_root"],
            "agent-dream",
            {
                "version": "existing-champion",
                "parent_version": "root",
                "created_round": "dream-agent-dream-0",
                "reward": {
                    "pareto_auc": 0.5,
                    "parallel_penalty": 0.0,
                    "lambda": 0.5,
                    "reward": 0.5,
                },
                "source": "manual",
                "approval": {
                    "approver": "init",
                    "at": "2026-09-19T09:00:00+08:00",
                    "decision": "approved",
                    "reason": "初始部署",
                },
            },
        )
        ticket = create_approval_ticket(
            env["round"],
            env["round"].champion_version,
            env["factory"].build_pool(env["trees"]),
            tickets_dir=env["tickets_dir"],
            replay_fn=_in_process,
        )
        decide(
            ticket.path,
            approver="李四",
            decision="rejected",
            reason="覆盖不足",
            history_root=env["history_root"],
            config_path=env["config_path"],
        )
        # meta 照常落盘（审批记录可审计）；源码不落盘；指针不变
        version = env["round"].winner_version
        meta = json.loads(
            (env["history_root"] / "agent-dream" / f"{version}.meta.json").read_text()
        )
        assert meta["approval"]["decision"] == "rejected"
        assert not (env["history_root"] / "agent-dream" / f"{version}.py").exists()
        pointer = current_policy_version(
            "agent-dream", env["config_path"], history_root=env["history_root"]
        )
        assert pointer == "existing-champion"

    def test_decision_枚举校验(self, dream_env):
        env = dream_env
        _write_config(env["config_path"])
        ticket = create_approval_ticket(
            env["round"],
            env["round"].champion_version,
            env["factory"].build_pool(env["trees"]),
            tickets_dir=env["tickets_dir"],
            replay_fn=_in_process,
        )
        with pytest.raises(ApprovalError, match="approved|rejected"):
            decide(
                ticket.path,
                approver="张三",
                decision="maybe",
                reason="",
                history_root=env["history_root"],
                config_path=env["config_path"],
            )


class Test部署指针机检:
    def test_指针版本无approved记录_报错(self, dream_env):
        """SC-005 机检：指针指向的版本必须有 approved 审批记录。"""
        env = dream_env
        _write_config(env["config_path"], version="ghost-version")
        with pytest.raises(ApprovalError, match="approved"):
            current_policy_version(
                "agent-dream", env["config_path"], history_root=env["history_root"]
            )

    def test_指针未配置报错(self, dream_env):
        env = dream_env
        _write_config(env["config_path"])  # 无 deployment 段
        with pytest.raises(ApprovalError, match="指针|current_policy_version"):
            current_policy_version(
                "agent-dream", env["config_path"], history_root=env["history_root"]
            )
