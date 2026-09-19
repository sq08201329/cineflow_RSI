# 契约：偏差计算、台账与信度报告

> 对应规格 US2 / FR-004~006、009。实现：`core/calibration/pairing.py`、`bias.py`、
> `ledger.py`、`report.py`。

## C4 配对

```
pair_anchors(anchors, tree_store, exclusions) -> list[PairingRecord]
```

- 对每个锚点：取节点 `eval_breakdown`，逐评估器分量配对（锚点得分 × 分量得分）
- **自循环剔除**：`exclusions` 来自配置 `calibration.self_pairing_exclusions`
  （`platform_truth → [human.platform_metrics]`）；命中时该分量不入配对，
  PairingRecord 注明剔除（FR-004）
- eval_breakdown 缺某评估器 → 该评估器跳过并注明

### 场景

1. promo 平台真值锚点 × 节点（含 human.platform_metrics 分量）→ 该分量被剔除，
   proxy.ctr_history 正常配对
2. visual 人评锚点（无排除项）→ 全量自动评估器配对

## C5 偏差计算

```
compute_bias(pairs, min_samples) -> BiasRecord
```

- 连续分量：mean_shift = mean(anchor − auto)；pearson_r（样本 ≥ min_samples 才产出）
- judge 类：kendall_tau（锚点排名 × judge 胜率排名，复用 002 τ-b 实现）——
  **禁止**胜率与分数直接相减
- 样本 < min_samples（默认 3）→ 备注"样本不足"，不产偏差值、不参与超阈判定

### 场景

1. 注入已知偏移 +0.2 的数据集 → mean_shift = 0.2 ± 1e-6，pearson_r 与参考实现一致
2. 注入完全背离数据 → pearson_r < 0 → 报告产告警条目，**不**生成权重提案
3. 2 个样本（min=3）→ BiasRecord 备注"样本不足"，偏差字段为空

## C6 台账与报告

```
append_ledger(records) -> None          # calibration/ledger/{agent}/{evaluator}.jsonl
build_report(period, target) -> dict    # calibration/reports/{period}.json
```

- 台账 append-only：每轮每评估器一行 BiasRecord JSON；既有行永不修改
- 报告：per agent per evaluator 的 pearson_r（或 tau）、samples、meets_target；
  负相关入 alerts；target 来自配置 `calibration.reliability_target`（默认 0.6）
- **版本不变断言**：本轮台账追加前后，注册中心全部 spec 的 `(key, calibration)`
  集合不变（契约测试）

### 场景

1. 两轮追加 → 台账 2 行，首轮行逐字节不变
2. 报告 JSON 含 target 与全部四要素字段（schema 断言）
