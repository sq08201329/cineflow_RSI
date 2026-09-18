# 契约：一致性验收（回放打分 vs 真实重跑）

**模块**: `agents/visual/consistency.py` | **消费方**: 发布门禁（新评估器/视觉闭环上线前）、CI

## 1. 验收入口

```python
def verify_consistency(tree_id: str, store: TreeStore,
                       artifacts: ArtifactStore, registry: Registry,
                       *, tau_threshold: float = 0.95) -> ConsistencyReport: ...
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 重算 | 对树中全部已评估节点：按 config_snapshot 冻结的评估器版本组合，对同一工件哈希重算得分 |
| 一致判定 | 重算值与落盘值**逐字节相等**（得分已定点归一，FR-012）；一致率必须 = 100% |
| 漂移 | 任一节点不一致 → 记入 `drift_nodes`（节点、评估器键、落盘值、重算值） |
| τ 门禁 | 真实轨迹与回放轨迹得分序列 → 复用 002 `verify_unbiasedness`（threshold 默认 0.95） |
| 判定 | `consistent_rate == 1.0 且 τ 合格 → pass`；否则 reject；样本不足（节点 < 2）→ reject 并注明 |
| 发布阻塞 | verdict=reject 即发布阻塞（宪章质量门禁清单） |

## 3. 报告 schema

见 [data-model.md](data-model.md) §1 ConsistencyReport；`notes` 字段承载人可读说明
（跳过的锚点节点、样本不足原因等）。

## 4. 回归要求

注入非确定性变体（如打乱帧采样顺序的评估器变体）的验收必须 100% reject——
该回归用例是 CI 的一部分（SC-002）。
