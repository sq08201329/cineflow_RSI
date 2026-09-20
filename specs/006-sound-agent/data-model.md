# 数据模型：声音 Agent 闭环（006-sound-agent）

> 存储分层：`sound_gen_jobs` 运营表（可变中间态，迁移 0005）→ 评估完成后节点一次性
> INSERT 进发现树（immutable，001 既有纪律）；音频工件 BLAKE3 内容寻址入对象存储。

## DB：`sound_gen_jobs`（迁移 0005，可变运营表）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| job_id | TEXT PK | round_id 确定性派生 |
| round_id | TEXT NOT NULL | 探索轮次 |
| gen_type | TEXT NOT NULL | `tts` / `sfx` / `music`（成本分账粒度） |
| params_json | TEXT NOT NULL | 规范化生成参数 JSON |
| params_hash | TEXT NOT NULL | 参数规范化 BLAKE3 |
| status | TEXT NOT NULL | pending → rendered → evaluated → inserted / failed |
| estimated_cost_usd | REAL NOT NULL | 申请时预估（预算门禁依据） |
| actual_cost_usd | REAL | 实际扣费 ≤ 预估 |
| artifact_hash | TEXT | 64 位小写 hex，rendered 后填 |
| error | TEXT | failed 时填 |
| created_at | TEXT NOT NULL | ISO8601 UTC |

唯一键 `(round_id, params_hash)`——幂等语义（重复触发撞键后重建首轮结果）。
**本表可变**（状态推进），与发现树 immutable 无冲突（两段式落盘，research 决策 1）。

## 领域模型

- **TimingSheet（时序元数据）**：`utterances: [{text, start_ms, end_ms}]` +
  `effects: [{kind, at_ms}]`；校验：时间不重叠、非负、start < end（上游输入错误执行前拒绝）
- **SoundGenParams**：按类型分型——TTS `{voice, speed_tier, text, seed}`、
  SFX `{kind, density, seed}`、Music `{mood, tempo, duration_s, seed}`；规范化 JSON 相等 =
  回放精确匹配键（002 语义复用）
- **AudioArtifactRef**：artifact_hash / duration_s / sample_rate / channels +
  元数据（声学属性注入标记：loudness_gain、event_times、cer_injected）
- **SoundGenJob**：运营表行的内存形态（可变 dataclass，非 frozen——它是两段式的可变侧）
- **声音节点**：复用 001 `TreeNode` 全字段；prompt = 台词/情绪输入摘要，
  eval_breakdown = 四评估器分量（`rule.*` 门禁 + `proxy.*` 得分，键格式应用层强制）
- **SoundRoundResult**：轮次收口——jobs 汇总、节点清单、成本按类型分账合计

## 评估器组合（evaluator_weights.sound）

```yaml
sound:
  rule.loudness_compliance: gate   # -27 LUFS ±2（对白档；音效档配置分档）
  rule.av_sync: gate               # 事件时间偏差 ≤ av_sync_threshold_ms
  proxy.asr_transcript: 0.5        # 仅 TTS 工件；CER → score
  proxy.emotion_music_match: 0.5   # 仅 music 工件；情绪向量距离 → score
```

类型不适用语义：ASR 不评非 TTS、情绪分类不评非 music——该分量对不适用类型跳过并在
eval_breakdown 注明（权重在合成时按适用分量归一，口径进 composite 版本元信息）。

## 状态机

- SoundGenJob：`pending → rendered → evaluated → inserted`；任一步失败 → `failed`
  （error 落盘，成本照常入账——原则二）
- 轮次：全部 jobs 终态后收口（SoundRoundResult + 节点批量 INSERT）
