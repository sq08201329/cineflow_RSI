# 契约：声音四评估器与合成评分

> 对应规格 US2 / FR-005~007。实现：`agents/sound/evaluators/`。

## C4 rule.loudness_compliance（gate）

- 对白档：积分响度 ∈ -27 LUFS ±2 通过，否则 gate 判 0；音效档：配置分档
  （`loudness.sfx_target_lufs/tolerance`）；纯 music 段按 music 档
- 静音/极短音频 → "不适用"注明，不伪造得分（规格边界情况）
- 测量 = 纯 numpy 简化 BS.1770（research 决策 3），输出定点 6 位小数

## C5 rule.av_sync（gate）

- 工件元数据事件时间 vs TimingSheet：max |偏差| ≤ `av_sync_threshold_ms`（默认 120）通过，
  否则 gate 判 0；无事件工件（纯音乐垫底）→ "不适用"注明
- 不含波形-画面对齐（澄清 Q1）

## C6 proxy.asr_transcript（连续，仅 TTS）

- 模拟路径：工件元数据 cer_injected（种子决定）→ 确定性"转写"复现 → CER 与台词比对
  → score = 1 − min(1, CER / cer_cap)（cer_cap 配置）
- 真实路径：HTTP ASR 骨架（无凭证跳过）；低置信（静音/过短）diagnostics 标注

## C7 proxy.emotion_music_match（连续，仅 music）

- 配乐谐波特征向量（参数种子决定的调式/速度/音色）vs 情绪基调标签向量的余弦距离
  → score；距离超校准带 → diagnostics 标低置信（不进盲评 top-k，规格边界情况）

## C8 合成与版本

- 合成 = 双 gate + 适用 proxy 加权（权重形态配置，不适用分量跳过并归一，口径进版本元信息）；
  quantize 6 位小数定点归一（复用 core/evaluators/quantize.py）
- 全部评估器 `deterministic=True`、实现哈希入版本号（004 `_versioning.py` 同款）；
  同工件重评估得分逐位一致（SC-004）

### 场景（每评估器 ≥2 条）

1. 响度 -27.5 LUFS 对白 → 通过；-20 LUFS → gate 判 0，总分 0
2. 事件偏差 80ms → 通过；200ms → gate 判 0（阈值 120）
3. cer_injected=0.1 → score 与映射口径误差 < 1e-6；两次评估逐位一致
4. 情绪匹配 vs 背离两组参数 → 前者 score 显著高于后者；低置信标注存在
5. 非 TTS 工件 → ASR 分量跳过注明，合成按适用分量归一
