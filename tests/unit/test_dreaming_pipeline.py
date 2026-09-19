"""做梦执行管线单测（US1 / T410，contracts/dreaming.md）。

M 套流程、违规 0 回放不记分、哈希去重、reward 排名正确、零生成审计、
failed_all_rejected / failed_all_unknown 状态、DreamRound 落盘。
"""

import json

import pytest

from dreaming.pipeline import (
    DreamRound,
    in_process_replay,
    run_dream_round,
)
from policies.versioning import policy_version


class ScriptGenerator:
    """测试用候选生成器桩：返回给定源码列表。"""

    def __init__(self, sources):
        self._sources = sources

    def generate(self, champion_source, digest, m):
        return list(self._sources)[:m]


@pytest.fixture()
def pool(multi_tree_pool):
    from core.replay.pool import SimulatorPool

    trees, store = multi_tree_pool(3)
    sim_pool = SimulatorPool(store)
    for tree in trees:
        sim_pool.add_tree(tree)
    return sim_pool


def _run(agent_id, champion, generator, pool, dream_config, tmp_path, m=8):
    return run_dream_round(
        agent_id,
        champion,
        generator,
        pool,
        None,  # 网关（MutatorGenerator 不用 LLM）
        dream_config,
        replay_fn=in_process_replay,
        history_root=tmp_path,
        m=m,
    )


class Test流程:
    def test_M套候选全流程(self, champion_source, pool, dream_config, tmp_path):
        from dreaming.candidates import MutatorGenerator

        result = _run(
            "agent-dream",
            champion_source(),
            MutatorGenerator("dream-agent-dream-1"),
            pool,
            dream_config,
            tmp_path,
        )
        assert isinstance(result, DreamRound)
        assert result.status == "completed"
        assert len(result.candidates) == 8
        assert result.winner_version is not None
        # digest 哈希入 DreamRound 可复核
        from dreaming.digest import digest_hash

        assert result.digest_sha == digest_hash(result.digest)

    def test_违规候选零回放不记分(self, champion_source, pool, dream_config, tmp_path):
        evil = "import socket\n\n\nclass Policy:\n    pass\n"
        good = champion_source()
        calls = []

        def counting_replay(source, sim_pool):
            calls.append(source)
            return in_process_replay(source, sim_pool)

        result = run_dream_round(
            "agent-dream",
            good,
            ScriptGenerator([evil, good]),
            pool,
            None,
            dream_config,
            replay_fn=counting_replay,
            history_root=tmp_path,
            m=2,
        )
        rejected = [c for c in result.candidates if c.static_check == "rejected"]
        passed = [c for c in result.candidates if c.static_check == "passed"]
        assert len(rejected) == 1 and len(passed) == 1
        assert rejected[0].trajectory is None and rejected[0].reward is None
        assert rejected[0].violations  # 违规清单
        assert calls == [good]  # 违规候选不回放（FR-003/SC-002）

    def test_哈希去重(self, champion_source, pool, dream_config, tmp_path):
        good = champion_source()
        result = _run(
            "agent-dream",
            good,
            ScriptGenerator([good, good, good]),
            pool,
            dream_config,
            tmp_path,
            m=3,
        )
        assert len(result.candidates) == 1  # 同版本号自动去重（FR-001 边界）
        assert result.candidates[0].version == policy_version(good)

    def test_reward排名正确(self, champion_source, pool, dream_config, tmp_path):
        from dreaming.candidates import MutatorGenerator
        from dreaming.reward import compute_reward

        result = _run(
            "agent-dream",
            champion_source(),
            MutatorGenerator("dream-agent-dream-1"),
            pool,
            dream_config,
            tmp_path,
        )
        scored = [c for c in result.candidates if c.reward is not None]
        rewards = [c.reward.reward for c in scored]
        assert rewards  # 有回放成功的候选
        # 胜者 = reward 最大者
        best = max(scored, key=lambda c: (c.reward.reward,))
        assert result.winner_version == best.version
        # reward 口径与 trajectory 一致（λ 来自配置）
        for c in scored:
            assert c.reward.lambda_ == dream_config.lambda_
            recomputed = compute_reward(c.trajectory_obj, dream_config.lambda_)
            assert c.reward.reward == pytest.approx(recomputed.reward)

    def test_零生成审计断言(self, champion_source, pool, dream_config, tmp_path):
        from dreaming.candidates import MutatorGenerator

        result = _run(
            "agent-dream",
            champion_source(),
            MutatorGenerator("dream-agent-dream-1"),
            pool,
            dream_config,
            tmp_path,
        )
        # FR-012：全程生成 API 调用恒为 0（回放只读历史）
        assert result.diagnostics["generation_api_calls"] == 0
        for c in result.candidates:
            if c.trajectory is not None:
                assert c.trajectory["total_cost"]["generation_api_calls"] == 0

    def test_DreamRound落盘可复核(self, champion_source, pool, dream_config, tmp_path):
        from dreaming.candidates import MutatorGenerator

        result = _run(
            "agent-dream",
            champion_source(),
            MutatorGenerator("dream-agent-dream-1"),
            pool,
            dream_config,
            tmp_path,
        )
        path = tmp_path / "agent-dream" / f"{result.round_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["round_id"] == result.round_id
        assert payload["digest_sha"] == result.digest_sha
        assert len(payload["candidates"]) == len(result.candidates)


class Test失败语义:
    def test_全灭_failed_all_rejected(self, champion_source, pool, dream_config, tmp_path):
        evil1 = "import os\n"
        evil2 = "eval('1')\n"
        result = _run(
            "agent-dream",
            champion_source(),
            ScriptGenerator([evil1, evil2]),
            pool,
            dream_config,
            tmp_path,
            m=2,
        )
        assert result.status == "failed_all_rejected"
        assert result.winner_version is None

    def test_全UNKNOWN_failed_all_unknown(self, champion_source, dream_config, tmp_path):
        """池为空（无经验覆盖）：全部回放 UNKNOWN → reward 0 + 告警提示扩大探索。"""
        from sqlalchemy import create_engine

        from core.replay.pool import SimulatorPool
        from core.tree.db import create_schema
        from core.tree.store import create_tree_store

        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        empty_pool = SimulatorPool(create_tree_store(engine))

        result = run_dream_round(
            "agent-dream",
            champion_source(),
            ScriptGenerator([champion_source()]),
            empty_pool,
            None,
            dream_config,
            replay_fn=in_process_replay,
            history_root=tmp_path,
            m=1,
        )
        assert result.status == "failed_all_unknown"
        assert result.winner_version is None
        assert "扩大线上探索" in result.diagnostics["note"]

    def test_候选崩溃记零分不阻断(self, champion_source, pool, dream_config, tmp_path):
        crashing = (
            "class Policy:\n    def solve(self, env, budget):\n        raise RuntimeError('boom')\n"
        )
        result = _run(
            "agent-dream",
            champion_source(),
            ScriptGenerator([crashing, champion_source()]),
            pool,
            dream_config,
            tmp_path,
            m=2,
        )
        crashed = [c for c in result.candidates if c.note.startswith("回放失败")]
        assert len(crashed) == 1
        assert crashed[0].reward is None  # 排名按 0 分
        assert result.status == "completed"
        assert result.winner_version == policy_version(champion_source())
