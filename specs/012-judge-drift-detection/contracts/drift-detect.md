# 契约：漂移检测与指标口径

> 对应规格 US1 / FR-001~003、FR-009~010。实现：`core/calibration/drift_metrics.py`。

## C1 一轮检测

```
detect_drift(agent_id, evaluator_key, period, cfg, data_dir) -> DriftMetrics
```

- 读 010 快照序列 → 滑动窗口基线（最近 N 周期合并分桶，不含当前）→ 当前周期分布
- 指标：PSI（分桶分布距离）+ 分位数偏移（p25/p50/p75/p90 位移向量）
- 判定：PSI > psi_threshold 或 max|分位偏移| > quantile_threshold → `drift`；
  首周期无基线 → `no_baseline`（记基线不告警）；样本 < min_samples → `insufficient`；
  无快照 → `no_data` 注明
- 检测范围：默认仅 `kind=judge`；proxy/rule 经配置纳入（`scope_kinds`）默认关闭

### 场景

1. 稳定分布 → `normal`（不误报）
2. 注入漂移（均值平移/方差展宽/双峰化 ≥3 形态）→ `drift`（100% 超阈）
3. 首周期 → `no_baseline` 记基线注明
4. 样本 < min_samples → `insufficient` 注明（不硬判）
5. 评估器升版 → 新版本独立基线（旧版本数据不混入）
6. proxy/rule 默认关闭 → 不判定（报表注明"非 judge 类未纳入"）

## C2 口径版本化与只读

- 每条检测记录携带 `detector_version = drift_detector@1.0.0+{算法+阈值哈希}`；
  口径变更不回溯改写历史判定
- 检测全程只读：树零写入、评估器零变更、生成/LLM 调用为 0（审计断言）

### 场景

1. 两次检测同输入同口径 → 记录逐字节一致（detector_version 相同）
2. 口径升级 → 新记录带新 detector_version；历史记录不被改写
