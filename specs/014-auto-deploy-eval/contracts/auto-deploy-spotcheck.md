# 契约：自动部署、抽检与回滚

> 对应规格 US3 / FR-007~012。实现：`core/deployment/auto_deploy.py`、`spot_check.py`。

## C6 唯一入口与三种行为（澄清 Q3）

```
evaluate_candidate(agent_id, candidate_version, *, ...) -> (EvidenceSnapshot, GateVerdict, action)
```

- `manual`：只落证据快照（不部署）
- `shadow`：落快照 + 影子事件（不部署）
- `auto`：满足门槛 → 部署；不满足 → 落快照 + 拦截记录
- 触发：做梦轮次收口后调用（一处接线）

### 场景

1. 三种模式下同一入口行为正确（manual/shadow 指针不变）
2. auto + eligible → 部署；auto + blocked → 拦截记录

## C7 自动部署执行

```
auto_deploy(candidate_version, snapshot, cfg) -> AutoDeployEvent
```

- ①证据快照已落 ②**指针一致性检测**（当前指针 == deploys 留痕的最后部署版本；不一致
  → 拒绝 + 告警，防外部绕过）③定点改写部署指针（`core/yaml_edit.upsert_section_entries`）
  ④部署事件留痕（谱系 `source=auto`）；历史节点零修改；同周期多候选按 reward 择一

### 场景

1. 正常部署 → 指针更新 + 事件留痕 + 历史节点零修改
2. 指针与留痕不一致 → 拒绝 + 告警（不部署）
3. 同周期多候选 eligible → 按 reward 择一（其余如实记录）

## C8 渐进抽检

```
open_spot_check(deploy_event, sequence) -> SpotCheckRecord | None
```

- 序号 ≤ `first_n`（默认 5）→ 全量产任务；之后按 `ratio`（默认 1/5）产任务
- 抽检为人工执行（CLI）；长期未执行 → 告警（不自动视为通过）

### 场景

1. 前 5 次部署 → 全部有复核任务（机检）
2. 第 6 次起 → 按比例产任务
3. 长期未复核 → 告警（不自动通过）

## C9 否决回滚（三件事一个逻辑事务）

```
veto_and_rollback(spot_check_id, by, reason) -> RollbackEvent
```

- ①指针切回前一版本 ②模式回 `manual` ③写 `recalibration_required`
- 任一失败 → **显式报错并保持人工审批**（绝不停留在不确定状态）；回滚事件留痕
- 重开 auto 必须重新标定（清标记）+ 重跑影子期

### 场景

1. 否决 → 三件事同时生效（机检三断言）
2. 回滚目标版本工件缺失 → 显式报错 + 模式已回 manual（不静默）
3. 清标记后请求 auto → 仍需影子期满足（不因清标记而跳过影子）

## C10 部署后漂移的回滚评估

```
record_drift_assessment(agent_id, drift_status) -> RollbackEvent（trigger=drift_assessment）
```

- 自动部署后 judge 转 suspect/confirmed_drift → 产生回滚评估记录（**不自动回滚**，
  按 012 处置流程人工定夺）

### 场景

1. 部署后漂移 → 评估记录落盘；指针不动（除非人工处置）
