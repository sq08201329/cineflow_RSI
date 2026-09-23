# 数据模型：开发 Agent 降级模式（017-dev-agent-degraded）

> 存储分层：`dev_jobs` 运营表（可变中间态，迁移 `0010`）→ 评估完成后节点一次性 INSERT
> （immutable，001 纪律）；立项组合工件（结构化 JSON）内容寻址入对象存储；判据材料 / 对比报告 /
> 采纳记录为 append-only JSON 文件（落 `calibration/upgrade-events/dev/`、`dev/comparisons/`、
> `dev/adoptions/`）。字段级约定见 [contracts/dev-artifact.md](contracts/dev-artifact.md) 与
> [contracts/dev-loop-degraded.md](contracts/dev-loop-degraded.md)。

## DB：`dev_jobs`（迁移 0010，可变运营表）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| job_id | TEXT PK | round_id 确定性派生 |
| round_id | TEXT NOT NULL | 产出轮次 |
| policy_version | TEXT NOT NULL | 人工策略版本（BLAKE3 前 12 位） |
| inputs_json | TEXT NOT NULL | 规范化立项约束 JSON（题材边界/受众/形态参数） |
| params_hash | TEXT NOT NULL | 参数哈希（幂等键分量） |
| cache_key | TEXT | 网关缓存键（命中即复现） |
| response_hash | TEXT | 网关响应哈希（回放核对） |
| status | TEXT NOT NULL | pending → generated → evaluated → inserted / failed |
| estimated_cost_usd | REAL NOT NULL | 按 token 预估（网关价目） |
| actual_cost_usd | REAL | CHECK (actual ≤ estimated) |
| artifact_hash | TEXT | 64 位小写 hex（`slate_hash()`） |
| error | TEXT | failed 时填 |
| created_at | TEXT NOT NULL | ISO8601 UTC |

唯一键 **`(round_id, params_hash)`**——本环节单一产出，**无 stage 列**（009 为
`(round_id, stage, params_hash)`，`agents/screenplay/db.py:60`；此处删去该列而非置空）。

## 领域模型

- **立项组合工件（TopicSlate）**: `schema_version` / `entries: [SlateEntry]` /
  `signal_sources`（模拟数据源标注，随产物本体落盘）/ `production_marks`（策略请求的进入生产指向，原样保留、不代判）；`canonical_json()` 确定性、
  `slate_hash()` = BLAKE3(canonical JSON) 内容寻址；`produce_ids` = 进入生产标记
- **组合条目（SlateEntry）**: `direction_id`（组合内唯一）/ `rationale` / `eval_components`
  （条目级分量呈现；实测分量仍以节点 `eval_breakdown` 为准）/ `genre` / `constraints` /
  `characters`（可移交下游的剧本输入要点）/ `in_production`
- **DevJob**: 运营表行内存形态（可变）
- **DevConfig**: `dev` 段解析结果——组合条目数区间、进入生产标记数区间、最小可比对树数、
  组合约束（多样性/去重率、条目数上限）、门禁阈值与权重来源、判据阈值、模拟数据源参数；
  缺项即装配期报错（不取码内默认）；随轮次树 `config_snapshot` 冻结
- **模拟数据源（SimulatedSignalSource）**: 确定性函数 + 夹具参数（基线/灵敏度/夹具表）；
  输出分量值 + 来源标注（`simulated=true`、"非真实商业数据"）；参数变更 ⇒ 新评估器版本
- **HumanPolicyVersion**: version（源码 BLAKE3 前 12 位）/ parent_version / 提交人 / 提交时间 /
  静态检查结果 / `no_auto_evolve` 审计标记 / source=manual
- **ReplayComparison**: new_version / deployed_version / 逐树得分 / 分项评估器差异 /
  pareto_auc 曲线（复用 `dreaming/reward` 口径）/ UNKNOWN 说明 / **可比对树数（样本量）**
- **AdoptionRecord**: comparison_id / 结论（adopt|reject）/ 人 / 时间 / 依据 / 理由
- **证据项（EvidenceItem）**: `key` / `threshold_key` / 提供者结果——取值 =
  `实测值` | `无法评价（来源缺失）` + 缺失原因；**每项必有取值形态，不得留空或省略**
- **升级判据材料（UpgradeEvidence）**: period / **全量阈值快照** / `items: [EvidenceItem]` /
  系统结论（`below` | `insufficient`，**永不为 `meets`**）/ 继续观察条件（待补齐阈值项清单）/
  推翻记录（人/时间/理由）

## 评估器组合（`evaluator_weights.dev`）

```yaml
dev:
  rule.slate_structure: gate     # 条目数区间 + 方向标识唯一 + 必填要点齐备
  rule.slate_combination: gate   # 组合层：多样性/去重率、条目数上限、进入生产标记数量与指向
  proxy.genre_regression: 0.5    # 历史同类型票房回归预测（模拟数据源，确定性）
  proxy.buzz_heat: 0.5           # 舆情检索热度（模拟数据源，确定性）
```

权重取值进配置（两形态取值不同）；`dev` 段一并纳入 `tests/unit/test_form_switch.py` 的顶层差异
键集与权重差异循环（见 [research.md](research.md) 决策 8）。

## `dev` 段配置 schema（两形态均须声明）

```yaml
dev:
  slate: {min: 3, max: 6}                          # 组合条目数区间
  production_marks: {min: 1, max: 1}               # 进入生产标记数区间
  combination: {max_direction_repeat_rate: 0.3}    # 组合约束（多样性/去重率、上限）
  min_comparable_trees: 3                          # 回放对比最小可比对树数（前置门槛）
  signals: {baseline_usd_million: …, sensitivity: …, fixtures: …}   # 模拟数据源参数
  upgrade_criteria: {correlation_target: …, min_samples: …, drift_band: …,
                     gate_violation_max: …}        # 判据阈值（全量声明，缺失即报错）
  model: mock-copy-v1                              # 立项论证正文的生成模型（走网关）
  model_prices: {…}                                # 模型价目（缺价目即报错，不允许静默零成本）
  max_tokens: 8192                                 # 单次生成输出预算（入匹配键与成本上界；016 遗留 5 口径）
```

## 状态机

- DevJob：`pending → generated → evaluated → inserted`；失败 → `failed`（成本照计）
- 策略版本：草稿（不入历史）→ 提交（版本化 + 静态检查）→ 回放对比（**最小池门槛**：
  可比对树数 < 下限即拒绝产出报告）→ 采纳 | 拒绝（终态）；终态不可逆
- 部署指针：仅采纳更新（`deployment.dev.current_policy_version`，定点改写、注释保留）
- 升级判据：每次材料生成 = 一次不可变快照（含当时阈值与逐项可评价性）；系统结论字段不可改写，
  人工推翻只追加记录
