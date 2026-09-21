# 数据模型：judge 漂移自动检测（012-judge-drift-detection）

> 无新 DB 表——数据源是 010 的文件化快照/台账/信度报告；漂移产物全部文件化
> （`calibration/drift/`，git 版本化，只增不改惯例）。frozen dataclass 为第一道工序。

## 领域模型（`core/calibration/`）

- **DriftBaseline**：evaluator_key（`id@version`）/ agent_id / **滑动窗口**（最近 N 周期、
  不含当前）/ 窗口内分桶合并分布（buckets: [float]）/ 分位数（p25/p50/p75/p90）/
  样本量 / 口径版本 / 周期区间
- **DriftMetrics**：evaluator_key / period / psi（分布距离）/ quantile_shifts（分位点
  位移向量）/ samples / verdict（`drift` / `normal` / `insufficient` / `no_baseline`）/
  baseline_ref / detector_version（口径版本）
- **DriftStatus**：evaluator_key / status（`normal`/`suspect`/`confirmed_drift`/`false_alarm`）/
  since / trigger_metrics 引用 / disposition 引用（可空）；系统只写 `suspect`
- **DriftDisposition**：evaluator_key / conclusion（`confirmed_drift` | `false_alarm`）/
  by / at / reason / action（停用 / 换锚点升版 / 恢复）；不可改写（只增不改留痕）
- **DriftReport**：period / items（per agent+evaluator：指标序列/基线/阈值/状态/处置记录/
  alerts）/ double_signal_rules / generated_at / detector_version
- **DeployEvidenceVerdict**：evaluator_key / allow: bool / reason——F9 证据接口返回形态

## 文件 schema

- **检测记录** `calibration/drift/metrics/{agent}/{evaluator_id}/{period}.json`：
  一份 DriftMetrics JSON（含 verdict 与 detector_version）
- **状态 registry** `calibration/drift/status/{evaluator_key_sanitized}.json`：
  当前态 + 历史（只增不改）
- **处置留痕** `calibration/drift/dispositions/{evaluator_key_sanitized}/{timestamp}.json`：
  一份 DriftDisposition JSON
- **报表** `calibration/drift/reports/{period}.json`：DriftReport JSON

## 状态机

- DriftStatus：`normal → suspect`（系统判定超阈写入）→ `confirmed_drift`（人工确认）|
  `false_alarm`（人工误报）→ 恢复 `normal`（处置动作）；终态仅人工写入
- 评估器升版 → 新版本的状态独立（默认 normal，无历史沿用）

## 与 010 的关系

- **只读消费** 010 产物：`calibration/snapshots/...`（分布）、`calibration/ledger/...`（偏差）、
  `calibration/reports/...`（信度）；本特性不产生采集，也不修改 010 的文件
