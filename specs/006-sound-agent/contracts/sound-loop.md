# 契约：声音探索执行器（预算门禁 + 两段式落盘）

> 对应规格 US1 / FR-001~004。实现：`agents/sound/loop.py`、`config.py`、`db.py`。

## C1 一轮探索

```
run_sound_round(engine, store, gateway, policy, inputs, cfg, round_id) -> SoundRoundResult
```

- 输入：TimingSheet + 台词文本 + 情绪基调 + 当前策略（`OptimalPolicy.solve()` 形态）
- 流程：策略产 SoundGenParams 组合 → 预算门禁（申请前校验 + 事务内复核，
  ≤ 语义含最小货币单位）→ 适配器生成（昂贵动作仅此阶段）→ 工件内容寻址 →
  四评估器 → 合成得分（quantize 定点归一）→ 节点一次性 INSERT
- 幂等：job_id/tree_id 由 round_id 确定性派生 + 唯一键 `(round_id, params_hash)`；
  二次触发重建首轮 SoundRoundResult（0 重复生成、0 重复扣费）
- 分账：轮次成本按 gen_type 分组合计（TTS/音效/音乐各自小计 + 总计）

### 场景

1. 策略产 4 组参数（2 TTS + 1 SFX + 1 music）→ 4 工件落树，成本分账齐全
2. 预算上限 $300，申请累计 $310 → 超额 job 拒绝，已执行 $280 照常入账落树
3. 同 round_id 二次触发 → 首轮结果重建，适配器调用计数不增
4. 某 job 渲染失败 → status=failed、error 落盘、预估成本照常入账，轮次继续
5. TimingSheet 时间戳重叠 → 执行前 ValidationError，适配器 0 调用、0 成本

## C2 配置

`agents/sound/config.py` 读 configs `sound` 段：budget、loudness 分档、sync 阈值、
sample_rate、prices（按类型）、simulated_gen 参数；缺价目即报错（不允许静默零成本，
003 同款纪律）。

## C3 运营表

`agents/sound/db.py` 定义 `sound_gen_jobs`（schema 见 data-model.md）；
迁移 `0005_sound_gen_jobs.py`（自包含 DDL + GRANT 纪律，0004 同模式）。
