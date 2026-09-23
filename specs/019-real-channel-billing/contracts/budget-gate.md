# 契约：前置预算门禁（按环节分档 + 跨进程账本 + 校准先决条件）

> 对应规格 FR-001~005、SC-004/005、US1、澄清第 4 条、原则三新增条款。实现：`core/billing/budget.py`
> （`BudgetConfig` + `BudgetTier` + `FileLedger` + `SpendGuard`）+
> 网关侧的**可选**注入点（`core/llm_gateway/gateway.py`）。现状：网关只累计从不拒绝
> （`gateway.py:100-104` 的 `total_cost_usd`/`call_count`），四个平台阶段各有逐轮预算但 **LLM 腿无上限**。

## C9 分档 schema + 配置声明 + 缺项即拒绝启动

```yaml
budget:
  channels: {<channel_id>: {adapter: …, bill: {format: …, fetch: export|api}}}
  tiers: {"<环节 id>": {limit_usd, window: {kind: run|day|period}, on_exhausted: refuse, note}}
```

- 校验：`limit_usd` > 0（非 bool）；`window.kind ∈ {run, day, period}`；`on_exhausted` **取值域单元素
  `refuse`**（"拒绝而非排队、而非降级为模拟"，规格边界情况）；`note` 缺省可通过但记 notes 告警
  （口径须可追溯，镜像 `profiles.py:238-240` 的 `price_note` 纪律）。任一非法 ⇒ `BudgetConfigError`。
- **配置即权威**：`budget.channels.<channel>`、`budget.tiers.<环节>`、`budget.peak_windows`、
  `budget.calibration`、`budget.reconcile`、`budget.ledger`、`budget.runs` 全量声明；装配某渠道某环节的
  真实调用面时**缺任一键即报错拒绝启动**——不取码内默认、不静默放行（FR-001 / US1 场景 2）。
- **环节归属机制**（本契约判断项）：`chat(..., stage=<环节 id>)` 由调用点声明环节（网关只有 4 个 `role`，
  `judge` 一个角色覆盖四个环节，`role → tier` 映射无法表达"按环节分档"，故必须由调用点声明）。
  断言：全部 `.chat(` 调用点声明 `stage=`，且取值 ∈ 两形态 `budget.tiers` 键集（扩展
  `tests/unit/test_no_vendor_literals.py:86-138` 的调用点断言；计数仍为 8，不新增调用点）。
  注入了 guard 而请求缺 `stage` ⇒ 拒绝（`tier_undeclared`），不静默归入默认档。
- 额度随快照冻结：装配时把生效档位定义 + `peak_windows_snapshot` + `channel_id` + 账本 `revision`
  并入 Agent 的 `config_snapshot["budget_tiers"]`（未接入时**不落键**，镜像
  `profiles.py:527-535` 的 `with_llm_profiles` 口径）——改额度只影响此后新装配，历史节点不变。
- 登记点须同步（新顶层段，共五处），见 [data-model.md](../data-model.md) 首节。

### 场景

1. 缺 `budget` 段 / 缺某环节档 / 缺 `peak_windows.timezone` → 装配报错（不取默认、不静默放行）
2. `on_exhausted: queue` 或 `limit_usd: 0` → 配置报错；`note` 缺失 → notes 告警但可启动
3. 调用点 `stage=` 取值不在档位键集 / 未声明 `stage=` → 静态断言红（运行期亦拒绝）

## C10 前置校验语义 + 拒绝分型 + 0 调用 0 入账

- 注入点：`LLMGateway(backend, price_book, *, …, spend_guard=None)` —— **可选**注入；`None` 时网关
  行为与现状逐字节一致（独立可用、既有用例不变）；`core/llm_gateway` **不 import** `core/billing`。
- **固定调用序列**（顺序即语义）：
  ① 角色路由与取价（`prices_for`，C5 格位）→ ② **本地缓存判定**（命中即返：`cost 0`、零后端调用、
  不占额——零成本故不过门禁，`gateway.py:283-294`）→ ③ **门禁 `check`（预估额 vs 剩余额度）** →
  ④ 后端调用 → ⑤ 入账（`total_cost_usd`/`call_count`/`_breakdown`）→ ⑥ `reservation.settle(actual)`。
