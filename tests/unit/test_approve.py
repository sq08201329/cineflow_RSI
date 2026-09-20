"""人工审批闸门单测（US2 / T414，contracts/approval.md）。

审批单字段完整（diff 摘要 + 双池 reward 对比）、approve/reject 落盘、
部署指针仅 approved 可更新（SC-005 机检）、拒绝后指针不变；
WS3 遗留收口：部署指针定点改写保注释（不全量重写 yaml）。
"""

import difflib
import json
from pathlib import Path

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


def _raw_config(config_path, pointer_block=""):
    """原始 movie.yaml 文本（含全部注释）直接落盘，可选追加部署指针段。"""
    text = (Path(__file__).resolve().parents[2] / "configs" / "movie.yaml").read_text(
        encoding="utf-8"
    )
    config_path.write_text(text + pointer_block, encoding="utf-8")


def _changed_lines(before: str, after: str, sign: str) -> list[str]:
    """unified diff 中指定方向（+/-）的真实变更行（排除文件头 +++/---）。"""
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    return [line for line in diff if line.startswith(sign) and not line.startswith(sign * 3)]


class Test部署指针保注释:
    """WS3 遗留收口：approve 更新部署指针不得全量重写 yaml。

    一期缺陷：decide 走 yaml.safe_load + safe_dump 全量重写，注释与格式全丢。
    收口后仅指针相关行变更，注释/空行/其他段逐字节保留。
    """

    _POINTER_BLOCK = (
        "\n# 部署指针：仅 approved 版本可成为当期策略（SC-005 机检）\n"
        "deployment:\n"
        "  agent-dream:\n"
        "    current_policy_version: champion-v1  # 当期部署策略版本\n"
    )

    def test_approve_保注释_仅指针行变更(self, dream_env):
        env = dream_env
        _raw_config(env["config_path"], self._POINTER_BLOCK)
        before = env["config_path"].read_text(encoding="utf-8")
        ticket = create_approval_ticket(
            env["round"],
            env["round"].champion_version,
            env["factory"].build_pool(env["trees"]),
            tickets_dir=env["tickets_dir"],
            replay_fn=_in_process,
        )
        decide(
            ticket.path,
            approver="张三",
            decision="approved",
            reason="泛化良好",
            history_root=env["history_root"],
            config_path=env["config_path"],
        )
        after = env["config_path"].read_text(encoding="utf-8")
        # 段头注释/行尾注释/其他段注释原样保留
        assert "# 部署指针：仅 approved 版本可成为当期策略（SC-005 机检）" in after
        assert "# 当期部署策略版本" in after
        assert "# 形态配置：电影（movie）" in after
        assert "# 回放形态参数" in after
        # 文件 diff 只触及指针一行
        removed = _changed_lines(before, after, "-")
        added = _changed_lines(before, after, "+")
        assert len(removed) == 1 and len(added) == 1
        assert "current_policy_version" in removed[0]
        assert env["round"].winner_version in added[0]
        # 指针机检可读：新版本有 approved 记录
        pointer = current_policy_version(
            "agent-dream", env["config_path"], history_root=env["history_root"]
        )
        assert pointer == env["round"].winner_version

    def test_无deployment段_指针追加_原文逐字节保留(self, dream_env):
        env = dream_env
        _raw_config(env["config_path"])  # 原始 movie.yaml：无 deployment 段、注释齐全
        before = env["config_path"].read_text(encoding="utf-8")
        ticket = create_approval_ticket(
            env["round"],
            env["round"].champion_version,
            env["factory"].build_pool(env["trees"]),
            tickets_dir=env["tickets_dir"],
            replay_fn=_in_process,
        )
        decide(
            ticket.path,
            approver="张三",
            decision="approved",
            reason="泛化良好",
            history_root=env["history_root"],
            config_path=env["config_path"],
        )
        after = env["config_path"].read_text(encoding="utf-8")
        # 原文逐字节保留（deployment 段仅追加在文件尾）
        assert after.startswith(before)
        winner = env["round"].winner_version
        assert (
            yaml.safe_load(after)["deployment"]["agent-dream"]["current_policy_version"] == winner
        )
        pointer = current_policy_version(
            "agent-dream", env["config_path"], history_root=env["history_root"]
        )
        assert pointer == winner
