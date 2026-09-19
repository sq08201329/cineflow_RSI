# 契约：权重再拟合提案与生效门禁

> 对应规格 US3 / FR-007~008。实现：`core/calibration/refit.py`，
> CLI `ops/calibrate.py propose|confirm|shelve`。

## C7 提案生成

```
maybe_propose(agent_id, bias_records, current_weights, cfg) -> WeightProposal | None
```

- 触发：某评估器 |mean_shift| > `calibration.bias_threshold`（默认 0.15）且样本达标
- 候选权重：约束岭回归（research 决策 5）——非负、和为一、向 current_weights 收缩
  （λ_ridge 配置，默认 1.0）；numpy 实现，零新增依赖
- 负相关（pearson_r < 0）→ **禁止**生成提案（规格边界情况：转人工裁决）
- 首轮（无历史台账）→ 记基线，不判超阈

### 场景

1. 超阈偏差 → 产 pending 提案（含 BiasRecord 引用 + current/candidate 权重 + based_version）
2. 未超阈 → 返回 None，仅报告记录偏差
3. 负相关 → 无提案 + 告警

## C8 确认与生效

```
confirm(proposal_id, confirmer) -> new_version
```

- 人工仅可 confirm / shelve，**无编辑候选权重的 CLI 路径**（FR-007）
- 生效（逻辑事务）：① composite 评估器注册新版本
  （version = `{base}+w{weights BLAKE3 前12}`，calibration 填台账最新快照）；
  ② `configs/movie.yaml` `evaluator_weights.{agent_id}` 段**定点改写**（保注释）；
  ③ 提案 → confirmed（confirmed_by/at 落盘）
- 基于过期版本（based_version ≠ 当前部署版本）→ 拒绝，须重新提案
- 生效中途失败 → 提案 → failed，部署指针不切换（回滚语义见 research 决策 6）

### 场景

1. pending 提案 confirm → 新版本注册成功、yaml 权重段更新且注释保留、提案 confirmed
2. shelve → 注册中心与 configs 零变更（机检：前后 spec 集合与文件哈希一致）
3. 过期版本提案 confirm → 拒绝并注明"基于版本已过期"
4. 生效后审计历史节点 → score/eval_breakdown 与落盘值逐字节一致（FR-008）

## C9 一轮完整收口

```
run_round(agent_id, period, cfg) -> CalibrationRound
```

清单 → 录入等待（外部 CLI）→ 配对 → 偏差 → 台账 → 报告 → （可选）提案。
demo：`uv run python ops/demo_calibration.py`（确定性夹具走全流程）。
