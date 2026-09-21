# 契约：模式状态机、影子模式与对照报告

> 对应规格 US2 / FR-004~006。实现：`core/deployment/mode.py`、`shadow.py`。

## C4 模式状态机

```
set_mode(mode, *, by, reason) -> DeployModeState   # 校验合法迁移
mode_state() -> DeployModeState
```

- `manual ⇄ shadow`；`shadow → auto` 需影子期下限满足；`manual → auto` 禁止直连；
  `auto → manual`（抽检否决/漂移/人工）
- 影子计时 = 模式区间累计（切换即暂停/恢复）；变更留痕（人/时间/理由）

### 场景

1. manual → shadow → auto（影子期满足）→ 允许
2. manual → auto 直连 → 拒绝
3. 影子期未满（时长或候选数不足）→ `shadow → auto` 拒绝并注明缺口
4. `recalibration_required=true` → `→ auto` 一律拒绝（即使影子期已满）

## C5 影子事件与对照报告

```
record_shadow_event(candidate, verdict, human_decision) -> ShadowEvent
build_shadow_report(period, cfg) -> ShadowReport   # deployment/shadow/{period}.json
```

- 影子期**不切换指针**（机检）；事件含"若 auto 会放行与否"与同期人工决策的差异分类
  （`sys_pass_human_reject` / `human_pass_sys_block` / `agree` / `no_human_decision`）
- 报告含放行/拦截数、理由分布、差异分类计数、**误入率分子分母与口径说明**、达标标记

### 场景

1. 影子运行 N 轮 → 报告字段齐全；指针变更次数 0
2. 系统会放行但人工拒绝的候选 → 计入 `sys_pass_human_reject`（门槛太松的信号）
3. 误入率可重算（`recompute_misadmission_rate` 从留痕重算 == 报告值）
