# 数据模型：发现树与评估器框架

**日期**: 2026-09-18 | **关联**: [spec.md](spec.md) / [plan.md](plan.md)

## 1. 领域模型（`core/tree/models.py`、`core/evaluators/base.py`）

全部为 `@dataclass(frozen=True)`（宪章原则二，第一道防线）。

### TreeNode（发现树节点）

| 字段 | 类型 | 校验规则 |
| --- | --- | --- |
| node_id | str | uuid7（时间有序）；入库冲突即报错 |
| tree_id | str | 必须指向已存在 DiscoveryTree |
| parent_id | str \| None | 根节点为 None；非根必须指向同树已存在节点 |
| depth | int | ≥ 0；根 = 0；非根 = 父节点 depth + 1 |
| agent_id | str | 非空 |
| policy_version | str | 非空（策略代码 BLAKE3 前 12 位，本特性只做非空校验） |
| prompt | str | 可为空串 |
| observation_context | dict | 回放重建依据；jsonb |
| artifact_hash | str | 64 位十六进制（BLAKE3） |
| eval_breakdown | dict | 每个键必须为 `evaluator_id@version`，缺失即拒写（FR-009） |
| score | float \| None | EVALUATED 时 ∈ [0,1]；FAILED 时必须为 None |
| cost | CostRecord | 必填，全字段 ≥ 0 |
| status | NodeStatus | 见状态机；PLANNED 不得落盘 |
| created_at | float | Unix 时间戳 |

### DiscoveryTree（发现树）

| 字段 | 类型 | 校验规则 |
| --- | --- | --- |
| tree_id | str | uuid7 |
| project_id | str | 非空 |
| agent_id | str | 非空 |
| policy_version | str | 非空 |
| root_id | str | 指向根节点 |
| node_ids | list[str] | 结构清单；节点本体按 node_id 独立存储 |
| config_snapshot | dict | 必填：评估器版本组合 + 权重 + 评估器相关全量配置，落盘即冻结 |

### CostRecord

`llm_calls`、`llm_tokens`、`generation_api_calls`、`generation_api_cost_usd`、
`human_review_minutes`、`wall_clock_seconds`，全部 ≥ 0，默认 0。

### NodeStatus 状态机

```text
PLANNED（仅线上探索内存态，禁止落盘）
   │ 评估成功
   ▼
EVALUATED（score ∈ [0,1] 必填）── 终态
   │ 评估器崩溃等
   ▼
FAILED（score=None）── 终态
```

无反向迁移；存储层与领域层双重校验。

### 评估器侧模型

- **EvaluatorSpec**：`evaluator_id` / `version` / `kind`(rule|proxy_model|judge|human) /
  `deterministic` / `cost_per_call` / `calibration`(dict)
- **EvalResult**：`score` ∈ [0,1] + `diagnostics`(dict，人可读，进 eval_breakdown)

## 2. 存储层（PostgreSQL）

### 表：`tree_nodes`

| 列 | 类型 | 约束 |
| --- | --- | --- |
| node_id | text | PRIMARY KEY |
| tree_id | text | NOT NULL, FK → discovery_trees(tree_id) |
| parent_id | text | NULL 或 FK 自引用 tree_nodes(node_id) |
| depth | integer | NOT NULL, CHECK ≥ 0 |
| agent_id | text | NOT NULL |
| policy_version | text | NOT NULL |
| prompt | text | NOT NULL DEFAULT '' |
| observation_context | jsonb | NOT NULL |
| artifact_hash | text | NOT NULL, CHECK 长度=64 |
| eval_breakdown | jsonb | NOT NULL；应用层校验键格式 `evaluator_id@version` |
| score | double precision | NULL 或 CHECK (0 ≤ score ≤ 1) |
| status | text | NOT NULL, CHECK ∈ ('evaluated','failed')（无 'planned'） |
| cost | jsonb | NOT NULL（CostRecord 序列化，分量均 ≥ 0 由应用层校验） |
| created_at | double precision | NOT NULL |

索引：`(tree_id, parent_id)`（子节点枚举）；`(agent_id, policy_version)` +
`discovery_trees(project_id, agent_id, policy_version)` 复合索引（三维谱系过滤）。

### 表：`discovery_trees`

tree_id PK；project_id / agent_id / policy_version / root_id 强类型列；
node_ids 与 config_snapshot 为 jsonb。

### immutable 双保险（宪章原则二，第二道防线）

```sql
-- 随首个 Alembic 迁移落库；对两张表分别建立
CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'tree data is immutable: % on % not allowed', TG_OP, TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tree_nodes_immutable BEFORE UPDATE OR DELETE ON tree_nodes
  FOR EACH ROW EXECUTE FUNCTION reject_mutation();
-- discovery_trees 同理

REVOKE UPDATE, DELETE ON tree_nodes, discovery_trees FROM cineflow_app;
```

### 工件对象存储

不入库；key = BLAKE3(content) 十六进制，PUT-if-absent 语义天然去重（FR-005）。

## 3. 写入路径校验顺序

1. 领域层：frozen dataclass 构造时校验（score/status 一致性、depth 递推、非空字段）
2. 应用层：`eval_breakdown` 键格式校验（FR-009）；CostRecord 分量非负
3. 存储层：CHECK 约束 + FK + 触发器/权限兜底

任一层失败即拒写，绝不部分落盘。
