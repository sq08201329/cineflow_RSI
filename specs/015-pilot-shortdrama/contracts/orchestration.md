# 契约：通用编排执行器（core/orchestration）

> 对应规格 US3 / FR-005~006、FR-011。实现：`core/orchestration/{dag,executor,models,ledger}.py`。

## C1 轻量 DAG

```
build_dag(stages: list[StageSpec]) -> Dag      # 拓扑校验：环检测、依赖存在、唯一 stage_id
dag.topological_order() -> list[str]
```

- **零业务概念**（不含任何形态/环节名——静态断言）：只处理 stage_id、依赖与执行入口引用
- 无外部编排框架依赖（依赖清单机检）

### 场景

1. 合法依赖图 → 拓扑序正确
2. 环依赖 → 拒绝（明确报错）
3. 依赖不存在 / stage_id 重复 → 拒绝

## C2 执行器与状态机

```
run(dag, ctx, *, resume_from=None) -> RunRecord
```

- 逐阶段执行：`pending → running → done | failed`；失败 → 其后阶段 `skipped`
- 每阶段调用业务侧执行入口（执行器不感知环节语义）；各环节既有门禁不被绕过
- 执行器不产生落树路径（复用各 Agent 既有落树）

### 场景

1. 六阶段全成功 → RunRecord done，各阶段产物引用齐备
2. 中途某阶段 failed → 其后 skipped；失败点与原因记录
3. 执行器代码无环节/形态字面量（静态断言）

## C3 断点续跑与输入指纹

```
resume(run_id, *, input_fingerprint) -> RunRecord
```

- `input_fingerprint` = 素材哈希 + 配置哈希；不一致 → 拒绝续跑
- 已完成阶段不重跑（产物引用复用）；从 faileds 阶段继续

### 场景

1. 失败修复后续跑 → 已完成阶段不重跑（机检调用计数）
2. 输入或配置变更后续跑 → 拒绝（指纹不一致）
3. 全部完成后再续跑 → 幂等（无副作用）

## C4 账目汇总

```
summarize_cost(record, agent_ledgers) -> PilotCostLedger
```

- 按 stage 与形态汇总；与各 Agent 落盘成本对账（零差异断言）；对账失败即报错
