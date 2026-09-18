"""沙箱 runner 与后端组装的进程内单测（补充 US2 覆盖率）。

用脚本化 FakeBackend（按剧本向 IOHandler 喂请求行、回收响应）驱动
run_policy 全编排：静态检查拦截、版本落盘、IPC 分发、结局归并——
不起真实容器（容器语义由 tests/integration 与 tests/adversarial 覆盖）。
"""

import json

import pytest

from core.sandbox.backends.docker_common import (
    CONTAINER_PREFIX,
    build_command,
    prepare_workdir,
    security_flags,
)
from core.sandbox.runner import (
    BackendOutcome,
    IOHandler,
    RunLimits,
    RunStatus,
    create_io_handler,
    run_policy,
)
from core.tree.models import CostRecord, NodeStatus
from policies.versioning import policy_version

TREE_SPEC = [
    (None, {}, 0.4, NodeStatus.EVALUATED, CostRecord(llm_calls=1)),
    (0, {"temperature": 0.3}, 0.9, NodeStatus.EVALUATED, CostRecord(llm_calls=2)),
]

HONEST_POLICY = """
class Policy:
    def solve(self, env, budget):
        return "ok"
"""


class FakeBackend:
    """脚本化假后端：把剧本请求行逐条喂给 handler，记录响应。"""

    name = "fake"

    def __init__(self, script: list[dict], outcome_status: RunStatus = RunStatus.COMPLETED):
        self._script = script
        self._outcome_status = outcome_status
        self.responses: list[dict] = []
        self.ran = False

    def available(self) -> bool:
        return True

    def run(self, policy_source, handler, limits):
        self.ran = True
        for request in self._script:
            line = json.dumps(request).encode() + b"\n"
            response, stop = handler.handle(line)
            if response is not None:
                self.responses.append(json.loads(response))
            if stop:
                break
        return BackendOutcome(status=self._outcome_status, stderr_tail="")


@pytest.fixture()
def sandbox_sim(tree_store, build_historical_tree):
    from core.replay.simulator import ReplaySimulator
    from policies.base import Budget

    tree, ids = build_historical_tree(TREE_SPEC)
    simulator = ReplaySimulator.from_trees(
        [tree], tree_store, worker_count=1, budget=Budget(max_probes=4), latency_quantum_ms=0
    )
    return simulator, ids


def _script_ok():
    return [
        {"id": 1, "method": "observed"},
        {"id": 2, "method": "probe", "params": {"parent_id": "ROOT", "gen_params": {}}},
    ]


class TestIOHandler分发:
    def test_observed_请求转发与投影(self, sandbox_sim):
        simulator, ids = sandbox_sim
        handler = create_io_handler(simulator, RunLimits(latency_quantum_ms=0))
        script = [{"id": 1, "method": "observed"}]
        FakeBackend(script).run("", handler, RunLimits(latency_quantum_ms=0))
        # 直接驱动 handle 以取响应
        response, stop = handler.handle(json.dumps(script[0]).encode() + b"\n")
        assert not stop
        payload = json.loads(response)
        nodes = payload["result"]["nodes"]
        assert list(nodes) == [ids[0]]
        assert nodes[ids[0]]["score"] == 0.4
        assert set(nodes[ids[0]]) == {"node_id", "depth", "score", "cost", "fields"}

    def test_probe_命中与_unknown(self, sandbox_sim):
        simulator, ids = sandbox_sim
        handler = create_io_handler(simulator, RunLimits(latency_quantum_ms=0))

        def call(msg):
            response, _ = handler.handle(json.dumps(msg).encode() + b"\n")
            return json.loads(response)

        hit = call(
            {
                "id": 1,
                "method": "probe",
                "params": {"parent_id": ids[0], "gen_params": {"temperature": 0.3}},
            }
        )
        assert hit["result"]["status"] == "ok"
        assert hit["result"]["nodes"][0]["score"] == 0.9
        assert hit["result"]["virtual_cost"]["llm_calls"] == 2

        miss = call({"id": 2, "method": "probe", "params": {"parent_id": ids[0], "gen_params": {}}})
        assert miss["result"] == {"status": "unknown"}

    def test_probe_预算耗尽映射错误帧(self, tree_store, build_historical_tree):
        from core.replay.simulator import ReplaySimulator
        from policies.base import Budget

        tree, ids = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=1, budget=Budget(max_probes=0), latency_quantum_ms=0
        )
        handler = create_io_handler(simulator, RunLimits(latency_quantum_ms=0))
        response, _ = handler.handle(
            json.dumps(
                {
                    "id": 1,
                    "method": "probe",
                    "params": {"parent_id": ids[0], "gen_params": {}},
                }
            ).encode()
            + b"\n"
        )
        payload = json.loads(response)
        assert payload["error"]["code"] == "budget_exceeded"
        assert handler.budget_exceeded

    def test_协议违例终止运行(self, sandbox_sim):
        simulator, _ = sandbox_sim
        handler = create_io_handler(simulator, RunLimits(latency_quantum_ms=0))
        response, stop = handler.handle(b'{"id": 1, "method": "eval"}\n')
        assert stop
        assert json.loads(response)["error"]["code"] == "protocol_violation"
        assert handler.violation is not None

    def test_超大消息违例(self, sandbox_sim):
        simulator, _ = sandbox_sim
        handler = create_io_handler(simulator, RunLimits(latency_quantum_ms=0))
        _, stop = handler.handle(b"x" * (1_048_576 + 1))
        assert stop
        assert handler.violation is not None

    def test_shutdown_携带最终答案(self, sandbox_sim):
        simulator, ids = sandbox_sim
        handler = create_io_handler(simulator, RunLimits(latency_quantum_ms=0))
        response, stop = handler.handle(
            json.dumps(
                {"id": 9, "method": "shutdown", "params": {"final_node_id": ids[0]}}
            ).encode()
            + b"\n"
        )
        assert stop
        assert handler.final_node_id == ids[0]
        assert json.loads(response)["result"]["final_node_id"] == ids[0]


