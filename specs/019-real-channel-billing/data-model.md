# 数据模型：真实渠道与账单对账（019-real-channel-billing）

> 存储分层：**配置**（`configs/*.yaml` 的 `budget:` 段 + `llm.profiles` 的 `price_matrix`）→
> **运行期事实**全部为文件化 append-only，**本特性无 DB 迁移**：`billing/{channel}/{bills,reports,calibrations,runs}/…`
> + 跨进程账本 `billing/{channel}/ledger.json` + 告警留痕 `billing/{channel}/alerts.jsonl`。
> 一次性快照的不可改写纪律镜像 `core/degraded/evidence.py`（`system_digest` 机检，`:33-44`/`:108-113`）；
> 周期索引报表沿用 `core/calibration/drift_report.py:395-409`；只增事实沿用
> `core/calibration/ledger.py:21-33`。字段级约定见 [contracts/core-billing.md](contracts/core-billing.md)、
> [llm-pricing.md](contracts/llm-pricing.md)、[budget-gate.md](contracts/budget-gate.md)、
> [reconcile-ops.md](contracts/reconcile-ops.md)。

## `budget:` 段配置 schema（新顶层段，两形态均须声明）

```yaml
budget:
  channels:                       # 渠道登记；渠道 id 由配置声明（码内零渠道/格式字面量）
    <channel_id>:
      adapter: <装配引用>          # 真实调用面的装配入口（本特性唯一实例 = LLM 渠道）
      bill:                       # 账单导入面：格式 id + 来源形态 + 列映射（列映射驱动分类，缺项即报错）
        format: <format_id>       # 内置通用格式键 csv_lines|json_lines，或装配方注册的渠道格式 id
        fetch: export|api
        columns: {entry_id: …, amount: …, currency: …, period: …, line_kind: …, amount_sign: …}
                                  # line_kind/amount_sign = 分类驱动列（C2/C13）；缺声明即报错，不得以金额启发式代替
  tiers:                          # 按环节分档额度（键 = 环节 id；调用点声明的环节必须在此）
    <环节>:
      limit_usd: <float>          # 上限（> 0）；具体数字待运营给定（规格开放问题 1，不发明）
      window: {kind: run|day|period}   # 时间窗（day 的日历与时区取 peak_windows.timezone）
      on_exhausted: refuse        # 唯一取值：拒绝（不排队、不降级为模拟）
      note: <口径备注>             # 缺省可通过但告警（口径须可追溯）；含"未标定"标注
  peak_windows:                   # 峰谷时段（FR-011）
    timezone: <IANA 名>           # 必填：**渠道日历唯一来源**（峰谷判定 + 额度 day 窗口 + 运行记录日期）
    attribution: call_start       # 取值域单元素（按调用开始时刻归属；其它取值报错）
    windows: [{start: "08:30", end: "00:30"}]   # 峰时区间（支持跨夜）；不需峰谷定价的渠道以 [] 声明全谷时
  calibration: {min_samples: <int>, deviation_tolerance: <float>, record_ttl_days: <int>}
  reconcile: {amount_tolerance_usd: <float>, alert_threshold_usd: <float>, unexplained_alert: true}
  ledger: {root: billing, lock_timeout_seconds: <float>}
  runs: {min_window_days: 7, gap_tolerance_days: 0}   # 覆盖下限 + 断档容差（0 = 不容断档；判定见 C15）
```

全部键**两形态均须声明**（FR-014），缺项即装配报错（不取码内默认）。`budget` 为**新顶层段**，登记点共五处：
① `tests/unit/test_form_switch.py:259-276`（顶层差异集；同文件的 `test_权重与阈值差异` Agent 权重循环**不适用**——
`budget` 不进 `evaluator_weights`）② `tests/unit/test_config_integrity.py:23-37`
（`CONFIG_CLASSES` 增 `("budget", "core.billing.budget", "BudgetConfig")`）与 `:43-67`
（`REQUIRED_PATHS` 增 `("budget", ("budget", "tiers"))`，缺项即红）③ `tests/contract/test_pilot_contracts.py:412`（C13 差异集）
④ `agents/pilot/pilot.py:101`（`config_completeness` 预检清单，`:167` 调用）⑤ `tests/conftest.py:3154`
（精简 movie 夹具）。①②③ 为既定钉点；④⑤ 是本次勘查发现的**清单缺口**（规格未列）——若选择"预检即强制
额度声明"，二者必须同步登记，否则"忘记声明额度"会在模拟路径悄悄跑通、真切换时才炸（取舍见 plan.md 缺口 6）。
两形态**取值不同**（短剧额度更小、时间窗更短——运营节奏即形态，先例 `deployment.spot_check.pending_alert_days`）。

## 价目：`price_matrix`（两维）与 legacy 形状

```yaml
llm:
  profiles:
    <profile_id>:
      prices: {prompt_per_1k: …, completion_per_1k: …}   # 基础两键（不变；未声明 matrix 时四格皆取它）
      price_matrix:                                      # 可选：声明即四格齐备（缺一即报错）
        peak_miss:     {prompt_per_1k: …, completion_per_1k: …}
        peak_hit:      {…}
        off_peak_miss: {…}
        off_peak_hit:  {…}
```

- 格位键 = `<峰谷>_<缓存>`：峰谷由**调用开始时刻**对 `peak_windows` 判定；缓存由**厂商响应报告的
  命中 prompt token 数**（`BackendResult.cached_prompt_tokens`）判定
