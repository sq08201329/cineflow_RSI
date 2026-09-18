# 契约：沙箱 IPC 协议与执行语义

**模块**: `core/sandbox/` | **消费方**: 回放装配层、做梦层（未来）、对抗测试

## 1. 消息协议（stdio JSON Lines，每行一条消息，UTF-8，≤ 1MB）

**请求（策略 → 宿主）**：

```json
{"id": 1, "method": "observed"}
{"id": 2, "method": "probe", "params": {"parent_id": "...", "gen_params": {...}}}
{"id": 3, "method": "shutdown"}
```

**响应（宿主 → 策略）**：

```json
{"id": 1, "result": {"nodes": { ... }}}
{"id": 2, "result": {"status": "ok", "nodes": [ ... ], "virtual_cost": { ... }}}
{"id": 2, "result": {"status": "unknown"}}
{"id": 2, "error": {"code": "budget_exceeded | not_found | validation", "message": "..."}}
{"id": 3, "result": {"final_node_id": "..."}}
```

## 2. 协议规则

| 规则 | 行为 |
| --- | --- |
| 值语义 | 只允许 JSON 可序列化类型穿越边界；引用/自定义类不可达 |
| 白名单 | 字段白名单 + 单消息 ≤ 1MB；超限 → `protocol_violation`，本次运行终止 |
| 超时 | 单请求响应超时（默认 30s）→ `timeout`，容器被回收 |
| 抖动 | 所有响应在宿主侧填充至 `latency_quantum_ms` 整数倍后发出（决策 3） |
| 最终答案 | 策略以 `shutdown` 携带最终 node_id 结束；进程退出未给答案 → `policy_error` |

## 3. 执行入口

```python
def run_policy(policy_source: str, simulator: ReplaySimulator,
               limits: RunLimits, backend: SandboxBackend) -> RunResult: ...
```

| 步骤 | 强制行为 |
| --- | --- |
| 静态检查 | AST 白名单（决策 6）不过 → 不起容器，直接 `policy_error` |
| 版本固定 | version = blake3(policy_source)[:12]；写入 `policies/history/{agent_id}/{version}.py`（幂等） |
| 容器隔离 | 无网络（--network=none）、只读根fs、drop 全部 capabilities、no-new-privileges、seccomp、内存/CPU/PID 限额、无对象存储凭证（不挂载、不注入环境变量） |
| 素材访问 | 一期沙箱**不挂载任何素材卷**；本条款为二期预留（视觉 Agent 接入素材库时再定义，届时一律只读） |
| 回收 | 结束/超时/违例一律强制回收容器，不留孤儿进程 |

## 4. 后端选择

`SandboxBackend` 协议：`available() -> bool`、`run(...) -> RunResult`。
装配顺序：`DockerGVisorBackend`（`--runtime=runsc` 可用）→ `DockerHardenedBackend`
兜底 → 两者皆不可用则报错（CI 必须 gVisor 可用，否则 CI 失败而非降级）。

## 5. 对抗保证（tests/adversarial 的验证对象）

1. 策略进程内不存在模拟器对象（peek_latent 必然失败）；
2. 响应时间经量子化后与隐藏得分无统计相关性（timing_side_channel）；
3. 沙箱内无对象存储凭证与网络（hash_oracle 必然失败）。
