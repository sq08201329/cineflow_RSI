# 契约：通用件落点（`core/billing/`）、账单规范化与 append-only 纪律

> 对应规格 FR-001/006/009/014、SC-006/007、US2。实现：新包 `core/billing/`（业务无关）+ 薄装配
> （各 Agent/`ops` 只注入渠道 id、环节 id、配置与路径）。磁盘布局见 [data-model.md](../data-model.md)。

## C1 包落点与业务无关性

```
core/billing/budget.py       BudgetTier / BudgetConfig.from_dict / from_yaml（`budget:` 段解析，缺项即报错）
                             / FileLedger（flock + 原子替换 + 窗口滚动与余量）
                             / SpendGuard.check(request) -> Reservation、Reservation.settle(actual_usd)
core/billing/bill.py         normalize_bill(raw, *, channel_id, bill_id, period, currency, source, fmt,
                                             columns, importer=None) -> VendorBill
                             register_importer(format_id, parser) / importer_for(format_id) / load_bill(...)
core/billing/reconcile.py    reconcile(period, *, channel_id, gateway_ledger, bill, cfg, ...) -> ReconciliationReport
core/billing/calibration.py  record_calibration(...) -> CalibrationRecord / load_calibration / require_calibration
core/billing/runlog.py       append_run(channel_id, *, moment, stage, source, ...) / load_run(date)
                             window_coverage(channel_id, *, end, min_window_days, gap_tolerance_days) -> Coverage
```

- **五模块**（与 plan.md 项目结构逐字一致）：守卫与 `budget:` 段解析并入 `budget.py`，不新增第六/第七个模块。
- 零业务概念：只认识 `channel_id` / 环节 id / 金额 / 时刻 / 路径——**渠道 id、环节 id、账单格式 id、
  档案 id、端点全作参数或配置值传入**，包内零字面量、零 `channel_id == …` 分支（参数化，镜像
  `core/degraded/` 的 `agent_id` 纪律）。
- 依赖方向（原则五）：`core/billing → core/llm_gateway`（**单向**，取 `SpendRequest`/`GatewayError` 类型）；
  `core/llm_gateway` 对 `core/billing` **零 import**（守卫以 `guard.check(request)` 结构化调用，`spend_guard=None`
  时网关行为逐字节不变 → 网关独立可用）；不 import `agents.*` / `dreaming.*`。
- 机检 `tests/unit/test_billing_core_purity.py`（**新增**，镜像 `tests/unit/test_dev_core_degraded_purity.py:1-15` 的双层扫描）：
  ① 无形态字面量/分支（同 `tests/unit/test_form_switch.py:293-294` 口径）；② 无厂商词与配置声明的
  档案 id/端点（同 `tests/unit/test_no_vendor_literals.py:39-52` 的反向扫描法）；③ **无配置声明的渠道 id、
  环节 id 与账单格式 id**（反向扫描 `budget.channels` 键、`budget.tiers` 键、`budget.*.bill.format`）——
  白名单**显式钉死**：`csv_lines` / `json_lines`（C2 的注册表内置通用格式键）是 `core/billing/` 内
  **唯一**允许出现的格式字面量，白名单本身常驻断言（新增一条即红）；④ 无 `from agents.` / `import agents`
  （AST + 文本）。断言按此可直接照写：扫 `core/billing/**/*.py` 文本，命中任一被禁字面量即红。
- **不新增 `.chat(` 调用点**（门禁在网关内）：`tests/unit/test_no_vendor_literals.py:103` 的计数断言保持 8；
  但**新增**同处断言"每个调用点声明 `stage=`（环节 id）"且取值 ∈ 两形态 `budget.tiers` 键集（见 C9/C10）。
  该 8 处**就是该测试扫描域的 8 处**——扫描域 = `core/`（除 `core/llm_gateway/`）/ `agents/` / `dreaming/`，
  故**含 `dreaming/candidates.py:79`**、**不含 `ops/smoke_llm.py:215`**（后者在 `ops/` 下，属 C10 ②
  的另一条装配面断言，不并入本条计数）。

### 场景

1. 扫描 `core/billing/**/*.py`：零渠道/格式/环节/厂商字面量、零反向依赖 → 全绿；`spend_guard=None` 时
   网关既有用例逐条不变（独立可用）
2. 同一份 `bill.py` / `runlog.py`（守卫与账本在 `budget.py`）以不同 `channel_id` 与 `tier_id` 调用 →
   各落各自目录，无第二份实现

## C2 账单规范化 + 导入器注册表 + 批次幂等

```
BillImporter: format_id: str; parse(raw: str | bytes, *, columns: Mapping[str, str]) -> Sequence[BillEntry]
```

- **不假设统一格式**（规格澄清第 6 条）：格式 id 由配置声明（`budget.channels.<id>.bill.format`），
  注册表 `register_importer` 由装配方注册；**内置通用键仅两个**：`csv_lines` / `json_lines`
  （`core/billing/` 内唯一允许的格式字面量，C1③）；未注册的格式 id ⇒ `UnknownBillFormatError`
  ——**报错而非静默跳过**（US2 场景 5 / SC-007）。
- **全成或全败**：解析中任一行/任一必填字段非法 ⇒ 整体拒绝、**零落盘**；不得"部分导入后谎报成功"。
  列映射由配置声明（`columns`），缺列/多列口径不明即报错。
