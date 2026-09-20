# 数据模型：剧本 Agent 降级模式（009-script-agent-degraded）

> 存储分层：`screenplay_jobs` 运营表（可变中间态，迁移 0008）→ 评估完成后节点一次性
> INSERT（immutable，001 纪律）；剧本工件（含结构化标记）内容寻址入对象存储。

## DB：`screenplay_jobs`（迁移 0008，可变运营表）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| job_id | TEXT PK | round_id 确定性派生 |
| round_id | TEXT NOT NULL | 产出轮次 |
| stage | TEXT NOT NULL | `outline` / `scenes` / `script`（CHECK 枚举） |
| policy_version | TEXT NOT NULL | 人工策略版本（BLAKE3 前 12 位） |
| params_json | TEXT NOT NULL | 规范化输入参数 JSON |
| params_hash | TEXT NOT NULL | 参数哈希 |
| cache_key | TEXT | 网关缓存键（命中即复现） |
| response_hash | TEXT | 网关响应哈希（回放核对） |
| status | TEXT NOT NULL | pending → generated → evaluated → inserted / failed |
| estimated_cost_usd | REAL NOT NULL | 按 token 预估（网关价目） |
| actual_cost_usd | REAL | CHECK (actual ≤ estimated) |
| artifact_hash | TEXT | 64 位小写 hex |
| error | TEXT | failed 时填 |
| created_at | TEXT NOT NULL | ISO8601 UTC |

唯一键 `(round_id, stage, params_hash)`——幂等语义。

## 领域模型

- **ScriptArtifact（剧本工件）**: stage / 文本正文 / **结构化标记**（`beats: [...]`、
  `scenes: [{scene_id, heading, characters, location, time_marker}]`、
  `characters: [{name, aliases}]`、`lines: [{line_id, kind, character, text}]`）/ 内容寻址；
  解析确定性的前提
- **ScreenplayJob**: 运营表行内存形态（可变）
- **HumanPolicyVersion**: version（源码 BLAKE3 前 12 位）/ parent_version / 提交人 /
  提交时间 / 静态检查结果 / source=manual
- **ReplayComparison**: new_version / deployed_version / 逐树得分 / 分项评估器差异 /
  pareto_auc 曲线（复用 dreaming/reward 口径）/ UNKNOWN 说明
- **AdoptionRecord**: comparison_id / 结论（adopt|reject）/ 人 / 时间 / 依据 / 理由
- **UpgradeEvidence**: period / 阈值快照 / judge 信度（相关系数、样本量）/ 漂移指标 /
  门禁违规分布 / 人评锚点计数 / **系统结论（meets|below）** / 推翻记录

## 评估器组合（evaluator_weights.screenplay）

```yaml
screenplay:
  rule.beat_structure: gate          # 三幕/序列结构 + 关键节拍存在性
  rule.page_minutes: gate            # 页数（lines_per_page 换算）∈ 目标 ± 容差
  rule.scene_character: gate         # 场景-角色一致性（无幽灵角色/地点一致）
  rule.dialogue_action_ratio: gate   # 对白/动作行比例 ∈ 配置区间
  proxy.entity_consistency: 0.5      # 角色名规范化匹配 + 别名表
  proxy.timeline_conflict: 0.5       # 时间线单调性冲突
  judge.dramatic_tension: 0.5        # 仅 outline 阶段；其他阶段"不适用"跳过
```

## 状态机

- ScreenplayJob：`pending → generated → evaluated → inserted`；失败 → `failed`（成本照计）
- 策略版本：草稿（不入历史）→ 提交（版本化 + 静态检查）→ 回放对比 → 采纳 | 拒绝（终态）；
  终态不可逆
- 升级判据：每次材料生成 = 一次不可变快照（含当时阈值与结论）
