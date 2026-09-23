# 契约：账单对账、告警门禁、运行记录与 CLI 面

> 对应规格 FR-006~008、012~013、015、SC-001/002/007/009、US2/US3。实现：`core/billing/{bill,reconcile,runlog}.py`
> + `ops/billing.py`（薄 CLI）+ `ops/demo_billing.py`（离线演示）。

## C13 对账分类完备性 + 未解释即告警

- 输入两侧：**网关账目**（`gateway.cost_report()` / `cost_breakdown()`，`gateway.py:188-247`——**内部口径**）
  与**厂商账单批次**（必须引用 `bill_id` + `source`）；报告必须含 `bill_refs[]`：
  网关记账**禁止**作"成本已核实"的唯一依据（FR-008 / 原则三）。
- 配对口径：按 `(档案 id, 周期)` 聚合逐项比对；金额以账单币种为准（异币种先按 C2 的 `fx` 折算）。
- **分类枚举固定六类、不增不减**：`计费口径` / `未入账` / `时序错位` / `免费额度与折扣` /
  `币种汇率` / `未结账`；条款来源 = 规格 FR-007 六类 + 边界情况（迟到与周期错位按"未结账"留待下期、
  免费额度与折扣不得静默按 0、汇率必须记来源与时点）。
- **分类的输入面 = 账单声明的驱动列，不是金额启发式**：分类**必须**由账单列映射声明并读入的
  `line_kind`（行类型）与 `amount_sign`（符号口径）判定（异币种渠道另需 `fx_rate`/`fx_source`/`fx_at`）；
  这些列**未声明即导入报错**（C2），**禁止**改用金额阈值或符号猜测推断分类。`line_kind` → 六类的映射
  由配置声明；映射不到的取值 ⇒ `unclassified` ⇒ 告警（同下条）。
- 每一条差异**必须**带 `classification` + `delta_usd`（实测偏差）+ `note`（口径备注）；缺失或取值域外
  ⇒ 记作 `unclassified` ⇒ `unexplained=true` ⇒ **告警**（"无分类即视为不可解释"，US2 场景 2/3）。
  `|delta| ≤ budget.reconcile.amount_tolerance_usd` 视为零差异，但**仍须分类与备注**（零差异不等于免分类）。
- 阈值与告警：超 `alert_threshold_usd` 或 `unexplained` 非空 ⇒ `alerts[]` 非空 + 落 `alerts.jsonl`
  （`kind ∈ {unexplained_delta, delta_over_threshold}`）；`unexplained_alert` 为真时**不得**关闭该行为。
- 时序错位/未结账不得据此判定"网关记账有误"（规格边界情况：留待下期对账）；报告文本与字段须写明。

### 场景

1. 夹具账单（刻意构造六类差异各若干）→ 逐项分类正确、偏差与备注齐备；`unexplained` 为空时零告警
2. 一条差异无分类 → `unexplained` 含该条 + 告警落盘（100%）
3. 免费额度条目不按 0 记账；异币种缺 `fx` 报错；账单迟到 → 分类 `未结账` 并注明"留待下期"

## C14 预算与账单差异告警门禁（三期立项书 §4 新增门禁）

- **运维产物**：`billing/{channel}/alerts.jsonl`（只追加；字段 `kind` / `at` / `channel_id` / `period` /
  `detail` / `ref`；`kind` 取值域 = `budget_refused` / `over_limit` / `unexplained_delta` /
  `delta_over_threshold` / `tier_raised` / `uncalibrated_raise`）——镜像既有留痕先例
  `core/deployment/auto_deploy.py:44` 的 `alerts.jsonl`（防绕过事件必须可追溯）。
- **机检**（两条腿，均常驻）：
  ① `uv run python ops/billing.py alert-check --channel <id>`（只读既有产物：报告未解释项 + `alerts.jsonl`
  增量）与 `ops/billing.py reconcile` 的退出码语义（0 无告警 / 1 有告警 / 2 用法或配置错误）——
  先例 `ops/cost_regression.py:5`（同一"告警即非零退出"口径）；定时执行形态对齐
  `.github/workflows/cost_regression.yml:8-10`（cron 错峰 + `workflow_dispatch`，工作流文件本体由 plan 定义）；
  ② `tests/contract/test_billing_contracts.py` 中的契约用例（跑 CI 的 `uv run pytest tests/contract`）：
  报告含未解释项却返回 0 / 省略未解释项 / 缺 `alerts` 落盘 ⇒ 红。
- 该机检**零真实调用、只读既有产物**，可每日跑（与 `ops/cost_regression.py` 同一定位），
  使"预算与账单差异告警"从叙述变为常驻门禁。
- 不得以"无账单"绕过：无账单批次时报告**拒绝产出**（`ReconciliationError`，退出码 2/1），
  不得产"零差异"报告（否则门禁形同虚设）。

### 场景

1. 有未解释项 → CLI 退出码 1 + `alerts.jsonl` 追加；无告警 → 退出码 0
2. 手工把未解释项从报告中省略 → 契约用例红（系统字段 digest 与告警一致性双检）
3. 无账单即对账 → 拒绝并报错（不产"零差异"报告）

## C15 渠道运行记录 + ≥7 天窗口机检

- 粒度：每次真实调用一条 entry（`at` / `stage` / `source ∈ {real, simulated, fallback}` / `adapter_ref` /
  `profile_id` / `result` / `cost_source` / `fallback_reason`）；**真实渠道失败禁止静默回落模拟并照常计费**
  ——回落必须显式声明 `fallback=true` + 原因 + 来源标注（FR-013）。