- **分类驱动列必须声明**（规格边界情况的落地条件）：映射**必填** `entry_id` / `amount` / `currency` /
  `period` / `line_kind`（行类型）/ `amount_sign`（符号口径）；账单币种 ≠ 记账币种时**条件必填**
  `fx_rate` / `fx_source` / `fx_at`。任一缺失 ⇒ **导入报错**（未声明的 `columns` 键即拒绝）——
  **禁止**改用金额阈值或符号猜测推断分类（C13 的分类输入面就是这几列，实现不得自创启发式）。
- 条目必填：`entry_id`（账单内唯一）、`amount`（非负数值，非 bool——镜像 `core/platform_http.py:227`）、
  `currency`、`period`、`model_ref`（可空）、`note`（计费口径备注）。
- **批次幂等**：身份 = `(channel_id, bill_id)`；同批次已存在 ⇒ 拒绝（append-only，US2 场景 1）。
- 来源留痕：`source ∈ {export, api}`（不假设获取形态，规格开放问题 2 留运营裁决；两种来源在本契约下同构）、
  `raw_ref`（原始行摘要/文件指纹）、`fetched_at` 全落盘。
- 币种与汇率：`currency` 与记账币种不一致时**必须**记录 `fx{rate, source, at}`，缺来源即报错
  （规格边界情况）；本特性不做汇率引擎（规格假设）。

### 场景

1. 夹具账单（含口径差/缺失条目/时序错位/折扣/异币种）→ 条目字段齐备、来源与批次留痕齐全
2. 重复导入同一 `bill_id` → 拒绝；未注册格式 → 报错且**零落盘**（无部分导入）
3. 非负校验：`amount` 为负数或 bool → 拒绝；异币种缺 `fx` → 报错

## C3 全量 append-only + 摘要机检

- **一次性快照**（`bills/`、`reports/`、`calibrations/`）：路径已存在即拒绝重产（镜像
  `core/degraded/evidence.py:237-241`）；系统字段 `system_digest = BLAKE3(canonical JSON of 系统字段)`
  机检（镜像 `:33-44`/`:108-113`）；人工批注（`overrides`/`annotations`）**只追加**、系统字段逐字节不变
  ——写入"无未解释项"、省略某未解释项、改写系统字段 ⇒ **拒绝**（SC-006、US2 场景 4）。
- **运行记录**（`runs/{date}.json`）：`entries` 追加只增；`head_digest` = BLAKE3(前一条 `head_digest` +
  本条 canonical JSON) 链式摘要（改写/删除任一条即断链 ⇒ 读取报错，不静默取）；当日 `sealed` 后追加拒绝。
- **跨进程账本**（`ledger.json`）为**可变状态**，但改写只经 C11 的 flock + 原子替换，且带单调 `revision`;
  花费与拒绝的**事实**另经只增的 `alerts.jsonl` 落痕（可变余量 + 只增事实双轨，审计不依赖内存）。
- 报告必须引用账单批次与来源（`bill_refs`）；**网关记账禁止作"成本已核实"的唯一依据**（原则三 /
  FR-008）——缺 `bill_refs` 的报告一律拒绝产出。

### 场景

1. 改写报告任一系统字段或省略未解释项 → 机检失败并拒绝写回
2. 篡改运行记录中任一条 entry / 删条目 → 链校验失败并报错
3. 报告无 `bill_refs` → 拒绝产出（网关记账不得自证）

## C4 磁盘布局

```
billing/{channel}/bills/{bill_id}.json / reports/{period}.json / calibrations/{calibration_id}.json
billing/{channel}/runs/{date}.json / ledger.json / alerts.jsonl
```

- 根目录可配（`budget.ledger.root`，默认仓库根 `billing/`）；**按渠道分目录**，单渠道的产物不跨目录写；
  缺失目录自动创建（与 `pilot/runs`、`deployment/` 同级，属运行期产物目录）。
- 命名口径：`{bill_id}`/`{period}`/`{calibration_id}` 取自账单与配置声明值；`{date}` = `YYYY-MM-DD`
  （按 `peak_windows.timezone` 的本地日期）。
- **日历耦合（明确登记，非疏漏）**：`peak_windows.timezone` 是**渠道日历的唯一来源**——运行记录 `{date}`、
  额度 `window.kind == day` 与峰谷判定**共用**它。故**不需要峰谷定价**的渠道仍须声明 `peak_windows`
  （以 `windows: []` 显式声明"全谷时"），实际只写一个 `timezone`；理由：三处日期口径若各配一份，
  必然出现 UTC/本地混用与"同一天算两个日"的分叉——口径必须单点。**不引入第二个时区配置键**。
- **禁止**写入其它特性目录与仓库权威配置（除 C12 的显式定点改写）；Web 只读不变（原则五）。
- 机检：`tests/unit/test_billing_paths.py` 断言路径拼接口径（渠道子目录、文件名派生、同键拒重产），
  且产物不落在 `pilot/`、`deployment/`、`calibration/` 等既有目录内。

### 场景

1. 两个渠道同周期各产一份报告/账本，互不覆盖；同键重产被拒
2. 账本与告警落 `billing/{channel}/` 下，既有产物目录零新增文件
