# 契约：剧本产出执行器（分阶段记录 + 网关生成 + 两段式落盘）

> 对应规格 US1 / FR-001~002。实现：`agents/screenplay/loop.py`、`db.py`、`config.py`。

## C1 分阶段产出

```
run_screenplay_round(engine, store, gateway, policy, inputs, cfg, round_id) -> ScreenplayRoundResult
```

- 输入：题材/约束 + 目标时长 + 角色设定 + 人工策略版本
- 阶段：`outline` → `scenes` → `script`，各自独立 job/节点与评估（`stage` 字段）；前一
  阶段工件是后一阶段的输入（记录脉络完整）
- 网关：缓存键 = 输入摘要 + 模型 + 参数；响应哈希落盘（回放核对）；计费入账

### 场景

1. 一轮三阶段 → 3 个节点（stage 齐备、policy_version = 人工版本），成本按 token 入账
2. 同 round_id 二次触发 → 唯一键 `(round_id, stage, params_hash)` 幂等重建，0 重复生成/扣费
3. 网关失败 → FAILED 节点 + 成本照计（原则二）
4. 任一节点可回溯至策略版本（人工版本同样版本化）

## C2 回放零 LLM

- 回放路径只读历史节点与落盘工件；网关调用计数恒 0（审计断言）

## C3 配置

`agents/screenplay/config.py` 读 configs `screenplay` 段：目标时长与页数容差、
`lines_per_page`、对白动作比例区间、节拍表定义、角色别名表、判据阈值、judge 提示词与
锚点集、静态检查策略；缺节拍表/阈值/价目即报错（不允许静默跳过门禁或无判据）。