class TestRunPolicy编排:
    def test_静态检查拒绝_不起容器(self, sandbox_sim, tmp_path):
        simulator, _ = sandbox_sim
        backend = FakeBackend([])
        result = run_policy("import os\n", simulator, RunLimits(), backend, history_root=tmp_path)
        assert result.status is RunStatus.POLICY_ERROR
        assert not backend.ran  # 不起容器
        assert result.trajectory.status == "policy_error"
        assert "静态检查" in result.stderr_tail

    def test_成功路径_版本落盘与轨迹归并(self, sandbox_sim, tmp_path):
        simulator, ids = sandbox_sim
        script = _script_ok() + [
            {"id": 3, "method": "shutdown", "params": {"final_node_id": ids[1]}}
        ]
        # 修正剧本中的父节点 id
        script[1]["params"]["parent_id"] = ids[0]
        backend = FakeBackend(script)
        result = run_policy(HONEST_POLICY, simulator, RunLimits(), backend, history_root=tmp_path)

        assert result.status is RunStatus.COMPLETED
        trajectory = result.trajectory
        assert trajectory.policy_version == policy_version(HONEST_POLICY)
        assert trajectory.final_node_id == ids[1]
        assert trajectory.probe_count == 1
        assert trajectory.status == "completed"
        # 版本落盘：history/{agent_id}/{version}.py
        version_file = tmp_path / "agent-replay" / f"{trajectory.policy_version}.py"
        assert version_file.read_text() == HONEST_POLICY

    def test_probe_错误帧不妨碍后续(self, sandbox_sim, tmp_path):
        """probe UNKNOWN 后策略继续，正常 shutdown → completed。"""
        simulator, ids = sandbox_sim
        script = [
            {"id": 1, "method": "probe", "params": {"parent_id": ids[0], "gen_params": {}}},
            {"id": 2, "method": "shutdown", "params": {"final_node_id": ids[0]}},
        ]
        backend = FakeBackend(script)
        result = run_policy(HONEST_POLICY, simulator, RunLimits(), backend, history_root=tmp_path)
        assert result.status is RunStatus.COMPLETED
        assert backend.responses[0]["result"] == {"status": "unknown"}

    def test_协议违例优先于后端结局(self, sandbox_sim, tmp_path):
        simulator, _ = sandbox_sim
        backend = FakeBackend([{"id": 1, "method": "eval"}])  # 白名单外方法
        result = run_policy(HONEST_POLICY, simulator, RunLimits(), backend, history_root=tmp_path)
        assert result.status is RunStatus.PROTOCOL_VIOLATION

    def test_后端超时报_backfilled(self, sandbox_sim, tmp_path):
        simulator, _ = sandbox_sim
        backend = FakeBackend([], outcome_status=RunStatus.TIMEOUT)
        result = run_policy(HONEST_POLICY, simulator, RunLimits(), backend, history_root=tmp_path)
        assert result.status is RunStatus.TIMEOUT
        assert result.trajectory.status == "timeout"

    def test_预算耗尽结局映射(self, tree_store, build_historical_tree, tmp_path):
        from core.replay.simulator import ReplaySimulator
        from policies.base import Budget

        tree, ids = build_historical_tree(TREE_SPEC)
        simulator = ReplaySimulator.from_trees(
            [tree], tree_store, worker_count=1, budget=Budget(max_probes=0), latency_quantum_ms=0
        )
        script = [{"id": 1, "method": "probe", "params": {"parent_id": ids[0], "gen_params": {}}}]
        backend = FakeBackend(script, outcome_status=RunStatus.POLICY_ERROR)
        result = run_policy(HONEST_POLICY, simulator, RunLimits(), backend, history_root=tmp_path)
        assert result.status is RunStatus.POLICY_ERROR
        assert result.trajectory.status == "budget_exceeded"


class Test后端命令组装:
    def test_加固旗标与命令(self, tmp_path):
        limits = RunLimits(cpu=0.5, memory_mb=128, pids=32)
        command = build_command("img:test", "cineflow-sandbox-x", tmp_path, limits, [])
        joined = " ".join(command)
        assert "--network=none" in joined
        assert "--read-only" in joined
        assert "--cap-drop=ALL" in joined
        assert "no-new-privileges" in joined
        assert "--memory=128m" in joined and "--cpus=0.5" in joined and "--pids-limit=32" in joined
        assert f"-v {tmp_path}:/work:ro" in joined
        assert "-e PYTHONDONTWRITEBYTECODE=1" in joined  # 唯一注入的 env（非凭证）

    def test_security_flags_独立可取(self):
        assert "--network=none" in security_flags(RunLimits())

    def test_prepare_workdir_三文件(self, tmp_path, sandbox_sim):
        simulator, _ = sandbox_sim
        handler = IOHandler(simulator, RunLimits())
        workdir = tmp_path / "work"
        prepare_workdir("class Policy:\n    pass\n", handler, workdir)
        assert (workdir / "policy.py").read_text().startswith("class Policy")
        budget = json.loads((workdir / "budget.json").read_text())
        assert budget == {"max_probes": 4, "max_generation_calls": 0}
        assert "policy_side" in (workdir / "policy_side.py").name
        # 挂载权限：cap-drop=ALL 后容器内可穿越（0755/0644）
        assert oct(workdir.stat().st_mode)[-3:] == "755"


class Test容器名前缀:
    def test_前缀供孤儿检查(self):
        assert CONTAINER_PREFIX == "cineflow-sandbox-"
