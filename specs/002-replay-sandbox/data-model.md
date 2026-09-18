# 数据模型：回放模拟器与沙箱化策略执行

**日期**: 2026-09-18 | **关联**: [spec.md](spec.md) / [plan.md](plan.md)

本特性**不新增数据库表**——模拟器只读消费功能 001 的 TreeStore；以下为内存/文件侧实体，
全部 `@dataclass(frozen=True)`（宪章原则二精神延伸）。

## 1. 回放侧实体（core/replay/）

### Observation（观测投影）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| node_id | str | 已揭示节点标识 |
| depth | int | 深度 |
| score | float \| None | 得分（FAILED 节点为 None） |
| cost | CostRecord | 该节点已发生成本（复用 001 模型） |
| fields | dict | 观测上下文允许的字段投影（白名单过滤后） |

**不变量**：Observation 中**不得**出现未揭示节点的任何信息；字段白名单由树配置快照
中的观测字段清单决定。

### ProbeResult（探针结果）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| status | "ok" \| "unknown" | 是否命中真实历史 |
| nodes | list[Observation] | status=ok 时的揭示节点（可多个，同父同参同批） |
| virtual_cost | CostRecord | 本次揭示计入的虚拟成本 |

### VirtualClock（虚拟时钟）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| decision_rounds | int | 策略决策次数（每次 observed/probe 调用 +1） |
| effective_sequential_rounds | float | 有效串行轮：Σ ⌈batch_size / worker_count⌉ |
| worker_count | int | 并行工作者数，来自形态配置（configs/*.yaml → replay.worker_count） |

校验：`worker_count ≥ 1`；`tick_execution(batch_size)` 要求 `batch_size ≥ 1`。

### ReplayTrajectory（回放轨迹）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| policy_version | str | 策略 BLAKE3 前 12 位（谱系字段，FR-015） |
| best_score_curve | list[float] | 每决策轮后的最优得分序列 |
| probe_count | int | probe 总次数 |
| effective_sequential_rounds | float | 回放结束时的有效串行轮 |
| total_cost | CostRecord | 虚拟成本合计 |
| final_node_id | str \| None | 策略最终选中的节点 |
| status | "completed" \| "budget_exceeded" \| "policy_error" \| "timeout" | 结局 |
| diagnostics | dict | 人可读明细（错误信息、IPC 日志引用等） |

可序列化为 JSON 报告（quickstart 与做梦层奖励函数的直接输入）。

### Budget（预算，policies/base.py）

`max_probes: int`、`max_generation_calls: int`；回放装配时 `max_generation_calls` **必须**
被强制为 0（FR-005，装配层断言，而非依赖调用方自觉）。

## 2. 模拟器内部状态（core/replay/simulator.py）

```text
ReplaySimulator
├── _revealed: dict[node_id, TreeNode]        # 已揭示（→ Observation 投影源）
├── _latent: dict[parent_id, list[TreeNode]]  # 未揭示（绝不离开宿主进程）
├── _clock: VirtualClock
├── _budget: Budget（probe 计数递减）
└── _pool: 池内树清单（构建时校验全部已冻结，FR-001）
```

状态机：节点仅 `latent → revealed` 单向迁移；无反向。`PLANNED` 状态节点按 001 约定
不存在于落盘树中，回放天然不含此态。

## 3. 沙箱侧（core/sandbox/）

### RunLimits（资源限额）

`cpu`、`memory_mb`、`pids`、`wall_clock_seconds`、`ipc_msg_max_bytes`（默认 1MB）、
`latency_quantum_ms`（时延量子，默认 50）。

### RunResult（沙箱运行结果）

`status`（completed / timeout / policy_error / protocol_violation / resource_exceeded）、
`trajectory`（成功时）、`stderr_tail`（截断的诊断）。

### 策略历史版本（policies/history/）

文件路径：`policies/history/{agent_id}/{version}.py`，`version = blake3(源码)[:12]`；
同内容重复提交幂等（同路径覆盖同内容），内容不同版本号必不同。

## 4. 无偏性报告（tests/unbiasedness/ 产出物 schema）

```json
{
  "policy_version": "a1b2c3d4e5f6",
  "tau": 0.97,
  "threshold": 0.95,
  "verdict": "pass | reject",
  "real_scores": [ ... ],
  "replay_scores": [ ... ],
  "notes": "人可读差异说明"
}
```

## 5. 校验规则汇总

- 池构建：任一棵树未冻结 → 拒绝（FR-001）
- probe 匹配：规范化 JSON 精确相等（决策 5）；budget 耗尽 → `BudgetExhaustedError`（FR-007）
- 时钟：`batch_size ≥ 1`、`worker_count ≥ 1`（构造即校验）
- IPC：消息超 1MB、字段白名单外、超时 → `protocol_violation`
- τ 门槛：`tau < 0.95` → verdict=reject（FR-012）；序列长度 < 2 → 拒绝并注明样本不足
