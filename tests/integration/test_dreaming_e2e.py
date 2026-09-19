"""做梦沙箱端到端集成测试（T420）。

真实容器沙箱回放候选的一轮做梦全流程：default_sandbox_replay 路径
（策略进程内无模拟器对象、IPC 边界、轨迹回传）。Docker 可用时真实执行；
不可用则 skip（集成测试语义）。
"""

import json

import pytest

from dreaming.candidates import MutatorGenerator
from dreaming.pipeline import default_sandbox_replay, run_dream_round

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def docker_backend():
    from core.sandbox.backends import NoBackendAvailableError, select_backend

    try:
        return select_backend()
    except NoBackendAvailableError:
        pytest.skip("Docker 不可用，跳过做梦沙箱集成测试")


def test_一轮做梦_真实沙箱回放(
    docker_backend, champion_source, multi_tree_pool, dream_config, tmp_path
):
    """M=3 演示档：候选经真实容器沙箱对全池回放，reward 排名与落盘完整。"""
    from core.replay.pool import SimulatorPool

    trees, store = multi_tree_pool(2)
    pool = SimulatorPool(store)
    for tree in trees:
        pool.add_tree(tree)

    result = run_dream_round(
        "agent-dream",
        champion_source(),
        MutatorGenerator("dream-agent-dream-1"),
        pool,
        None,
        dream_config,
        replay_fn=default_sandbox_replay,
        history_root=tmp_path,
        m=3,
    )

    assert result.status == "completed"
    assert len(result.candidates) == 3
    replayed = [c for c in result.candidates if c.trajectory is not None]
    assert len(replayed) == 3  # 全部候选真实容器回放成功
    for candidate in replayed:
        assert candidate.reward is not None
        # 轨迹来自容器内执行（IPC 回传），零生成审计成立
        assert candidate.trajectory["total_cost"]["generation_api_calls"] == 0
        assert candidate.trajectory["policy_version"] == candidate.version
    assert result.winner_version in {c.version for c in replayed}
    assert result.diagnostics["generation_api_calls"] == 0

    # DreamRound 落盘可复核
    payload = json.loads(
        (tmp_path / "agent-dream" / f"{result.round_id}.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "completed"

    # 无孤儿容器
    import subprocess

    leftover = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=cineflow-sandbox-", "-q"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert not leftover, f"存在残留沙箱容器：{leftover}"