- 日期归属 = `peak_windows.timezone` 的本地日期（与额度时间窗同一日历）；同日文件追加（C3 链式摘要）；
  `sealed` 后当日追加拒绝。
- `window_coverage(channel_id, *, end, min_days, gap_tolerance_days) -> {covered_days, gaps[], max_gap_days,
  continuous, meets}`：`covered_days` **只计 `source=real` 的日期**（回落日不算真实运行日）；`gaps` 为缺失
  日期区间（含天数）、`max_gap_days` = 最长断档天数；**如实列出、禁止插值补齐**（FR-012）。
- **判定规则（本契约精确口径，收紧 FR-012/SC-001）**：`meets = covered_days ≥ min_days` **∧**
  `max_gap_days ≤ gap_tolerance_days`——**覆盖与连续双条件**，两项均由配置声明
  （`budget.runs.min_window_days` / `budget.runs.gap_tolerance_days`，后者 0 = 不容断档）。
  **只累计够天数但有断档 ⇒ 恒不通过**（假绿必须被拒）；任一不满足时同时给出**未达标归因**：
  `covered_days` 与 `min_days` 的差值、`max_gap_days` 与容差的差值，以及逐段 `gaps`——
  `covered_days`/`continuous`/`gaps` 三项**照旧落在产物里**（通过时也可见），失败可归因、缺口不被隐藏。
- 术语分辨：`continuous = not gaps`（**有无断档**，与容差无关）；`meets` 才是**带容差的判定**。容差放开时
  `meets=true` 与 `continuous=false` 可**同时出现**——产物并列呈现两者，通过不谎报为"连续"。
- 记录写入点与网关调用同源（一次真实调用 = 一条 entry），`cost_source` 标注金额来源（网关记账值 /
  实测回填），使"花费"可回溯（不凭报告自证）。

### 场景

1. **连续夹具**（窗口内逐日有 `source=real`）→ `meets=true`、`gaps=[]`、`max_gap_days=0`
2. **散点夹具**（8 天真实运行但中间缺 2 天，容差 0）→ 即使 `covered_days=8 ≥ min_days` 仍 `meets=false`
   （`max_gap_days=2 > 0`）；断档区间如实呈现在 `gaps`，文件内**无插值条目**
3. 容差放开（`gap_tolerance_days=2`）→ 同一散点夹具 `meets=true`，而 `gaps` 与 `max_gap_days` **照旧可见**
   （通过不隐藏缺口）；仅 5 天 → `meets=false` 且给出覆盖差值
4. 真实失败回落模拟 → entry 标 `source=fallback` + 原因，不计入 `covered_days`

## C16 CLI / 演示面与退出码

```
uv run python ops/billing.py tiers     --channel <id> [--config configs/*.yaml]
uv run python ops/billing.py calibrate --channel <id> --tier <环节> [--profile <档案 id>] [--config …]
uv run python ops/billing.py import-bill --channel <id> --file <账单文件> --bill-id <批次> --period <周期>
uv run python ops/billing.py reconcile --channel <id> --period <周期> [--config …]
uv run python ops/billing.py alert-check --channel <id>          # 告警门禁只读入口（C14）
uv run python ops/billing.py runs      --channel <id> [--window-days 7]
uv run python ops/billing.py raise-tier --channel <id> --tier <环节> --limit-usd <额> \
                                          --calibration <id> --by <人> --reason <理由>
```

- 退出码：`0` 成功（`reconcile` / `alert-check` 兼"无告警"）｜`1` 执行失败或拒绝或**有告警**｜`2` 用法或配置错误
  （同 `ops/pilot.py:4` 与 `ops/deploy.py:11-12` 既有口径）；JSON 输出；凭证只报"是否设置 + 长度"，
  **绝不回显值**（沿用 `ops/smoke_llm.py:123-130` 口径）。
- CLI 只做参数解析与结果打印，**判定全在 core**（薄转发；`raise_tier` 的定点改写经
  `core/yaml_edit.py`）；`import-bill` 不联网（导出形态为人工上传，API 拉取属来源选项，同 C2 `source`）。
- 离线演示 `uv run python ops/demo_billing.py`：零真实调用、零外部网络（Mock 后端 + 夹具账单），
  六步 = ① 额度声明与缺项拒绝 → ② 最小规模校准记录 → ③ 未校准扩量拒绝留痕 →
  ④ 夹具账单导入 + 对账分类（含未解释项告警、重复批次拒绝、未识别格式报错）→
  ⑤ 跨进程账本并发共享额度 → ⑥ 两维取价四格 + 改价后历史复算逐字节不变 + ≥7 天窗口机检（断档如实报缺口）；
  退出码 0 = 六步全 ok（镜像 `ops/demo_dev_loop.py` 风格）。
- 命名判断项：`ops/billing.py` 与 `ops/demo_billing.py` 为本契约固定名（与 `ops/demo_pilot.py`、
  `ops/demo_calibration.py` 既有命名一致，并与 plan.md 项目结构逐字对齐），实现与 quickstart 须逐字一致。

### 场景

1. `tiers` 输出各档余量/拒绝计数/未结算预留；`calibrate` 产出校准记录且可被 `raise-tier` 引用
2. `reconcile` 有告警退出码 1、无告警 0、缺账单与参数非法 2；`import-bill` 重复批次退出码 1
3. `demo_billing.py` 退出码 0、六步全 ok；`runs --window-days 7` 未达标即如实报缺口与差值
