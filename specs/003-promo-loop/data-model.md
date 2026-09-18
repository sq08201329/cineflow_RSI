# 数据模型：宣发 Agent 全闭环

**日期**: 2026-09-18 | **关联**: [spec.md](spec.md) / [plan.md](plan.md)

## 1. 领域实体

### PromoMaterial（宣发物料）

| 字段 | 类型 | 校验 |
| --- | --- | --- |
| material_id | str | uuid7 |
| kind | str | 物料类型（如 `copy` 文案 / `poster_params` 海报参数） |
| content | dict | 物料内容（文案文本/海报参数），工件化后内容寻址入库 |
| artifact_hash | str | BLAKE3（content 规范化序列化） |
| platform | str | 目标平台 |
| tags | list[str] | 题材/类型标签（CTR 分桶键之一） |

### Campaign（投放活动，可变运营状态）

| 字段 | 类型 | 校验 |
| --- | --- | --- |
| campaign_id | str | uuid7 |
| round_id | str | 所属探索轮次（幂等键一部分） |
| material_id | str | 关联物料 |
| node_id | str \| None | 落盘后的树节点（未落盘为 None） |
| status | 枚举 | `created → delivering → delivered → ingested`；`failed` 为失败终态 |
| spent_usd | float | ≥ 0，事务内扣减 |
| external_id | str \| None | 平台返回的活动标识 |
| metrics | dict \| None | 回流后的指标快照（落盘前暂存） |

### MetricSnapshot（指标快照）

`ctr`、`completion_rate`、`conversions`、`impressions`、`clicks`、平台时间戳、
数据版本；校验：比率 ∈ [0,1]、计数 ≥ 0，越界拒绝（FR-006）。

### RoundBudget（轮次预算）

`round_id`、`total_budget_usd`、`pilot_ratio`（默认 0.02）、`spent_usd`；
不变量：`spent_usd ≤ total_budget_usd × pilot_ratio`（FR-002，含 1 个最小货币单位边界）。

### EvolutionReport（进化报告，JSON 产出）

```json
{
  "agent_id": "promo",
  "generated_at": "2026-09-18T...",
  "pool_tree_ids": ["..."],
  "variants": [
    {"policy_version": "...", "best_score_curve": [...], "probe_count": 0,
     "effective_sequential_rounds": 0.0, "total_cost": {...},
     "reward_parts": {"pareto_auc": 0.0, "parallel_penalty": 0.0}}
  ],
  "verdict": "variant_better | baseline_better | inconclusive"
}
```

## 2. 存储层增量：迁移 0002 `promo_campaigns`（**可变**运营表）

| 列 | 类型 | 约束 |
| --- | --- | --- |
| campaign_id | text | PRIMARY KEY |
| round_id | text | NOT NULL；与 material_id 组成唯一键（幂等） |
| material_id | text | NOT NULL |
| node_id | text | NULL（落盘后回填，回填即终态不再变） |
| status | text | NOT NULL CHECK ∈ ('created','delivering','delivered','ingested','failed') |
| spent_usd | double precision | NOT NULL DEFAULT 0, CHECK ≥ 0 |
| external_id | text | NULL |
| metrics | jsonb | NULL（回流后写入一次） |
| created_at / updated_at | double precision | NOT NULL |

**注意**：本表是运营状态，**不适用** immutable 触发器；immutable 约束仍只作用于
`tree_nodes`/`discovery_trees`。树节点由本表状态 `ingested` 后一次性构造 INSERT。

唯一键：`(round_id, material_id)` —— 同轮次同物料重复投放直接命中约束。

## 3. 评估明细键约定（宣发树节点）

```json
"eval_breakdown": {
  "rule.material_compliance@1.0.0": {"score": 1.0, "diagnostics": {...}},
  "proxy.ctr_history@<快照哈希前12位>": {"score": 0.62, "diagnostics": {...}},
  "human.platform_metrics@1.0.0": {"score": 0.58, "diagnostics": {"ctr": ..., "completion_rate": ...}}
}
```

- `human.platform_metrics` 的 score = 平台真值的归一化合成（完播率/CTR/转化按 configs
  权重），diagnostics 保留原始指标；
- CTR 代理的版本号携带数据快照哈希（research 决策 4）；
- 合成权重来自 `configs/movie.yaml` 的 `evaluator_weights.promo` 段（冻结进 config_snapshot）。

## 4. 闭环状态机

```text
轮次触发（round_id）
  → 物料生成（LLM 网关计费）
  → rule.material_compliance 门禁（不合格：物料被拒，成本入账，走下一物料）
  → 预算门禁（spent + 申请 ≤ 上限，事务内校验扣减；超限：拒投记录）
  → Campaign created → delivering（适配器投放）
  → delivered（平台完成，待回流）
  → ingest_metrics 校验 MetricSnapshot → 一次性构造完整 TreeNode INSERT
  → ingested（终态；节点冻结，可入回放池）
任一步失败 → failed（成本照常入账，可重试回流）
```