- **legacy 形状**：快照条目**无 `price_matrix` 键** ⇒ 四格皆取 `prices`，口径备注记「未区分峰谷/缓存」；
  历史节点 `config_snapshot["llm_profiles"]` 的快照**永不重写**（原语义 = 四格同价）
- 快照条目新增**可选**键 `price_matrix` / `declared_dimensions`（如 `["peak_off_peak","cache_hit_miss"]`）；
  `prices` 与既有键（`profiles.py:98-112`，含"无密钥"纪律）逐字保留；指纹 = canonical JSON 的 BLAKE3
  （`profiles.py:131-138`），新键自然进指纹
- **本地网关内容哈希缓存 ≠ 厂商 prompt 缓存**：本地命中零成本、无后端调用、不占额、不进任何格位
  （`gateway.py:283-294`）

## 领域模型

- **真实渠道（Channel）**：`channel_id`（配置键）/ `adapter`（真实调用面装配入口）/ `bill`
  （格式 id + 来源形态 `export|api`）/ `calibration_status`（`untested|pass|fail|stale`，取最新校准记录；
  `run_window`（`covered_days`/`continuous`/`gaps`）。身份 = `channel_id`
- **预算档（BudgetTier）**：`tier_id`（= 环节 id）/ `limit_usd` / `window` / `spent_usd` /
  `reserved_usd` / `refusals`（计数 + `last_refusal{at,reason,estimated_usd}`）/ `note`。
  身份 = `(channel_id, tier_id, 窗口实例)`；余量 = `limit − spent − reserved`（由跨进程账本持有，非进程内）
- **价目表（PriceBook，两维）**：`profile_id` / `prices`（基础两键）/ `price_matrix`（四格，可选）/
  `declared_dimensions` / `price_note`；随 `ProfileSnapshot` 冻结（`profiles.py:115-153`）；历史复算不漂移
- **厂商账单（VendorBill）与账单条目（BillEntry）**：`bill_id`（批次）/ `channel_id` / `period` /
  `currency` / `source`（`export|api`）/ `raw_ref`（原始行摘要）/ `fetched_at` / `entries[]`；
  `BillEntry` = `entry_id`（账单内唯一）/ `model_ref`（档案 id，可空）/ `amount` / `currency` /
  `fx{from,to,rate,source,at}` / `usage{…}` / `note`（计费口径备注）。身份 = `(channel_id, bill_id)`
- **差异报告（ReconciliationReport）**：`report_id` = `(channel_id, period)` / `gateway_total` /
  `bill_total` / `items[]`（每条：`key`、`gateway_usd`、`bill_usd`、`delta_usd`、`classification`、
  `note`）/ `deviates` / `unexplained[]` / `alerts[]` / `thresholds_snapshot` / `bill_refs[]`
  （**必须引用账单批次与来源**——网关记账不得作"成本已核实"唯一依据）/ `overrides[]`（人工批注只追加）
- **校准记录（CalibrationRecord）**：`calibration_id` / `channel_id` / `tier_id` / `prices_snapshot`
  （含 matrix）/ `measured{`样本量`, `实测花费`}` / `deviation` / `passed` / `reasons` /
  `note`（口径备注）/ `at`。身份 = `(channel_id, calibration_id)`；**是提高额度的先决条件**
- **渠道运行记录（ChannelRunLog）**：键 = `(channel_id, date)`（date 按 `peak_windows.timezone`——渠道日历
  单点，与额度 `day` 窗口、峰谷判定同一时区，见 C4）；`entries[]` 每条 = `{at, stage,
  source: real|simulated|fallback, adapter_ref, profile_id, result, cost_source, fallback_reason}`；
  `head_digest` 链式摘要（追加只增、改写即断链）。窗口机检（C15）= `covered_days`（仅 `source=real` 的日期）
  / `gaps[{from,to,days}]` / `max_gap_days` / `continuous` /
  `meets = covered_days ≥ min_window_days ∧ max_gap_days ≤ gap_tolerance_days`

## 状态机

- 渠道：`untested →（最小规模校准）→ pass | fail`；`untested|fail` 下扩量**恒拒绝**；`pass` 是扩量唯一
  前置，且记录过期（`record_ttl_days`）即须重校
- 预算档：`在额 → 超限拒绝（0 调用 0 入账）`；窗口滚动到期重置计数；实测超预估使余量 < 0 ⇒
  `over_limit=true` 告警 + 后续调用拒绝，**已发生的花费如实入账且不回滚、不改写**
- 账单：`导入 →（批次唯一）→ 条目就位`；重复批次拒绝；未识别格式拒绝（零部分导入）
- 差异项：`六类之一 | unclassified`；`unclassified` 或超阈值 ⇒ `unexplained=true` ⇒ 告警落盘 + 报告标记
- 报告 / 校准记录 / 账单记录：产出即冻结（同键重产拒绝）；人工批注只允许追加（`overrides`）

## 磁盘布局（根可配，默认仓库根 `billing/`；`{date}` 为 `peak_windows.timezone` 的本地日期）

```
billing/{channel}/bills/{bill_id}.json              # 账单导入记录（append-only + system_digest）
billing/{channel}/reports/{period}.json             # 差异报告（append-only + system_digest）
billing/{channel}/calibrations/{calibration_id}.json
billing/{channel}/runs/{date}.json                  # 当日运行记录（追加式 + head_digest 链）
billing/{channel}/ledger.json                       # 跨进程账本（单主机文件账本：flock + 原子替换）
billing/{channel}/alerts.jsonl                      # 告警与拒绝留痕（只追加；镜像 deployment/deploys/alerts.jsonl）
```
