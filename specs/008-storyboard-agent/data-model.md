# 数据模型：分镜 Agent 闭环（008-storyboard-agent）

> 存储分层：`storyboard_render_jobs` 运营表（可变中间态，迁移 0007）→ 渲染与评估齐备后
> 节点一次性 INSERT（immutable，001 纪律）；预演 mp4 BLAKE3 内容寻址入对象存储。

## DB：`storyboard_render_jobs`（迁移 0007，可变运营表）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| job_id | TEXT PK | round_id 确定性派生 |
| round_id | TEXT NOT NULL | 探索轮次 |
| shotlist_json | TEXT NOT NULL | 规范化 ShotList JSON |
| shotlist_hash | TEXT NOT NULL | 规范化 BLAKE3（回放匹配键） |
| status | TEXT NOT NULL | pending → rendered → evaluated → inserted / failed |
| estimated_cost_usd | REAL NOT NULL | 按镜头数 × 价目预估 |
| actual_cost_usd | REAL | CHECK (actual ≤ estimated) |
| artifact_hash | TEXT | 64 位小写 hex，rendered 后填 |
| error | TEXT | failed 时填 |
| created_at | TEXT NOT NULL | ISO8601 UTC |

唯一键 `(round_id, shotlist_hash)`——幂等语义；两段式可变侧。

## 领域模型

- **ScriptSegment（剧本段落）**：`scenes: [{scene_id, lines: [{line_id, kind: dialogue|action,
  text, key: bool, emotion}], axis_base}]`；校验：行 id 唯一、场景有序、关键行标注合法
- **ShotEntry（分镜镜头）**：shot_id / scene_id / covers: [line_id] / shot_size（景别档位，
  配置枚举）/ camera（机位档位 + side: A|B）/ movement（运动档位）/ est_duration_ms /
  alternatives（备选数，下游视觉线参数）
- **ShotList（分镜脚本）**：`shots: [ShotEntry]` 有序 + `schema_version`；规范化 JSON
  （键排序）即 `shotlist_hash`
- **ShotGrammarRules（镜头语法规则库）**：配置形态——景别枚举与序（如 特写<近景<中景<全景）、
  相邻跳跃上限、同景别连续上限、轴规则参数
- **StoryboardRenderJob**：运营表行内存形态（可变）
- **AnimaticArtifact**：artifact_hash / duration_ms / shot_count / has_temp_audio
- **StoryboardPolicy**：被进化对象——镜头语言策略（景别节奏/备选数，OptimalPolicy.solve() 形态）

## 评估器组合（evaluator_weights.storyboard）

```yaml
storyboard:
  rule.shot_grammar: gate          # 景别跳跃上限 / 同景别连续上限
  rule.coverage: gate              # 场景级 + 必覆盖清单（key 行逐条承接）
  rule.axis_rule: gate             # 轴规则（侧别跳变需过渡镜头）
  proxy.emotion_alignment: 0.5     # 预演画面帧特征 vs 情绪基调向量（余弦）
  judge.script_fit: 0.5            # ShotList 摘要 + 剧本段落成对比较（三段哈希版本）
```

## 状态机

- StoryboardRenderJob：`pending → rendered → evaluated → inserted`；失败 → `failed`
  （成本照常入账——原则二）
- 轮次：全部 jobs 终态后收口（StoryboardRoundResult + 节点批量 INSERT）
