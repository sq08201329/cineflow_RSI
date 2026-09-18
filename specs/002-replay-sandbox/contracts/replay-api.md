# 契约：回放 API（模拟器 / 时钟 / 轨迹 / 策略接口）

**模块**: `core/replay/`、`policies/base.py` | **消费方**: 做梦层、无偏性验收、演示脚本

## 1. 模拟器

```python
class ReplaySimulator:
    @classmethod
    def from_trees(cls, trees: list[DiscoveryTree], store: TreeStore,
                   *, worker_count: int, budget: Budget,
                   latency_quantum_ms: int) -> "ReplaySimulator": ...
    def observed(self) -> dict[str, Observation]: ...
    def probe(self, parent_id: str, gen_params: dict) -> ProbeResult: ...
    def trajectory(self) -> ReplayTrajectory: ...
```

| 操作 | 前置条件 | 成功保证 | 失败语义 |
| --- | --- | --- | --- |
| `from_trees` | 全部树已冻结、同 agent_id；worker_count ≥ 1 | 模拟器就绪；budget.max_generation_calls 被强制为 0 | `PoolError`（未冻结/异 Agent）；`ValidationError`（参数非法） |
| `observed` | — | 仅返回已揭示节点的白名单投影；决策轮 +1 | 无 |
| `probe` | budget 未耗尽 | 精确匹配（规范化 JSON 相等）则揭示真实节点并计虚拟成本；无匹配返回 `UNKNOWN` | `BudgetExhaustedError`（probe 数达上限） |
| `trajectory` | — | 返回截至当前的完整轨迹 | 无 |

**不变量**：
1. `observed()` 的返回绝不包含未揭示节点信息（字段白名单投影）；
2. `probe` 的得分来源只能是真实历史节点——模拟器不生成、不插值（原则三）；
3. 已揭示集合单调递增；`_latent` 无出宿主进程的通道；
4. `probe`/`observed` 响应时间填充至 `latency_quantum_ms` 整数倍（决策 3）。

## 2. 虚拟时钟

```python
class VirtualClock:
    decision_rounds: int
    effective_sequential_rounds: float
    worker_count: int
    def tick_decision(self) -> None: ...
    def tick_execution(self, batch_size: int) -> None: ...  # += ceil(batch_size / worker_count)
```

## 3. 策略接口（policies/base.py）

```python
class SimulatorEnv(Protocol):
    """策略唯一可用的环境接口（沙箱内由 IPC 客户端实现）。"""
    def observed(self) -> dict[str, Observation]: ...
    def probe(self, parent_id: str, gen_params: dict) -> ProbeResult: ...

class ExplorationPolicy(ABC):
    @abstractmethod
    def solve(self, env: SimulatorEnv, budget: Budget) -> str:
        """返回最终选中的 node_id。"""

@dataclass(frozen=True)
class Budget:
    max_probes: int
    max_generation_calls: int  # 回放中恒为 0
```

## 4. 轨迹消费（做梦层前置契约）

`ReplayTrajectory.best_score_curve` 与 `probe_count`、`effective_sequential_rounds`
必须能直接代入奖励函数 `pareto_auc(best_score_curve, probe_count) − λ ·
(effective_sequential_rounds / max(probe_count, 1))`（做梦层特性将按此消费）。
