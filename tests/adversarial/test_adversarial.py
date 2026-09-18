"""对抗测试套件（US2 / T121，CI 合并阻塞门禁）。

三类作弊策略必须全部被拦截：peek_latent / timing_side_channel / hash_oracle。
**无 Docker 环境报错而非 skip**——门禁不允许静默豁免。

计时断言双重判定（SC-007）：全部响应时间 ∈ 时延量子整数倍，
且与隐藏得分 |Pearson r| < 0.1。
"""

import subprocess

import pytest

from core.sandbox.runner import RunLimits, RunStatus, create_io_handler, run_policy
from core.tree.models import CostRecord, NodeStatus
from tests.adversarial.cheating_policies import (
    HASH_ORACLE_OPEN,
    HASH_ORACLE_STATIC,
    PEEK_LATENT_RUNTIME,
    PEEK_LATENT_STATIC,
    TIMING_PROBE_POLICY,
)

pytestmark = pytest.mark.adversarial

CONTAINER_PREFIX = "cineflow-sandbox-"

TREE_SPEC = [
    (None, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
    (0, {"temperature": 0.3}, 0.9, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
]

# 计时侧信道专用树：4 个命中走法（得分各异）+ 其余参数 UNKNOWN
TIMING_TREE_SPEC = [
    (None, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
    (0, {"temperature": 0.1}, 0.9, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
    (0, {"temperature": 0.2}, 0.1, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
    (0, {"temperature": 0.3}, 0.5, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
    (0, {"temperature": 0.4}, 0.7, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
]


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson 相关系数；任一序列零方差（被量子化抹平）视为 0——无相关性可言。"""
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x**0.5 * var_y**0.5)


@pytest.fixture(scope="module")
def backend():
    """装配链探测：CI 上必须 gVisor（权威后端），本地兜底 hardened；
    两者皆不可用 → 报错而非 skip（门禁不允许静默豁免）。"""
    from core.sandbox.backends import NoBackendAvailableError, select_backend

    try:
        return select_backend()
    except NoBackendAvailableError as exc:
        pytest.fail(f"对抗门禁不允许跳过：{exc}")


@pytest.fixture()
def sandbox_setup(tree_store, build_historical_tree, tmp_path):
    from core.replay.simulator import ReplaySimulator
    from policies.base import Budget

    tree, ids = build_historical_tree(TREE_SPEC)
    # 沙箱形态：模拟器内不填充（latency=0），填充统一在宿主 IPC 桥接层
    simulator = ReplaySimulator.from_trees(
        [tree], tree_store, worker_count=1, budget=Budget(max_probes=16), latency_quantum_ms=0
    )
    limits = RunLimits(latency_quantum_ms=50, wall_clock_seconds=60)
    return simulator, ids, limits, tmp_path


@pytest.fixture(autouse=True)
def _无孤儿容器():
    yield
    leftover = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "-q"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert not leftover, f"存在残留沙箱容器：{leftover}"


class TestPeekLatent:
    def test_反射偷看_静态检查拒绝_不起容器(self, backend, sandbox_setup):
        """US2 场景 1 第一道防线：getattr/私有属性形态静态必拒。"""
        simulator, _, limits, tmp_path = sandbox_setup
        result = run_policy(PEEK_LATENT_STATIC, simulator, limits, backend, history_root=tmp_path)
        assert result.status is RunStatus.POLICY_ERROR
        assert "静态检查" in result.stderr_tail
        assert result.trajectory.probe_count == 0

    def test_枚举偷看_运行时一无所获(self, backend, sandbox_setup):
        """US2 场景 1 第二道防线：容器内无模拟器对象，枚举猜测全部 UNKNOWN。"""
        simulator, ids, limits, tmp_path = sandbox_setup
        result = run_policy(PEEK_LATENT_RUNTIME, simulator, limits, backend, history_root=tmp_path)
        assert result.status is RunStatus.COMPLETED
        trajectory = result.trajectory
        assert trajectory is not None
        # 全部 probe 未命中（UNKNOWN 零得分）：曲线恒为根节点分数
        assert trajectory.best_score_curve == [0.4, 0.4, 0.4, 0.4, 0.4]
        # 最终答案只能是初始已揭示的根节点（无任何泄漏）
        assert trajectory.final_node_id == ids[0]
        assert ids[1] not in simulator.observed()  # 0.9 分节点从未被触及


class TestHashOracle:
    @pytest.mark.parametrize("source", [HASH_ORACLE_STATIC, HASH_ORACLE_OPEN])
    def test_网络与文件IO_静态拒绝(self, backend, sandbox_setup, source):
        simulator, _, limits, tmp_path = sandbox_setup
        result = run_policy(source, simulator, limits, backend, history_root=tmp_path)
        assert result.status is RunStatus.POLICY_ERROR
        assert "静态检查" in result.stderr_tail

    def test_隔离语义直验_无网络无凭证(self, backend):
        """US2 场景 3 运行时直验：同一加固旗标下容器无网络、无对象存储凭证。"""
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                *backend.security_flags(RunLimits()),
                backend.image,
                "python",
                "-c",
                (
                    "import socket, os\n"
                    # 凭证形态：云厂商前缀或 SECRET/TOKEN/PASSWORD 等字样
                    # （镜像自带的 GPG_KEY 属构建期公钥，非对象存储凭证，不计）
                    "creds = [k for k in os.environ if k.startswith("
                    "('AWS_', 'S3_', 'MINIO_', 'OSS_', 'ALIBABA_')) or any("
                    "t in k.upper() for t in ('SECRET', 'TOKEN', 'CREDENTIAL', 'PASSWORD',"
                    " 'ACCESS_KEY'))]\n"
                    "assert not creds, f'凭证泄漏: {creds}'\n"
                    "try:\n"
                    "    socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
                    "    raise SystemExit('网络未隔离!')\n"
                    "except OSError:\n"
                    "    print('NETWORK_DENIED')\n"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert probe.returncode == 0, probe.stderr[-500:]
        assert "NETWORK_DENIED" in probe.stdout


class TestTimingSideChannel:
    def test_响应时间量子化且与隐藏得分无相关(
        self, tree_store, build_historical_tree, backend, tmp_path
    ):
        """US2 场景 2 / SC-007：响应 ∈ 量子整数倍，且与隐藏得分 |Pearson r| < 0.1。

        攻击者可见的信号 = 时间落入的量子桶；桶内残余抖动被 ±8ms 物理上界锁死，
        相关性在量子化后的桶值上计算（n=12 探测：4 命中得分各异 + 8 UNKNOWN）。
        """
        from core.replay.simulator import ReplaySimulator
        from policies.base import Budget

        tree, _ = build_historical_tree(TIMING_TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=1, budget=Budget(max_probes=16), latency_quantum_ms=0
        )
        limits = RunLimits(latency_quantum_ms=50, wall_clock_seconds=60)
        handler = create_io_handler(simulator, limits)
        outcome = backend.run(TIMING_PROBE_POLICY, handler, limits)
        assert outcome.status is RunStatus.COMPLETED, outcome.stderr_tail

        times = handler.response_times_ms
        assert len(times) == 14  # 1 次 observed + 12 次 probe + 1 次 shutdown ack
        quantum = limits.latency_quantum_ms
        for elapsed in times:
            remainder = elapsed % quantum
            assert min(remainder, quantum - remainder) < 8, (
                f"响应时间 {elapsed:.1f}ms 不在量子 {quantum}ms 整数倍上"
            )
        # 攻击者可见信号 = 量子桶值；隐藏得分 = 各次 probe 命中节点的 score
        buckets = [round(elapsed / quantum) for elapsed in times[1:13]]
        hidden_scores = [0.9, 0.1, 0.5, 0.7] + [0.0] * 8
        correlation = abs(_pearson(buckets, hidden_scores))
        assert correlation < 0.1, f"响应时间与隐藏得分相关性 {correlation:.3f} 超限"
