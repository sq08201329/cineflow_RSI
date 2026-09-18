"""沙箱端到端集成测试（US2 / T122）。

正常策略容器内回放全程：版本落盘（BLAKE3 前 12 位）、轨迹回传、
容器回收无孤儿；另覆盖策略崩溃（policy_error）、超时（timeout）、
超大消息（protocol_violation）边界。
本地 Docker 可用时真实执行；不可用则 skip（集成测试语义，区别于对抗门禁）。
"""

import subprocess
import time

import pytest

from core.sandbox.backends.docker_hardened import DockerHardenedBackend
from core.sandbox.runner import RunLimits, RunStatus, run_policy
from core.tree.models import CostRecord, NodeStatus
from policies.versioning import policy_version

pytestmark = pytest.mark.integration

CONTAINER_PREFIX = "cineflow-sandbox-"

TREE_SPEC = [
    (None, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
    (0, {"temperature": 0.3}, 0.9, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
    (0, {"temperature": 0.7}, 0.5, NodeStatus.EVALUATED, CostRecord(llm_calls=3)),
]

HONEST_POLICY = """
class Policy:
    def solve(self, env, budget):
        observations = env.observed()
        if not observations:
            return ""
        parent = sorted(observations)[0]
        best, best_score = parent, observations[parent].score or 0.0
        if budget.max_probes > 0:
            result = env.probe(parent, {"temperature": 0.3})
            for obs in result.nodes:
                if obs.score is not None and obs.score > best_score:
                    best, best_score = obs.node_id, obs.score
        return best
"""

CRASH_POLICY = """
class Policy:
    def solve(self, env, budget):
        raise RuntimeError("策略内部崩溃")
"""

HANG_POLICY = """
class Policy:
    def solve(self, env, budget):
        while True:
            pass
"""

OVERSIZE_POLICY = """
class Policy:
    def solve(self, env, budget):
        env.probe("anything", {"pad": "x" * 2_000_000})
        return ""
"""


@pytest.fixture(scope="module")
def backend():
    candidate = DockerHardenedBackend()
    if not candidate.available():
        pytest.skip("Docker 不可用，跳过沙箱集成测试")
    return candidate


@pytest.fixture()
def sandbox_env(tree_store, build_historical_tree, tmp_path):
    from core.replay.simulator import ReplaySimulator
    from policies.base import Budget

    tree, ids = build_historical_tree(TREE_SPEC)

    def make_simulator():
        return ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=2, budget=Budget(max_probes=4), latency_quantum_ms=0
        )

    limits = RunLimits(latency_quantum_ms=10, wall_clock_seconds=60)
    return make_simulator, ids, limits, tmp_path


def _orphan_containers() -> str:
    return subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "-q"],
        capture_output=True,
        text=True,
    ).stdout.strip()


class Test正常回放:
    def test_容器内回放全程(self, backend, sandbox_env):
        make_simulator, ids, limits, tmp_path = sandbox_env
        simulator = make_simulator()
        result = run_policy(HONEST_POLICY, simulator, limits, backend, history_root=tmp_path)

        assert result.status is RunStatus.COMPLETED, result.stderr_tail
        trajectory = result.trajectory
        assert trajectory is not None
        # 版本落盘：trajectory 携带策略版本，history 文件内容逐字节一致
        assert trajectory.policy_version == policy_version(HONEST_POLICY)
        history_file = tmp_path / "agent-replay" / f"{trajectory.policy_version}.py"
        assert history_file.read_text() == HONEST_POLICY
        # 轨迹回传：probe 命中 t=0.3 的 0.9 分节点
        assert trajectory.probe_count == 1
        assert trajectory.best_score_curve == [0.4, 0.9]
        assert trajectory.final_node_id == ids[1]
        assert trajectory.effective_sequential_rounds == 1.0  # ⌈1/2⌉
        assert trajectory.status == "completed"
        # 容器回收无孤儿
        assert _orphan_containers() == ""

    def test_版本落盘幂等(self, backend, sandbox_env):
        make_simulator, _, limits, tmp_path = sandbox_env
        run_policy(HONEST_POLICY, make_simulator(), limits, backend, history_root=tmp_path)
        files_before = sorted(p.name for p in tmp_path.rglob("*.py"))
        # 同内容策略再次回放：版本文件幂等不增生
        run_policy(HONEST_POLICY, make_simulator(), limits, backend, history_root=tmp_path)
        assert sorted(p.name for p in tmp_path.rglob("*.py")) == files_before

    def test_策略崩溃_policy_error_不污染模拟器(self, backend, sandbox_env):
        make_simulator, ids, limits, tmp_path = sandbox_env
        simulator = make_simulator()
        result = run_policy(CRASH_POLICY, simulator, limits, backend, history_root=tmp_path)
        assert result.status is RunStatus.POLICY_ERROR
        assert "策略内部崩溃" in result.stderr_tail
        assert result.trajectory.status == "policy_error"
        # 模拟器状态未被污染：根仍可用
        assert ids[0] in simulator.observed()
        assert _orphan_containers() == ""

    def test_策略死循环_超时回收(self, backend, sandbox_env):
        make_simulator, _, _, tmp_path = sandbox_env
        simulator = make_simulator()
        limits = RunLimits(latency_quantum_ms=10, wall_clock_seconds=8)
        start = time.monotonic()
        result = run_policy(HANG_POLICY, simulator, limits, backend, history_root=tmp_path)
        assert time.monotonic() - start < 30  # 必须在墙钟限额附近被回收
        assert result.status is RunStatus.TIMEOUT
        assert result.trajectory.status == "timeout"
        assert _orphan_containers() == ""

    def test_超大消息_protocol_violation(self, backend, sandbox_env):
        make_simulator, _, limits, tmp_path = sandbox_env
        simulator = make_simulator()
        result = run_policy(OVERSIZE_POLICY, simulator, limits, backend, history_root=tmp_path)
        assert result.status is RunStatus.PROTOCOL_VIOLATION
        assert _orphan_containers() == ""

    def test_凭证与业务配置不注入容器(self, backend, sandbox_env, monkeypatch):
        """隔离语义：宿主即使持有凭证形态的环境变量，容器内也绝不可见。"""
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-test-credential")
        monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
        make_simulator, ids, limits, tmp_path = sandbox_env
        result = run_policy(HONEST_POLICY, make_simulator(), limits, backend, history_root=tmp_path)
        assert result.status is RunStatus.COMPLETED
        # 容器命令行不含任何凭证环境变量注入（-e/--env 只允许 PYTHONDONTWRITEBYTECODE）
        command = backend.last_command
        env_flags = [command[i + 1] for i, a in enumerate(command) if a in ("-e", "--env")]
        assert all(flag.startswith("PYTHONDONTWRITEBYTECODE") for flag in env_flags)


class Test后端探测:
    def test_gvisor_本地不可用_hardened_兜底(self):
        from core.sandbox.backends import select_backend
        from core.sandbox.backends.docker_gvisor import DockerGVisorBackend

        backend = select_backend()
        if DockerGVisorBackend().available():
            assert backend.name == "docker_gvisor"  # CI 形态
        else:
            assert backend.name == "docker_hardened"  # 本地兜底形态

    def test_加固旗标齐全(self, backend):
        flags = backend.security_flags(RunLimits())
        joined = " ".join(flags)
        assert "--network=none" in joined
        assert "--read-only" in joined
        assert "--cap-drop=ALL" in joined
        assert "no-new-privileges" in joined
        assert "--memory=" in joined and "--cpus=" in joined and "--pids-limit=" in joined
