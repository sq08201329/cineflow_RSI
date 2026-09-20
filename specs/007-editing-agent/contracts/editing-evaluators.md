# 契约：剪辑五评估器与合成评分

> 对应规格 US2 / FR-006~009。实现：`agents/editing/evaluators/`。

## C4 rule.duration_compliance（gate）

成片总时长 ∈ `target_duration_s ± duration_tolerance_s` 通过，否则 gate 判 0。

## C5 rule.shot_distribution（gate）

逐镜头时长 ∈ [`min_shot_ms`, `max_shot_ms`]（配置）；任一越界 → gate 判 0
（防碎片化与拖沓）。

## C6 rule.transition_rules（gate）

EDL 转场序列经规则库复核（与执行前校验同一配置库——单一事实源）；违规 → gate 判 0。

## C7 proxy.pacing_curve（连续）

- 成片镜头时长序列按相对位置分段（PacingBaseline.segments 的 span）统计均值/方差
- 与基准段的加权欧氏距离 d → `score = 1 − min(1, d / d_cap)`，定点 6 位小数
- 基准缺失 → 拒绝启动并报错（配置纪律）；贴近 vs 背离基准的两组分差显著（验收场景 4）

## C8 judge.narrative_flow（连续，澄清 Q1 口径）

- 输入 = `summary.py` 的 EDL 结构化文本摘要（逐镜：序号/时长/转场/分区/音轨标记）
- 3 固定提示词 × 冻结锚点 EDL 集（configs 内嵌，经同一摘要函数）成对比较投票 → 胜率
- 平局（胜率 0.5）如实记录不二次裁决；LLM 全过网关计费（Mock 后端确定性）
- **版本号 = `1.0.0+j{提示词哈希前8}{锚点集前8}{摘要函数前8}`**——三者任一变更 = 新版本

## C9 合成与版本

- 合成 = 三 gate + proxy/judge 加权（权重形态配置）+ quantize 6 位定点；
  gate 短路不跑 judge（省 LLM 成本，004 同款纪律）
- 全部评估器 deterministic=True、实现哈希入版本号；同成片重评估逐位一致（SC-004）

### 场景（每评估器 ≥2 条）

1. 时长 130s（目标 120±10）→ 过；150s → gate 判 0 总分 0
2. 含 300ms 镜头（min 500）→ 分布 gate 判 0
3. 非法转场组合 → 转场 gate 判 0；合法 → 过
4. 贴近基准 vs 背离基准两组 → 分差显著，距离口径误差 < 1e-6
5. judge 同比较重跑 → 逐位一致；版本号含三段哈希
6. gate 违规节点 → judge 未被调用（网关调用计数不增）
