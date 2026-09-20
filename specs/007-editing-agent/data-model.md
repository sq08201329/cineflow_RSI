# 数据模型：剪辑 Agent 闭环（007-editing-agent）

> 存储分层：`edit_render_jobs` 运营表（可变中间态，迁移 0006）→ 渲染与评估齐备后节点
> 一次性 INSERT（immutable，001 纪律）；成片 mp4 BLAKE3 内容寻址入对象存储。

## DB：`edit_render_jobs`（迁移 0006，可变运营表）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| job_id | TEXT PK | round_id 确定性派生 |
| round_id | TEXT NOT NULL | 探索轮次 |
| edl_json | TEXT NOT NULL | 规范化 EDL JSON |
| edl_hash | TEXT NOT NULL | EDL 规范化 BLAKE3（回放匹配键） |
| status | TEXT NOT NULL | pending → rendered → evaluated → inserted / failed |
| estimated_cost_usd | REAL NOT NULL | 申请时预估（按成片预估时长 × 价目） |
| actual_cost_usd | REAL | CHECK (actual ≤ estimated) |
| artifact_hash | TEXT | 64 位小写 hex，rendered 后填 |
| error | TEXT | failed 时填 |
| created_at | TEXT NOT NULL | ISO8601 UTC |

唯一键 `(round_id, edl_hash)`——幂等语义。本表可变（状态推进），两段式落盘。

## 领域模型

- **ShotEntry**：shot_id / artifact_hash / duration_ms / scene_id（分区归属）/ metadata
  （视觉评估得分可供策略参考但不入剪辑打分）
- **SceneStructure（场景分区）**：`scenes: [{scene_id, shot_ids: [...]}]` 有序列表；
  校验：shot_ids 无重复、引用存在；EDL 合法性校验的依据
- **EditDecisionList（EDL）**：`clips: [{shot_id, in_ms, out_ms, transition: {type, duration_ms}}]`
  有序 + `audio: [{track_ref, at_ms, gain}]` 可选；规范化 JSON（键排序）即回放匹配键
- **PacingBaseline（节奏基准）**：配置形态——`{segments: [{span: [起, 止), mean_ms, var_ms, weight}], d_cap}`；
  成片按相对位置分段统计镜头时长均值/方差，加权欧氏距离映射得分
- **EditRenderJob**：运营表行的内存形态（可变 dataclass——两段式可变侧）
- **FilmArtifact**：artifact_hash / duration_ms / shot_count / has_audio
- **剪辑节点**：复用 001 TreeNode；prompt = 镜头库/分区/音轨输入摘要，
  eval_breakdown = 五评估器分量

## 评估器组合（evaluator_weights.editing）

```yaml
editing:
  rule.duration_compliance: gate   # 总时长 ∈ target ± tolerance
  rule.shot_distribution: gate     # 镜头时长 ∈ [min_shot_ms, max_shot_ms]
  rule.transition_rules: gate      # 转场规则库合法性（与执行前校验同库）
  proxy.pacing_curve: 0.6          # 分段统计 vs PacingBaseline 距离
  judge.narrative_flow: 0.4        # EDL 摘要成对比较，3 提示词 × 冻结锚点 EDL 集
```

## 状态机

- EditRenderJob：`pending → rendered → evaluated → inserted`；任一步失败 → `failed`
  （成本照常入账——原则二）
- 轮次：全部 jobs 终态后收口（EditingRoundResult + 节点批量 INSERT）
