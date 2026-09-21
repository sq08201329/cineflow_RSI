# 契约：漂移报表与信度联动

> 对应规格 US3 / FR-007~008、012。实现：`core/calibration/drift_report.py`。

## C6 周期报表

```
build_report(period, cfg, data_dir) -> DriftReport   # calibration/drift/reports/{period}.json
```

- per (agent, evaluator)：指标序列（周期 × DriftMetrics）、基线引用、阈值、当前状态、
  处置记录；JSON 可机读；只读（不修改任何源数据）

### 场景

1. 多评估器多周期 → items 齐全（指标序列/基线/阈值/状态/处置记录）
2. 已处置项 → 状态与处置记录（人/时间/理由）如实呈现
3. 无数据评估器 → 标注"无数据"（不伪造）

## C7 双信号联动（010 信度）

- 读 010 信度报告（per 评估器相关系数与达标标记）
- 规则（配置 `double_signal`）：漂移告警 ∧ 信度低于 target → **强化告警**（级别升级 +
  `double_signal: true` 标注）；单信号 → 常规告警（不误升级别）

### 场景

1. 漂移 ∧ 信度下降 → 强化告警（级别升级 + 双信号标注）
2. 仅漂移（信度正常）/ 仅信度下降（无漂移）→ 常规告警
3. F6 ScoreConflict 存在 → 报表附注（不参与阈值判定）

## C8 只读审计

- 检测/报表全程：树零写入、评估器零变更、生成/LLM 调用计数 0（审计断言，SC-005）
