"""沙箱内策略侧 IPC 客户端（容器入口，T129）。

本文件以只读挂载进入容器、独立运行——不得 import 项目代码
（容器内只有 python:3.11-slim 标准库）。

流程：读策略文件与预算 → exec 策略代码（已过宿主静态检查）→
实例化 Policy → solve(env, budget)；env 的 observed/probe 经 stdio
JSON Lines 转发给宿主；solve 返回后以 shutdown 携带最终 node_id 结束。
进程退出未给答案 → 宿主判 policy_error。
"""

import json
import sys
import types


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _recv() -> dict:
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("宿主侧关闭了 IPC 通道")
    return json.loads(line)


class _IpcEnv:
    """策略唯一可用的环境：observed/probe 经 IPC 转发（无模拟器对象可言）。"""

    def __init__(self) -> None:
        self._next_id = 0

    def _call(self, method: str, params: dict | None = None):
        self._next_id += 1
        request = {"id": self._next_id, "method": method}
        if params is not None:
            request["params"] = params
        _send(request)
        response = _recv()
        if "error" in response:
            error = response["error"]
            raise RuntimeError(f"{error['code']}: {error['message']}")
        return response["result"]

    def observed(self):
        result = self._call("observed")
        return {
            node_id: types.SimpleNamespace(**fields) for node_id, fields in result["nodes"].items()
        }

    def probe(self, parent_id: str, gen_params: dict):
        result = self._call("probe", {"parent_id": parent_id, "gen_params": gen_params})
        if result["status"] == "unknown":
            return types.SimpleNamespace(status="unknown", nodes=[], virtual_cost=None)
        return types.SimpleNamespace(
            status="ok",
            nodes=[types.SimpleNamespace(**node) for node in result["nodes"]],
            virtual_cost=result.get("virtual_cost"),
        )


def main() -> int:
    policy_path, budget_path = sys.argv[1], sys.argv[2]
    with open(policy_path, encoding="utf-8") as fp:  # 只读挂载内的策略文件
        source = fp.read()
    with open(budget_path, encoding="utf-8") as fp:
        budget = types.SimpleNamespace(**json.load(fp))

    namespace: dict = {}
    exec(compile(source, policy_path, "exec"), namespace)  # noqa: S102 - 策略代码已过静态检查
    policy = namespace["Policy"]()
    env = _IpcEnv()
    final_node_id = policy.solve(env, budget)

    env._next_id += 1
    _send(
        {
            "id": env._next_id,
            "method": "shutdown",
            "params": {"final_node_id": final_node_id},
        }
    )
    _recv()  # 等待宿主 ack 后退出
    return 0


if __name__ == "__main__":
    sys.exit(main())
