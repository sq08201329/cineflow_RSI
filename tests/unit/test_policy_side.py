"""沙箱内策略侧 IPC 客户端单测（T129 进程内覆盖：容器内语义不起容器）。

policy_side.py 是容器入口（独立标准库脚本）；此处用内存 stdin/stdout
模拟宿主侧，验证其请求/应答/异常路径。
"""

import io
import json

import pytest

from core.sandbox import policy_side

POLICY = """
class Policy:
    def solve(self, env, budget):
        observations = env.observed()
        assert budget.max_probes == 2
        return sorted(observations)[0] if observations else ""
"""

PROBE_POLICY = """
class Policy:
    def solve(self, env, budget):
        result = env.probe("n1", {"temperature": 0.3})
        assert result.status == "unknown"
        assert result.nodes == []
        hit = env.probe("n1", {"temperature": 0.5})
        assert hit.status == "ok"
        assert hit.nodes[0].score == 0.9
        return hit.nodes[0].node_id
"""

OBSERVATION = {
    "node_id": "n1",
    "depth": 0,
    "score": 0.4,
    "cost": {"llm_calls": 1},
    "fields": {"gen_params": {}},
}


def _run_main(monkeypatch, tmp_path, policy_source, responses):
    policy_file = tmp_path / "policy.py"
    policy_file.write_text(policy_source)
    budget_file = tmp_path / "budget.json"
    budget_file.write_text(json.dumps({"max_probes": 2, "max_generation_calls": 0}))

    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in responses))
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.argv", ["policy_side.py", str(policy_file), str(budget_file)])
    rc = policy_side.main()
    return rc, [json.loads(line) for line in stdout.getvalue().splitlines()]


class Test策略侧客户端:
    def test_observed_与_shutdown_全流程(self, monkeypatch, tmp_path):
        responses = [
            {"id": 1, "result": {"nodes": {"n1": OBSERVATION}}},
            {"id": 2, "result": {"final_node_id": "n1"}},
        ]
        rc, sent = _run_main(monkeypatch, tmp_path, POLICY, responses)
        assert rc == 0
        assert sent[0] == {"id": 1, "method": "observed"}
        assert sent[1] == {
            "id": 2,
            "method": "shutdown",
            "params": {"final_node_id": "n1"},
        }

    def test_probe_unknown与ok两态(self, monkeypatch, tmp_path):
        responses = [
            {"id": 1, "result": {"status": "unknown"}},
            {
                "id": 2,
                "result": {
                    "status": "ok",
                    "nodes": [OBSERVATION | {"score": 0.9}],
                    "virtual_cost": {"llm_calls": 2},
                },
            },
            {"id": 3, "result": {"final_node_id": "n1"}},
        ]
        rc, sent = _run_main(monkeypatch, tmp_path, PROBE_POLICY, responses)
        assert rc == 0
        assert sent[0]["method"] == "probe"
        assert sent[0]["params"] == {"parent_id": "n1", "gen_params": {"temperature": 0.3}}

    def test_错误帧抛异常_进程非零退出(self, monkeypatch, tmp_path):
        responses = [{"id": 1, "error": {"code": "budget_exceeded", "message": "预算耗尽"}}]
        with pytest.raises(RuntimeError, match="budget_exceeded"):
            _run_main(monkeypatch, tmp_path, POLICY, responses)

    def test_宿主关闭通道_SystemExit(self, monkeypatch, tmp_path):
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, tmp_path, POLICY, [])