- **预估额口径 = 既有成本上界估算**（唯一实现，本特性只调用不另写）：prompt 按 `max(1, len(prompt)//2)`
  token + completion 按 `max_tokens` 满额。**唯一属主 = `core/llm_gateway`**（估算与折算同源的口径单点）；
  **两处收敛目标**：`agents/screenplay/loop.py:281-289`（口径定义，调用点 `:672`）与
  `agents/dev/loop.py:160-171`（同一公式的第二份，口径行 `:167`）——两处都改为薄调用，不得留第二份。
  **不并入**：`core/llm_gateway/backends/mock.py:49`（`prompt_tokens=max(1, len(prompt) // 2)`）是
  **模拟后端的 usage 生成**（用途不同：造伪 usage，不是成本上界预估），并入即语义错位。
  由网关填入 `SpendRequest.estimated_usd`（guard 不自行估算，避免两套口径）；口径写进报告与校准记录
  （**估算额 / 网关记账 / 厂商账单三数分离**）。
- **拒绝 = `BudgetRefusedError`**（`GatewayError` 子类，调用方可统一捕获）；拒绝语义：
  `call_count` 与 `total_cost_usd` 与 `_breakdown` **均不变**、缓存不写、异常上抛 ⇒
  **调用计数 0、成本入账 0**（FR-002 / SC-004 / US1 场景 1）；拒绝原因与预估价落
  `billing/{channel}/alerts.jsonl`（`kind=budget_refused`）+ 账本 `refusals` 计数（US1 场景 1"原因落盘"）。
- **分型互斥**（规格边界情况，对账须可归因）：`budget_refused`（本系统门禁）≠ 认证失败
  （厂商 401/403 → `PermanentBackendError`，`backends/http.py:31`）≠ 配额/限流
  （厂商 429 → `TransientBackendError`，`backends/http.py:29`）——三类各有独立错误类型与告警 `kind`。
- 实测超预估（`settle` 使余量 < 0）：如实入账 + `over_limit=true` 告警 + 后续调用拒绝（不回滚、不改写、
  不静默清零）；**登记边界**：上界估算仍可能被超（prompt 实际 token 高于 `len//2`），故余量可为负。
- 覆盖断言（US1 场景 5"不得绕过"），**两层，缺一不可**（豁免**按规则判定，不用文件白名单**）：
  ① **显式性断言**（必要但不充分——`spend_guard=None` 也算"传了"）：`core/`、`agents/`、`ops/` 内任何
  `LLMGateway(...)` 构造**必须显式传** `spend_guard=`；同函数内构造 `MockBackend`（或测试桩）的离线装配
  ⇒ 必须显式 `spend_guard=None`（登记为豁免），离线装配点清单常驻断言（新增一处即红）。
  ② **保证性断言**（真正的"必先过门禁"）：引用 `HttpBackend` / `_llm_backend(kind="http")` 的**真实渠道
  装配点**必须传**非 `None`** 的 guard——当前**两处**：`agents/pilot/backends.py:210-214`（整条链的唯一
  装配点）与 `ops/smoke_llm.py:193-198`（最小规模真实调用/校准的装配点）；清单常驻断言，且 `core/` 与
  `agents/` 下**不得出现** `spend_guard=None`。该链上"超限即拒"因此是机检事实，而不是从调用点普查
  推断出的结论（实测普查：非测试构造点共 10 处 = 真实 2 处（本契约所列）+ `ops/` 内 mock/桩 8 处
  （5 处显式 `MockBackend()` 演示 + `demo_screenplay_loop.py:271` / `screenplay.py:167` / `dev.py:168`
  的注入后端路径），按①显式登记）。
  `tests/contract/test_billing_contracts.py` 另断言超限调用后端计数 0、`call_count`/`total_cost_usd` 不变。

### 场景

1. 预估额 > 剩余额度 → 后端 0 次调用、`call_count` 0、`total_cost_usd` 0、拒绝原因落盘
2. 本地缓存命中 → 返 `cost 0`、`call_count` 不变、不占额（与厂商缓存维度区分，C5/C7）
3. 认证失败（401）与厂商限流（429）不落 `budget_refused`，各自分型；三类告警 `kind` 可辨
4. 未注入 guard 的网关（既有装配）行为逐字节不变；`core/llm_gateway` 中零 `core.billing` import

## C11 跨进程账本（单主机文件账本）

```
billing/{channel}/ledger.json
  {revision, updated_at, tiers: {<环节>: {window_key, limit_usd, spent_usd, reserved_usd,
   refusals, last_refusal{at,reason,estimated_usd}}}}
```

- 结构：JSON；**额度校验不得只在进程内**（规格边界"多实例并行共享同一额度"）——余量、预留与拒绝计数
  全部落在该文件，`FileLedger` 为唯一读写入口。
- 并发原语：`fcntl.flock(LOCK_EX)` 独占 → 读 → 改 → `os.replace` 原子替换；每写 `revision += 1`
  （单调，供并发无丢失更新的机检）。锁超时（`budget.ledger.lock_timeout_seconds`）⇒ `BudgetLedgerError`
  拒绝调用：**不无锁写、不静默放行**。
- 预留—结算两段（防并发超额）：`check` 时 `reserved += estimated_usd`（同事务判定余量并占用）；
  `settle(actual)` 时 `reserved -= estimated_usd; spent += actual_usd`。进程崩溃残留的未结算预留
  **如实呈现**（CLI `tiers` 列出未结算预留），不得静默清零——处置由运营决定（登记为运维边界）。
- 窗口滚动：`window_key = (kind, 窗口实例)`（`run` 用运行标识、`day` 用本地日期、`period` 用账单周期）；
  键变更即归零计数（旧窗口的记录**保留**在账本历史中，可审计）。
- **登记边界（如实）**：本账本为**单主机多进程安全**；多主机并发需换 PG 行锁/事务（**未做**，
  登记为边界，属后续特性）；不得以本实现声称跨主机安全。

### 场景

1. 两进程并发抢同一档：总入账 ≤ `limit_usd`、`revision` 单调、无丢失更新；预留使并发不超额
2. 锁被占满超时 → 拒绝调用（错误可辨），不写坏账本、不静默放行
3. 崩溃残留预留 → CLI 如实列出（`reserved_usd > 0` 且有未结算标记），不自动清零

## C12 校准记录 = 扩量的先决条件

- `record_calibration(...) -> CalibrationRecord`：字段 = 渠道 / 环节档 / **当时配置价目快照**（含
  `price_matrix` 与 `peak_windows_snapshot`）/ 实测花费与样本量 / `deviation = (实测 − 按价目折算)/按价目折算` /
  `passed`（阈值 `budget.calibration.deviation_tolerance` 与 `min_samples`）/ 口径备注 / `at`；
  **append-only**（同 `(channel_id, calibration_id)` 重产拒绝，镜像 `core/degraded/evidence.py:237-241`）。
  最小规模调用的实际发生与花费来源须可从运行记录与网关账目回溯（不凭报告自证）。
- `raise_tier(channel, tier, limit_usd, *, calibration_id, by, reason)` 拒绝条件（**任一命中即拒绝并留痕**）：
  ① 无 `calibration_id`；② 记录不存在或不同渠道；③ `passed != true`；④ 样本量 < `min_samples`；
  ⑤ 记录超期（`record_ttl_days`）；⑥ `deviation` 超容差 ⇒ SC-005"未校准即扩量恒 0 次"。
- 落地动作 = **定点改写**配置（`core/yaml_edit.py`，注释与其它段逐字节保留；镜像 017 采纳改部署指针
  `deployment.dev.current_policy_version` 的先例）+ 把 `calibration_id` 写进该档的 `calibrated_by` 键，
  同时追加留痕行到 `alerts.jsonl`（`kind=uncalibrated_raise` 或 `tier_raised`）——升级必须可追溯到记录。
- 渠道校准状态由最新记录派生（`untested|pass|fail|stale`）；记录超期 ⇒ 状态 `stale`
  （登记为 `untested` 处理：扩量被拒，理由写明超期）。

### 场景

1. 最小规模调用后产出校准记录（含价目快照/实测花费/偏差/口径备注/样本量/时间），同键重产被拒
2. 无校准记录 / 记录 `passed=false` / 记录超期 ⇒ `raise_tier` 拒绝且留痕（配置**未被改写**）
3. 记录合格 ⇒ 定点改写额度 + `calibrated_by` 留痕；其余段与注释逐字节不变
