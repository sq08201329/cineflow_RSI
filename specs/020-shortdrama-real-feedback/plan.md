# 实现计划：短剧形态的真实投放与日级回流（C 路径接入 + 日级周期量纲 + 校准结论迁移）

**分支**: `020-shortdrama-real-feedback` | **日期**: 2026-09-25 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/020-shortdrama-real-feedback/spec.md` 的功能规格说明（14 条 FR / 9 条 SC / 15 条澄清裁决 / 17 条边界情况，含 2026-09-25 范围与机制裁决会话）

## 概要

把短剧线从"**配置里是日级、产物里还是周级**"推进到"**真的按日运转、真的有归属日、结论真的能迁移**"。本特性与 019 的关系是**兼容性扩展**（不是重做、不是削弱）：

- 019 交付了 `core/billing/` 五模块（`budget` / `bill` / `reconcile` / `calibration` / `runlog`）与两条真实渠道纪律（**前置预算门禁** + **厂商账单对账**），并**明文把媒体投放渠道整条结转 G4**（`specs/019-real-channel-billing/spec.md:18`、`docs/三期立项书.md:103-105`）。020 做的是**把媒体投放渠道接上同一道门禁**——为此必须给 `budget` 段补**渠道命名空间**（`channels.<id>.tiers.<环节>`，`core/billing/budget.py:275` 现为扁平 `tiers`）并**放开单渠道硬拒绝**（`core/billing/budget.py:357` `sole_channel()` 现明写"必须恰好声明一个渠道"）。属**兼容性扩展**：既有断言按扩展**更新**、**不削弱**；旧扁平形状仍可读、显式归入其声明的渠道、歧义即报错。
- 019 的"运行记录窗口"口径（`core/billing/runlog.py:226` 的 `covered_days ≥ min_window_days` **∧** `max_gap_days ≤ gap_tolerance_days`、`RUN_SOURCES` 取值纪律 `core/billing/runlog.py:28`）被 020 **同构复用**到"日级真实回流 ≥2 周"的机检上——不新造第二套覆盖判定；短剧态把 `budget.runs.min_window_days` 由现值 7 改为 **14**（= 立项书 G4 的"≥2 周"原文，**非发明数字**；`configs/shortdrama.yaml:600-602`），电影态保持 7（`configs/movie.yaml:597-599`）、`gap_tolerance_days` 两形态均保持现值并留在开放问题。

技术主线有三条，全部落在**真实文件**上：

1. **周期量纲由 cadence 派生**：新增业务无关模块 `core/calibration/periods.py`（4 函数），并把"一律取 ISO 周"（`core/calibration/rounds.py:28` `iso_week_label` 用于 `:69`）改为由形态声明的 `calibration.period_days` 派生（周级 ⇒ ISO 周、日级 ⇒ 日期、其他值 ⇒ 显式拒绝"未支持的 cadence"）。标签必须贯穿**台账/快照/报告/漂移**四处（`core/calibration/ledger.py:84`、`core/calibration/report.py:55`、`core/calibration/drift_metrics.py:53-54`）；漂移窗口单位与 cadence **同量纲**（日级 `window: 3` = 3 天，`configs/shortdrama.yaml:408` 的注释今天与实际不符）。周期窗口统一为**半开区间** `[start, start + period_days)`——今天 `ops/calibrate.py:43` 的缺省起点 + `core/calibration/selection.py:28` 的 `+1 天` 使缺省窗口跨 `period_days + 1` 天。窗口口径进产物（`window_semantics=half_open` + `period_days`），跨"口径变更日"的比较必须在 `note` 显式标注，历史已落盘件**零回改**。
2. **日级回流有归属日、有唯一性键**：归属日**必须**由平台接口显式给出的指标日期字段承载（`agents/promo/platform/base.py:79` 现只有 `platform_timestamp`、`agents/promo/anchors.py:22-28` 把它当"真值产生时刻"⇒ 归属日今天**无从取得**），两个适配器（`agents/promo/platform/simulated.py:94`、`agents/promo/platform/http_real.py:208`）都必须产出，**缺失即显式失败**（原因"平台未提供指标归属日"、该条不落锚点），**禁止**以拉取/采集时刻兜底；周期窗口改按**归属日**过滤（`core/calibration/selection.py:108` 的过滤键须改造）。日级回流按**采集日**分片、唯一性键由"活动"改为**（活动, 周期）**并落在**新表 `promo_daily_metrics`**（（campaign_id, period）+ INSERT-only 触发器）上——`promo_campaigns` 的既有键 `(round_id, material_id)` **零改动**（`agents/promo/db.py:33`/`:44`/`:47`、`agents/promo/ingest.py:58`/`:130` 今天使"同一活动无法连续多日各采一次"）。**两条规则分属两层、不得互相吞并**：快照层按（活动, 周期）**幂等拒绝**；外环产物层**按轮并留存、零覆盖**。
3. **校准结论迁移**：新增 `core/calibration/transfer.py` + append-only 存储 + **独立脚本** `ops/transfer.py`（与 019 的 `ops/billing.py` 同风格、退出码 0/1/2），只做**结论迁移**、**不做权重自动迁移**；**可比性条件配置化**（`configs/*.yaml` 两形态均声明、缺项即报错）；可比性不成立即**拒绝迁移**并记原因；**不改**任何既有节点得分与 `eval_breakdown`。

**诚实分层（逐条登记，不掩盖）**：本特性交付**机制 + 离线复现**（Mock 平台 + 夹具指标/账单，零真实花费、零外部网络、零凭证）；"**短剧线真实数据回流 ≥2 周**"属**运营侧墙钟 + 凭证**前提（`docs/三期立项书.md:280`："运营侧必须有人对接真实渠道"），产物与报告一律写"**机制已就绪 / 真实回流待运营**"并给出机检到的真实覆盖天数与缺口。平台名、凭证变量名、预算档数字**不由本特性发明**——全部走配置并由运营侧填值，未给定期间如实标"未标定"。"模拟被标为真实"的次数恒为 0。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: **零新增运行时依赖**——stdlib（`re` / `json` / `datetime` / `zoneinfo` / `pathlib` / `random`）+ 既有 `core/calibration/*`、`core/billing/*`、`agents/promo/*` 惯用法；不引入日期/表格/时区库，不引入厂商 SDK

**存储**: **一次数据库变更、两件 DDL**——alembic `0011_*`（当前迁移头 `0010_dev_jobs`，`ops/migrations/versions/0010_dev_jobs.py`）：① `calibration_anchors` 增一列**可空**归属日（`core/calibration/db.py:23` 的表定义同步；历史行保持 NULL、不回改；新写入非空；降级只 `DROP COLUMN`），读取侧按"归属日缺失锚点数"如实登记；② 新增表 `promo_daily_metrics`（日级分片记录，唯一键 **（campaign_id, period）** + INSERT-only 触发器）——**唯一性键必须落在有存储层冻结的记录面上**（`promo_campaigns` 是运营状态表、刻意无触发器，`agents/promo/db.py:1-5`），且这样**不改** `promo_campaigns` 的既有唯一键 `(round_id, material_id)`（`agents/promo/db.py:47`）⇒ 兼容风险最小。其余全部是文件产物，沿用既有布局：`ledger/` / `snapshots/` / `reports/`（010）、`drift/`（012）、`billing/{channel}/…`（019，`core/billing/budget.py:329` 的 `ledger_root`）；新增迁移件目录按渠道无关原则落形态数据根下，**不写任何其它特性目录与仓库权威配置**

**测试**: pytest（TDD，测试任务先于实现）。新增单元/契约套件；既有断言**按扩展更新、不削弱**。**不依赖真实昂贵调用**——全部用例走 Mock 平台 + 夹具指标/账单；对抗（合并阻塞）、无偏性（发布阻塞）、Immutable 审计与成本回归（每日）四道常驻门禁**不放松**；覆盖率 ≥85%（口径不降，含 web）

**目标平台**: Linux（WSL2 + CI）。`fcntl` 为 POSIX 专有（019 既有边界）不变

**项目类型**: `core/calibration/`（**新 2 模块**，业务无关）+ `core/billing/`（**兼容性扩展**）+ `agents/promo/`（日级分片 + 归属日）+ `ops/` CLI 与离线演示 + `configs/*.yaml`（两形态）

**性能目标**: **不设性能门禁**——单轮动作量级是"读若干 JSON + 追一行 JSONL"，数据量为"天数 × 活动数"（百级）；本特性的优先级是**口径正确性与可机检性**（宁可拒绝、不可静默兜底），不是吞吐

**约束**: `core/` 保持业务无关、依赖单向（原则五）：`core/calibration/periods.py` 与 `transfer.py` 接受 cadence/形态名作**参数**，零形态分支（`tests/unit/test_form_switch.py:256` 的差异集合断言本就覆盖 `core/`）；`core/billing/` 零渠道/环节/格式字面量（`tests/unit/test_billing_core_purity.py:110` 的"五模块被扫描"会因新增写入面而需要同步扩展）；新增参数**全部**形态配置化、两形态均声明、缺项即报错、**不取码内默认**；`core/platform_http.py` 与适配器协议**原样复用**，不重写；`CostRecord` 与发现树**一律不动**

**规模/范围**: 新 2 模块（`periods.py` / `transfer.py`）+ 010/012 六个模块改造 + promo 侧五个文件 + billing 两个文件 + 两份形态配置 + 1 个 alembic 迁移 + 2~3 个新 CLI/演示 + 测试同步；**不含**: G5 多形态插件验证、B 路径真实生成厂商对接、多租户/公网服务化、Decimal 金额重构、多主机账本、适配器协议重写

## 现状勘查：五处名不副实（先核实，后设计）

| # | 事实（逐条可核实） | 本特性处理 |
| --- | --- | --- |
| 1 | **"日级"只在配置里**：`configs/shortdrama.yaml:399` 声明 `period_days: 1`（对照 `configs/movie.yaml:392` 的 `7`），但周期标签一律取 ISO 周（`core/calibration/rounds.py:28` 的 `iso_week_label` 在 `:69` 使用），台账 `BiasRecord.period`、快照文件名（`core/calibration/ledger.py:84`）、信度报告文件名（`core/calibration/report.py:55`）全部继承该标签 ⇒ **同一 ISO 周内的日级多轮覆盖同一份快照与报告** | 周期标签由 cadence 派生（FR-003）；同周期多轮零覆盖（SC-002） |
| 2 | **漂移窗口按周计**：`core/calibration/drift_metrics.py:54` 的 `_WEEK = timedelta(days=7)` 与标签正则 `:53` 恒定为 ISO 周，`configs/shortdrama.yaml:408` 的注释却写"日级外环 → 3 天窗口" ⇒ `window: 3` 实际是 **3 周** | 窗口单位与 cadence 同量纲并写进产物（FR-005）；窗口单位进 `metric_hash` ⇒ 口径变更即新 `detector_version`（原则一"口径即版本"） |
| 3 | **缺省窗口跨 `period_days + 1` 天**：`ops/calibrate.py:43` 的缺省 `period_start = 今天 − period_days`，而 `core/calibration/selection.py:28` 的 `_period_window` 把 `period_end` **加一天**（含首尾）⇒ 日级下即"2 天窗" | 统一半开 `[start, start + period_days)`；`end - start == period_days` 机检；口径进产物 + 跨变更日标注（FR-003） |
| 4 | **归属日无从取得 + 一次活动一次快照**：`agents/promo/platform/base.py:79` 的 `MetricSnapshot` 只有 `platform_timestamp`，`agents/promo/anchors.py:22-28` 把它当"真值产生时刻"；`core/calibration/selection.py:108` 的周期窗口按节点 `created_at` 过滤；`agents/promo/db.py:33` 注明 `node_id` "回填即终态不再变"、`:44` 的 `metrics` "回流后写入一次"，回流只处理 `status == "delivered"`（`agents/promo/ingest.py:58`）并随即置 `ingested`（`:130`）⇒ 同一活动跨多日各采一次**不可能** | 归属日由平台显式字段承载、缺失即失败（FR-006）；唯一性键改（活动, 周期）、按采集日分片（FR-001） |
| 5 | **门禁面只认单渠道 + 额度无渠道命名空间**：`core/billing/budget.py:357` 的 `sole_channel()` 明写"必须恰好声明一个渠道"（`:433` 的 `assemble_guard` 经它取渠道，`:841`/`:848` 直接查扁平 `cfg.tiers`），`core/billing/budget.py:275` 的 `tiers` 为扁平"环节 → 额度"，`ops/billing.py:102-110` 的 `--channel` 亦经它校验 | 补渠道命名空间 `channels.<id>.tiers.<环节>` + 按配置声明的渠道集合分派额度（FR-007）；旧扁平形状仍可读（不削弱 019 断言） |

**口径澄清 A（最易误读）："同周期多轮零覆盖"与"同一（活动, 周期）幂等拒绝"是两层规则，不是一件事。**
① **快照层（按事件）**：唯一性键 =（活动, 周期），同键再次回流 ⇒ **幂等拒绝**（零变更、整批不中断）——"同一活动同一天的第二次采集"是**拒绝**而非覆盖（`agents/promo/db.py:47` 现为 `(round_id, material_id)`，**不改**——含周期的键落**新表 `promo_daily_metrics`**）。② **外环产物层（按轮）**：同一周期内多轮各自的台账行与信度报告**必须并留存**（文件名带轮标识）、**零覆盖**（今天 `core/calibration/ledger.py:25` 的 `append_ledger` 本就是追加、但 `:84` 的快照与 `core/calibration/report.py:55` 的报告是同名单文件写，`core/calibration/report.py:36` 甚至"同周期取末行"）。"同一活动跨多日"是**不同周期** ⇒ 合法追加。两条**分别**机检（SC-002）。

**口径澄清 B：快照是"周期物化"，不是"某一轮的产物"。**
快照主键 =（评估器, 周期），**路径形状不变**（`core/calibration/drift_metrics.py:140-142` 的 `snapshot_path` 只读、`:145-151` 缺文件返 `None` 的既有语义不变）；内容 = 该周期**全部锚点**的分布。漂移**每周期读一份**（读取时刻取该周期**最后一次物化**；缺文件 ⇒ 该周期不入窗口、缺口如实报）。同周期多轮若使快照内容变化（迟到/回补）⇒ **必须记账**（台账行登记 `anchor_count` + `snapshot_fingerprint`），**静默改写次数恒为 0**；漂移产物**必须**登记所读快照的指纹与该周期锚点数，使"读的是哪一份"可追溯（SC-002）。

**口径澄清 C："归属日"与"平台时间戳"是两个量，报告须三者并列。**
归属日 = **平台指标所描述的日期**（周期归属依据）；`platform_timestamp` 保持"**真值产生时刻**"语义（`agents/promo/anchors.py:22-28` 据此写锚点 `created_at`，不改）；回流节点 `created_at` 保持**采集墙钟**（`agents/promo/ingest.py:117` 的 `time.time()`，不改）。迟到/回补时归属日与平台时间戳**必然不同**，两者相同只是巧合。报告必须并列可见**归属日 / 采集墙钟 / 平台时间戳**（SC-003）。

**口径澄清 D：015 的"按轮上限"与 019 的"跨进程账本"是两套门禁，必须共同生效且口径可辨。**
015 是**进程内按轮估算**（`agents/promo/config.py:70-72` 的 `budget_cap_usd = exploration_per_round_usd × promo_pilot_ratio`，前置校验在 `agents/promo/loop.py:403-418`，花费读自运营表 `_round_spent` `:240`）；019 是**跨进程文件账本 + 厂商账单口径**（`core/billing/budget.py` 的 `FileLedger` + `SpendGuard`）。二者**不得混同、不得互相替代**：投放申请必须**同时**过两道，拒绝理由须点名命中的是**哪一条**（FR-008）。`SpendGuard` 本身**渠道无关**（`core/billing/budget.py:763-793`，只认 `channel_id` + 注入的 `cfg` + `SpendRequest` 协议 `:133-143`），故媒体渠道复用同一实现、不新造旁路门禁。

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| **原则一** 评估器确定性与版本冻结；`human` 类评估器**仅**作校准锚点、其产出（人评、**平台回流数据**）**一经写入即冻结为常数** | 锚点写入仍走**唯一接口** `insert_anchor`（`core/calibration/anchors.py:25`），存储层触发器（`core/calibration/db.py:47` 的 SQLite / `:61` 的 PG）与唯一键（`:41`）不动；同键重复回流**幂等拒绝**、整批不中断（FR-004/SC-008）；迁移 0011 **只做加法**（锚点表加可空归属日列 + 新表 `promo_daily_metrics`），历史行 NULL 不回改、`promo_campaigns` 既有键零改动；漂移窗口单位与 cadence 同量纲 ⇒ 进 `metric_hash`（`core/calibration/drift_metrics.py:116-132`），口径变更体现为**新 `detector_version`**、历史判定**不回溯**（`drift_metrics.py:331-348` 的 `write_record` 既语义保持） | ✅ 满足 |
| **原则二** 节点不可变与全量谱系；`CostRecord` 含 `FAILED` **必须**入账 | 发现树、`CostRecord`、`eval_breakdown`、`score` **一律不动**（迁移不改任何节点）；回流节点仍是"一次性完整 INSERT 落盘即终态"（`agents/promo/ingest.py:90-120`）；台账/报告/迁移件为 append-only 文件产物；**日级分片是追加新节点/新行，不是改写旧节点**（同周期多轮零覆盖＝每轮各写各的文件名） | ✅ 满足 |
| **原则三 · 条款①** 真实渠道调用**必须**受**预算门禁**：调用前**必须**通过按环节分档的额度（超限即拒绝，**禁止**先花后报） | 投放申请在**适配器调用之前**过 019 的 `SpendGuard.check`（`core/billing/budget.py:824` 的 `check` 语义：缺环节 ⇒ `tier_undeclared`、超限 ⇒ `over_limit`、通过即同事务占额）；额度键改由**渠道命名空间**解析（`:841`/`:848` 的扁平查表改为按 `channel_id` 分派）；超限 ⇒ **调用前拒绝、平台调用 0 次、零入账**（FR-008/SC-004）；与 015 的按轮上限**并存**（口径澄清 D） | ✅ 满足 |
| **原则三 · 条款②** 调用后**必须**产出与厂商账单的差异报告；网关记账**并非**"成本已核实"唯一依据 | 复用 019 的 `core/billing/reconcile.py:233` 的 `reconcile` 与 `:95` 的 `ReconciliationReport`：媒体渠道的账单导入面在 `budget.channels.<投放渠道>.bill` 声明（格式 id + 列映射 + 分类取值域），逐项比对 + **每条差异带分类** + 实测偏差 + 口径备注，报告必引账单批次（FR-009/SC-005）；**不新造第二套对账机制** | ✅ 满足 |
| **原则三 · 条款③** 任一真实渠道**必须**先以**最小规模**验证协议与计费口径，验证通过后方可扩量 | 复用 019 的 `core/billing/calibration.py`：媒体渠道必须以**投放侧**最小规模入口（**不得**假定 019 的 LLM 冒烟入口，`specs/019-real-channel-billing/spec.md:102`）产出校准记录（配置价目/实测花费/偏差/口径备注/样本量/时间），低于样本下限即 `passed=false`；无记录/过期/样本不足 ⇒ **扩量 100% 拒绝并留痕**、理由点名命中哪一条（FR-010/SC-006） | ✅ 满足 |
| **原则四** 沙箱隔离与前缀不可泄露（含 v2.0.0 的**人工编写降级模式策略显式例外条款**） | 本特性**不涉及**策略执行、不触模拟器、不引入新执行路径 ⇒ **既不触发原则四的任何条款，也不适用例外条款**（例外只对"人编写 + 过静态检查 + 需人显式采纳"的降级策略成立；020 无新增策略代码）。迁移件的"人工两键"是**原则六**的人工锚点纪律，不是沙箱例外 | ✅ 不适用/不触及 |
| **原则五** 单向依赖 + 形态差异**必须**经 `configs/*.yaml` 表达、切换形态**必须**零代码改动 + **例外必须是"新增配置项"而非"新增分支代码"** | 新模块落 `core/calibration/`（**业务无关**：零形态名、零 cadence 字面量分支——cadence 作参数传入，仅接受 `1` 与 `7` 两个**数值**并对其余显式拒绝）；依赖方向 `core/calibration → core/evaluators`（既有）单向，**不 import `agents.*`/`dreaming.*`**；`agents/promo/`（业务侧）负责"平台字段 → 通用字段"的映射；新增参数**全部**进 `configs/movie.yaml` 与 `configs/shortdrama.yaml`（周期量纲与窗口口径、回流日期归属、预算分档与 `runs` 窗口下限、迁移口径与可比性条件；**渠道命名空间的 schema 亦两形态齐备，但投放渠道只在短剧态登记**），**两形态均声明、缺项即报错、不取码内默认**，且**两形态都必须能装配通过**；**零形态分支**（无 `form ==`、无 cadence 的 `if` 分支树——`1`/`7` 的判定即量纲本身，其他值一律拒绝）（FR-014/SC-009） | ✅ 满足 |
| **原则六** 指标口径**必须**可被证伪；人**不得**参与逐条评估、**只**以稀疏抽检校准评估器；Calibration 结论写入 `calibration` 字段 | 三处口径全部**可指认**：归属日来源（平台显式字段，缺失即失败并给原因）、窗口口径（`window_semantics` + `period_days` 进产物）、漂移读取口径（所读快照指纹 + 该周期锚点数进产物）；局限**逐条如实标注**（真实回流待运营、平台字段未标定、历史锚点缺归属日、可比性不成立即拒绝迁移、断档逐段报出**不插值**）；迁移件走**人工两键**（提案 → 人工采纳/搁置，与 010 的 `ProposalStatus` 机制同构 `core/calibration/models.py:60`），**不自动改权重**、不改任何节点得分（FR-012/SC-007） | ✅ 满足 |
| **治理：复杂度必须被论证** | 新增 `core/calibration/periods.py`（4 函数）、`core/calibration/transfer.py`、渠道命名空间、迁移件 append-only 存储、`0011_*` 迁移**逐项**写入复杂度跟踪表并对照被否决的更简方案 | ✅ 满足 |
| **测试纪律** | TDD（测试任务先于实现，见阶段 2~6 的 TDD 序）；**不依赖真实昂贵调用**——全部用例走 Mock 平台 + 夹具指标/账单，真实投放与真实回流属运营动作；新增/变更断言**不削弱**既有门禁 | ✅ 满足 |
| **门禁清单**（对抗 / 无偏性 / Immutable 审计 / 成本回归） | 四条常驻门禁**不放松**：Immutable 审计（`ops/audit_immutable.py`）因"锚点只加一列、历史行不回改、节点零改动"而无需改判据；成本回归不受影响（本特性的花费面为 0 真实调用）；对抗与无偏性套件不因本特性改动（`tests/adversarial` / `tests/unbiasedness` 零改动） | ✅ 满足 |

**门禁通过。** 三条新增条款（原则三 v2.0.0）在媒体渠道上的落实分别由 **C11**（渠道命名空间 `channels.<id>.tiers.<环节>` + 旧扁平形状兼容读）、**C12**（装配与额度按配置声明的渠道集合分派 + 投放调用接入同一门禁）、**C14**（投放调用与 015 进程内按轮上限的**分辨**：调用前拒绝、平台 0 次调用、零入账）、**C16**（凭证就绪矩阵 + 最小规模先行）承载；**C13** 是"对 019 既有断言**零删除、零放宽**"的逐条举证清单（`tests/contract/test_billing_contracts.py` 内 12 个 `--channel` 实参 + 每日工作流），**它是本特性兼容性主张的举正面**。原则一以 **C1/C3** 的断言为前提——"周级标签逐字节不变 + 快照不重写 + 同周期多轮零覆盖"，未落实即视为未通过。

## 项目结构

### 文档（此功能）

```text
specs/020-shortdrama-real-feedback/
├── spec.md / plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── period-cadence.md    # cadence 派生标签 / 半开窗口 / 快照周期物化 / 漂移读取口径 / 口径进产物与变更留痕（C1~C5）
│   ├── daily-ingest.md      # 日级分片与（活动,周期）唯一性 / 归属日来源与缺失即失败 / 锚点冻结与幂等 / 三时间与覆盖 / 来源纪律（C6~C10）
│   ├── channel-budget.md    # 渠道命名空间与旧扁平形状兼容 / 按声明渠道集合分派 / 019 断言不削弱清单 / 投放调用与按轮上限之辨（C11~C14）
│   └── transfer-ops.md      # 迁移件与可比性判定 / 凭证矩阵与最小规模先行 / CLI 与演示 / 诚实分层机检（C15~C18）
└── tasks.md                 # 阶段 2 输出（/skill:speckit-tasks）
```

**契约编号（固定，本计划引用时以此为准）**：`contracts/period-cadence.md` ← **C1~C5**；`contracts/daily-ingest.md` ← **C6~C10**；`contracts/channel-budget.md` ← **C11~C14**；`contracts/transfer-ops.md` ← **C15~C18**。

### 源代码（仓库根目录，在既有结构上增量）

```text
core/calibration/periods.py            # 新（业务无关）：period_label / period_start / period_window / period_regex
                                       #   period_days==1 ⇒ YYYY-MM-DD、==7 ⇒ ISO 周（语义沿用 rounds.iso_week_label）、
                                       #   其余 ⇒ ValidationError「未支持的 cadence」；period_window 半开
core/calibration/transfer.py           # 新（业务无关）：迁移件生成 + 可比性判定 + append-only 读回
core/calibration/selection.py          # 既有：_period_window（:28）改半开；候选过滤键（:108）改按归属日
core/calibration/rounds.py             # 既有：period（:69）改经 periods.period_label（周级逐字节不变）
core/calibration/ledger.py             # 既有：台账行登记 anchor_count + snapshot_fingerprint；报告/台账文件名带轮标识
core/calibration/report.py             # 既有：产物写 window_semantics + period_days + note（跨口径变更日标注）
core/calibration/drift_metrics.py      # 既有：标签正则/周期步长（:53-54）改 cadence 量纲；登记所读快照指纹与锚点数
core/calibration/drift_config.py       # 既有：period_days 进判定口径（缺项即报错）+ 窗口单位进 thresholds 快照
core/calibration/drift_models.py       # 既有：DriftMetrics（:179）增可选登记字段（快照指纹/锚点数）
core/calibration/models.py             # 既有：AnchorScore（:70）增归属日字段（可选，历史行允许缺）
core/calibration/db.py                 # 既有：表定义（:23）增可空归属日列
core/calibration/anchors.py            # 既有：insert_anchor（:25）/ load_anchors（:47）带归属日
core/billing/budget.py                 # 既有：tiers（:275）改 channels.<id>.tiers.<环节>；sole_channel（:357）
                                       #   退役 → declared_channels(cfg) / channel_for_adapter(cfg, adapter_id)
                                       #   / tiers_of(channel_id) / tier_of(channel_id, tier_id)；
                                       #   check（:841/:848）按渠道解析档位；assemble_guard（:433）扩参 channel_id=
core/billing/runlog.py                 # 既有：RUN_SOURCES（:28）复用不改；新增 RecordingChannelCall
                                       #   （投放调用门禁包装，与 RecordingGateway（:313）同构）
agents/promo/platform/base.py          # 既有：MetricSnapshot（:71）增归属日字段（C7 定名 metric_date）+ 缺失即失败
agents/promo/platform/simulated.py     # 既有：fetch_metrics（:94）产出归属日（取 platform_timestamp 的 UTC 日期）
agents/promo/platform/http_real.py     # 既有：_snapshot（:208）读平台指标日期字段，缺失即拒（不兜底）
agents/promo/db.py                     # 既有：`promo_campaigns` 零改动（键 :47 保留、口径注释 :33/:44 更新表述）；
                                       #   新增 `promo_daily_metrics` 表定义（唯一键（campaign_id, period）+ INSERT-only 触发器）
agents/promo/daily.py                  # 新（业务侧库函数）：日级回流 + 归属日归入 + 同（活动,周期）幂等拒绝 + 窗口机检
agents/promo/ingest.py                 # 既有：按采集日分片（CLI 薄封装见 ops/ingest_metrics.py）
agents/promo/anchors.py                # 既有：归属日进锚点（:57-67）；历史缺归属日按 created_at 日期回退并登记
agents/promo/loop.py                   # 既有：投放按轮上限原位保留（:403-418）；账本门禁挂在装配面包装上（C14）
ops/calibrate.py                       # 既有：缺省窗口半开（:43）+ 周期标签派生（:190）
ops/transfer.py                        # 新 CLI（**独立脚本**，与 019 的 ops/billing.py 同风格、退出码 0/1/2）：
                                       #   transfer / transfer-confirm / transfer-shelve / transfer-report
ops/screenplay.py / ops/dev.py         # 既有：week_label（:473 / :423）改经 periods.period_label（周级不变）
ops/ingest_metrics.py                  # 既有：增 --daily / --metric-date / --coverage；--source {real,simulated} 必填
ops/demo_shortdrama_feedback.py        # 新：离线端到端演示（Mock 平台 + 夹具指标/账单；退出码 0；零真实花费）
ops/billing.py                         # 既有：增加 channels 只读子命令；--channel 语义保留（未声明渠道仍退出 2）
ops/smoke_llm.py                       # 既有：最小规模校准入口（LLM 渠道口径不变）
agents/pilot/backends.py               # 既有：装配点（:280）按装配引用解析渠道，不再经 sole_channel
ops/migrations/versions/0011_*.py      # 新（**两件 DDL**）：① 锚点表增可空归属日列（历史行不回改、新写入非空）；
                                       #   ② 新表 promo_daily_metrics（唯一键（campaign_id, period）+ INSERT-only 触发器）
configs/movie.yaml / shortdrama.yaml   # 既有：calibration 新增项（量纲/窗口口径/归属/迁移可比性条件）
                                       #   + budget.channels 增渠道命名空间 tiers（**投放渠道仅短剧态登记**；
                                       #     两形态 schema 齐备、缺键即报错、两形态装配都须通过）
                                       #   + 短剧态 budget.runs.min_window_days: 14（电影态保持 7）
tests/…                                # 见阶段 2~6 与 research.md 决策 10 的变红清单
```

**结构决策**: 三块落点各有其必然性。① **周期量纲必须落 `core/calibration/`**（不能落 `agents/`）：标签被 010 的台账/快照/报告与 012 的漂移共用，写入四个模块的是 `core` 侧机制；且它**与形态无关**——cadence 由配置声明、以参数传入（原则五）。② **归属日字段必须落在业务侧**（`agents/promo/platform/base.py`）：字段名与语义是**平台协议面**的事，`core` 不得出现平台概念；因此 `core/calibration/selection.py` 只接收"归属日"这个通用量（`MetricSnapshot` 不得被 `core` 引用）。③ **渠道命名空间必须改 `core/billing/budget.py` 而非另造门禁**：019 的 `SpendGuard`/`FileLedger` 本就渠道无关（`:763` 起），媒体渠道复用同一实现才满足原则三"禁止为投放另造一套旁路门禁"；改动是兼容性扩展（旧扁平形状仍可读），且**渠道 id 全部来自配置**（`core/billing/` 零渠道字面量由 `tests/unit/test_billing_core_purity.py:110` 常驻守着）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要（10 条，与本计划逐条对齐）：

1. **周期 cadence 派生标签且周级字节不变**——`periods.period_label` 在 `period_days == 7` 时**逐字复用** `iso_week_label` 语义（`core/calibration/rounds.py:28`），故所有周级路径标签与历史产物**逐字节不变**。
2. **半开窗口的兼容性修正与口径变更留痕**——两处既有实现点（`ops/calibrate.py:43`、`core/calibration/selection.py:28`）改为半开；`window_semantics` + `period_days` 进产物；跨"口径变更日"的比较在 `note` 显式标注；历史已落盘件零回改。
3. **快照=周期物化、漂移每周期读一份**——路径形状不变（`core/calibration/drift_metrics.py:140-142` 只读）；读取取该周期最后一次物化；漂移产物登记所读快照指纹与锚点数；内容变化必须记账（静默改写恒 0）。
4. **日级分片的唯一性键与幂等边界**——快照层按（活动, 周期）幂等拒绝；外环产物层按轮并留存；两层分属不同层、分别机检。
5. **归属日的来源**——平台显式指标日期字段 + 缺失即失败 + 历史锚点按 `created_at` 日期回退（如实登记缺失数）；`platform_timestamp` 语义不变。
6. **019 渠道命名空间的兼容读旧形状与渠道收敛口径**——**穷举** `tests/contract/test_billing_contracts.py` 全部 `--channel` 调用点与 `.github/workflows/billing_alerts.yml:27`，逐条给出"保留/更新"结论。
7. **015 进程内按轮上限与 019 跨进程账本的分工**——共同生效、拒绝理由点名命中的是哪一条，不得混同。
8. **迁移件的可比性条件配置化**——两形态均声明、缺项即报错；结论迁移、不自动迁权重。
9. **迁移 `0011_*` = 两件 DDL**（当前头 `0010_dev_jobs`）：① 锚点表加**可空**归属日列（历史行 NULL 不回改、**新写入非空**）；② 新表 `promo_daily_metrics`（唯一键 **（campaign_id, period）** + INSERT-only 触发器）承载日级分片记录——唯一性键落在**有存储层冻结**的记录面上（`promo_campaigns` 刻意无触发器，`agents/promo/db.py:1-5`），且**不改** `promo_campaigns` 的既有唯一键 `(round_id, material_id)`（`:47`）。
10. **会变红的既有测试与夹具清单及处理方式**——逐条列出，处理原则是"**按扩展更新、不削弱**"。

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：周期量纲口径（cadence → 标签/窗口单位）、日级回流记录与唯一性键（含 `promo_daily_metrics` 的（campaign_id, period）键）、归属日与三者并列的字段面、锚点归属日列与 `metric_date` 形态、渠道命名空间 schema、迁移件 schema、迁移 0011（两件 DDL）的兼容规则（**由并行任务产出，已与本计划逐条对齐**）
- [contracts/period-cadence.md](contracts/period-cadence.md)（**C1~C5**）：C1 周期标签由 cadence 派生（`1`/`7`/其余显式拒绝，周级字节不变）；C2 周期窗口统一半开 `[start, start + period_days)`（禁止 `period_days + 1` 天）；C3 快照 = 周期物化（主键（评估器, 周期）+ 零覆盖 + 内容变化必记账）；C4 漂移读取口径（每周期一份 + 窗口同量纲 + 指纹/锚点数登记）；C5 口径进产物与变更留痕（`window_semantics` / `period_days` / 变更日）
- [contracts/daily-ingest.md](contracts/daily-ingest.md)（**C6~C10**）：C6 日级分片与（活动, 周期）唯一性（快照层幂等拒绝 vs 外环产物层零覆盖，两条规则各管一层）；C7 归属日来源与缺失即失败（禁止以采集时刻兜底）；C8 锚点冻结与幂等（多日只追加、迟到按归属日归入、存储层拒绝改写）；C9 报告三时间可见（归属日 / 采集墙钟 / 平台时间戳）+ 覆盖按归属日（断档逐段如实报、不插值）；C10 运行来源纪律（`real` / `simulated` / `fallback`，模拟不得冒充真实）
- [contracts/channel-budget.md](contracts/channel-budget.md)（**C11~C14**）：C11 渠道命名空间配置形状 `channels.<id>.tiers.<环节>` + 旧扁平形状兼容读；C12 装配与额度按配置声明的渠道集合分派（`declared_channels(cfg)` / `channel_for_adapter(cfg, adapter_id)` / `tiers_of(channel_id)` / `tier_of(channel_id, tier_id)`；**`adapter` 取值域不变**、既有 `pilot_llm` 原样保留）；C13 019 既有断言不削弱清单（逐个 `--channel` 调用点 + 每日工作流）；C14 投放调用受同一门禁 + 与 015 进程内按轮上限的分辨
- [contracts/transfer-ops.md](contracts/transfer-ops.md)（**C15~C18**）：C15 迁移件与可比性判定（只做结论迁移、不自动迁权重）；C16 凭证就绪矩阵与最小规模先行；C17 CLI / 离线演示与退出码语义；C18 诚实分层机检（模拟不得冒充真实）
- [quickstart.md](quickstart.md)：验证命令 + 运营侧流程（配置渠道与凭证 → 最小规模投放校准 → 日级回流 → 日级校准/漂移 → 迁移提案 → 人工采纳）+ 验收口径 + 验证记录回填区

**FR → 契约落点对照**（不新增需求，只做覆盖核对）：

| FR | 承载契约 | 说明 |
| --- | --- | --- |
| FR-001 按采集日分片的日级回流面、唯一性键（活动, 周期）、来源可机读区分 | **C6**（+C10 来源纪律） | `node_id` 不再是终态唯一标识——改为**含周期的确定性派生**（如 `{material_id}-node@{period}`，**历史值不回改**）；唯一性键落新表 `promo_daily_metrics` 的（campaign_id, period）；既有历史行不被误判为另一次采集 |
| FR-002 证据件时间索引、覆盖 ∧ 连续双条件、断档如实报、按 cadence 标签分片、按归属日聚合 | **C9**（+C1/C2 量纲与窗口、C7 归属日） | 复用 `core/billing/runlog.py:226` 口径；**短剧态 `budget.runs.min_window_days: 14`**（= 立项书 G4"≥2 周"原文，**非发明数字**）、电影态保持 7；禁止插值 |
| FR-003 周期量纲由 cadence 派生、同周期多轮零覆盖、半开窗口、口径进产物与跨变更日标注 | **C1/C2/C3/C5** | 今天 `tests/unit/test_form_switch.py:188-194` 只断言配置数字 ⇒ 须扩展为"运转"断言 |
| FR-004 `human` 锚点写入即冻结、同键幂等拒绝、多日只追加、锚点携带归属日与采集墙钟 | **C8**（+C3 快照零覆盖、C5 历史零回改） | 触发器与唯一键不动（`core/calibration/db.py:41`/`:47`） |
| FR-005 漂移随 cadence 同量纲、每周期读一份快照、登记指纹与锚点数、样本不足如实标注 | **C4**（+C1 量纲、C3 快照物化） | 窗口单位进 `core/calibration/drift_metrics.py:116-132` 的口径哈希 |
| FR-006 周期归属由归属日决定、平台显式字段承载、缺失即失败、按归属日过滤、三者报告可见 | **C7/C9**（+C8 归属日进锚点） | `metric_date` 末位可选（兼容历史行重建），**新写入产生 None 恒为 0**；`core/calibration/selection.py:108` 的过滤键改造；`platform_timestamp` 语义不变 |
| FR-007 投放渠道登记进 019 的渠道注册、补渠道命名空间、放开单渠道硬拒绝、旧扁平形状仍可读 | **C11/C12** | 对 019 的兼容性扩展、不削弱；**`adapter` 取值域不变**（`pilot_llm` 原样保留）；禁止旁路门禁 |
| FR-008 投放调用前置门禁、超限拒绝零入账、与 015 按轮上限共同生效且口径可辨 | **C14**（+C12 渠道分派） | 拒绝理由须点名命中的是哪一条 |
| FR-009 平台账单对账（规范化导入 → 逐项比对 → 分类 → 偏差与口径备注 → 无分类即告警） | **C11**（投放渠道的 `bill` 声明面）+ **C13**（既有对账断言不削弱） | 复用 `core/billing/reconcile.py:233` 与 `core/billing/bill.py:321` 的导入面，**不新造** |
| FR-010 最小规模校准记录（含投放渠道入口）、无合格记录不得扩量 | **C16** | 最小规模入口**必须**适用投放渠道（不得假定 019 的 LLM 冒烟入口） |
| FR-011 凭证就绪矩阵显式、以适配器代码为权威、缺失即装配期拒绝启动、不落盘凭证 | **C16** | `agents/promo/platform/http_real.py:143` 的缺凭证文案与 `:151-155` 的读取点；缺失只在短剧态声明真实投放后端时拦截 |
| FR-012 校准结论迁移必须成为交付（来源标识 + 迁移口径 + 可比性判定 + append-only + 人工两键 + 不改既有节点） | **C15**（+C17 CLI） | 与 010 提案机制同构、**不自动迁权重**；CLI 为独立脚本 `ops/transfer.py` |
| FR-013 诚实分层机检（机制与离线复现、"机制已就绪/真实回流待运营"、模拟不冒充真实、禁静默回落计费） | **C18**（+C10 来源纪律） | 复用 `RUN_SOURCES`（`core/billing/runlog.py:28`），"模拟被标为真实"恒 0 |
| FR-014 全部新参数形态配置化、两形态均声明、缺项即报错、零形态分支、CLI 与离线演示 | **C5**（口径进产物）+ **C11**（渠道 schema）+ **C15**（可比性条件）+ **C17**（CLI/演示） | 登记点见阶段 6；"两形态均声明" = **新增参数键的 schema 齐备**（两形态都能加载、缺键即报错），**不是**要求两形态登记同一渠道或取同值——**两形态都必须能装配通过** |

## 阶段 2：P1 骨架——周期量纲与半开窗口（US1 第一半）

**TDD 序**：先写 `tests/unit/test_period_cadence.py`（新）+ 扩展 `tests/unit/test_form_switch.py:188-194` 的 `test_外环日级`，使其**因目标行为缺失而失败**，再实现。

1. 新增 `core/calibration/periods.py`：`period_label` / `period_start` / `period_window` / `period_regex`；`period_days == 7` 走既有 ISO 周语义（与 `core/calibration/rounds.py:28` 逐字一致），`== 1` 走 `YYYY-MM-DD`，其余抛 `ValidationError`「未支持的 cadence」。**机检**：对全部既有周级标签样本，`period_label` 输出与 `iso_week_label` **逐字节相同**。
2. 改 `core/calibration/rounds.py:69`：`period = period_label(round_.period_end, config.period_days)`；`iso_week_label` **保留为公开函数**（`:28`），供 `periods.py` 与既有调用点复用（`ops/screenplay.py:473`、`ops/dev.py:423`、`ops/calibrate.py:190` 改为经 `periods.period_label`，周级取值不变）。
3. 改 `core/calibration/selection.py:28` 的 `_period_window` 为**半开**：`end = period_start + period_days` 天（不再 `+1`）；`ops/calibrate.py:43` 的缺省起点保留"今天 − period_days"，二者合成后 `end - start == period_days`。
4. 改 `core/calibration/selection.py:108` 的候选过滤：过滤键由节点 `created_at` 改为**归属日**（见阶段 3 的锚点归属日列；无归属日的历史行按 `created_at` 日期回退并在报告登记缺失数）。
5. 口径进产物：`core/calibration/report.py:20-58` 的报告增 `window_semantics` / `period_days` / `note`；`ops/calibrate.py:150` 的 CLI 输出同口径；跨"口径变更日"的比较在 `note` 显式标注（变更日由配置声明）。
6. 配置：`configs/movie.yaml` 与 `configs/shortdrama.yaml` 的 `calibration` 段新增量纲/窗口口径项（两形态均声明、缺项即报错）；`core/calibration/config.py:33` 的 `from_dict` 改为**必需读取**（不取码内默认）。

## 阶段 3：P1 主体——日级分片、归属日与漂移量纲（US1 第二半）

**TDD 序**：先写 `tests/unit/test_promo_daily_ingest.py`、`tests/unit/test_metric_attribution_date.py`、`tests/unit/test_drift_cadence.py`（新）+ 更新 `tests/unit/test_anchor_snapshots.py:23`、`tests/unit/test_drift_detect.py:32`

1. **归属日字段**（键名由 C7 定名）：`agents/promo/platform/base.py:71` 的 `MetricSnapshot` 增**平台指标日期**字段（`contracts/daily-ingest.md:99-130` 记为 `metric_date`）；`:83` 的 `validate_metrics` 保持值域校验；新增显式存在性检查（原因文案「平台未提供指标归属日」）；`agents/promo/platform/simulated.py:94-112` 产出该字段（缺省取 `platform_timestamp` 的 UTC 日期 ⇒ 与 `:110` 的固定时间戳同源、逐字节可复现，**禁止**由 `now()` 派生）；`agents/promo/platform/http_real.py:208-227` 的 `_snapshot` 从平台指标响应读该字段，**缺失即拒**（沿用其"缺字段即拒、不补零"的既有风格），**禁止**以拉取/采集时刻兜底。
2. **日级分片与幂等**：库函数落 `agents/promo/daily.py`（**新增，业务侧**；CLI 只作薄封装——镜像 `agents/promo/ingest.py` 的分层纪律），`ops/ingest_metrics.py` 增 `--daily` / `--metric-date` / `--coverage` 等薄参数且 `--source {real,simulated}` **必填**；**唯一性键（campaign_id, period）落新表 `promo_daily_metrics`**（迁移 `0011_*` 的第二件 DDL，INSERT-only 触发器）——`promo_campaigns` 的既有唯一键 `(round_id, material_id)`（`agents/promo/db.py:47`）**原样保留、不改**，`:33` 的"回填即终态"口径与 `:44` 的"metrics 写一次"按"同一活动多周期各一条"更新表述；`node_id` 改为**含周期的确定性派生**（如 `{material_id}-node@{period}`，`agents/promo/ingest.py:91`，**历史值不回改**）；`agents/promo/ingest.py:58` 的候选选取由"仅 delivered"改为"按采集日/周期分片"，`:117` 的 `created_at` 保持采集墙钟、`:130` 的回填按（活动, 周期）落分片记录；同（活动, 周期）重复 ⇒ **幂等拒绝（零变更、整批不中断）**。
3. **锚点归属日**：`core/calibration/models.py:70` 的 `AnchorScore` 增归属日字段（**形态 = 末位可选 `metric_date: str | None = None`**，兼容历史行读取）；`core/calibration/db.py:23` 的表定义加**可空**列，新增迁移 `ops/migrations/versions/0011_*.py`（`down_revision = "0010_dev_jobs"`）；`core/calibration/anchors.py:25`/`:47` 读写带该字段；`agents/promo/anchors.py:57-67` 写锚点时带上归属日，历史缺该字段 ⇒ 按 `created_at` 日期回退并在报告登记**归属日缺失锚点数**；`_snapshot_created_at`（`:22-28`）的"平台时间戳语义"**不改**。**机检边界（必须常驻）**：① **新采集写入路径**产生 `metric_date is None` 的次数**恒为 0**；② 回退值必须被计数并在报告可见（不得静默补值）。
4. **快照=周期物化 + 零覆盖**：`core/calibration/ledger.py:55` 的 `write_anchor_snapshots` 内容 = 该周期**全部锚点**；`:25` 的台账行登记 `anchor_count` + `snapshot_fingerprint`（内容变化必须记账、静默改写恒 0）；报告文件名带**轮标识**（`core/calibration/report.py:55`）使同周期多轮并留存、零覆盖；`core/calibration/report.py:36` 的"同周期取末行"改为"按轮并留存"。
5. **漂移量纲与可追溯**：`core/calibration/drift_metrics.py:53-54` 的正则/步长改为由 cadence 派生（`period_regex` / `period_days`）；`:73-83` 的 `missing_periods` 同步；`:116-132` 的 `metric_hash` 纳入周期量纲与窗口单位 ⇒ 口径变更即新 `detector_version`；`core/calibration/drift_config.py:99` 的 `window` 增配 `period_days`（**缺项即报错**）与 `:185-192` 的 `thresholds_snapshot` 增窗口单位；`core/calibration/drift_models.py:179` 的 `DriftMetrics` 增可选登记字段（所读快照指纹 + 该周期锚点数），由 `core/calibration/drift_metrics.py:358-454` 的 `detect_drift` 填。

## 阶段 4：P2——C 路径投放接入与 019 渠道命名空间（US2）

**TDD 序**：先写 `tests/unit/test_billing_channel_namespace.py`、`tests/unit/test_promo_delivery_gate.py`（新）+ 更新 `tests/unit/test_billing_paths.py:114` 与契约调用点

1. **渠道命名空间**：`core/billing/budget.py:275` 的扁平 `tiers` 改为按渠道分组（`channels.<id>.tiers.<环节>`）；`:318` 的 `tier()`、`:344` 的 `to_snapshot()`、`:841`/`:848` 的守卫查表全部改为**按渠道解析**（同一档位不得跨渠道串用）；`:1016` 的 `_parse_channels` 与 `:1063` 的 `_parse_tiers` 同步改造。
2. **放开单渠道硬拒绝**：`core/billing/budget.py:357` 的 `sole_channel()` 的"必须恰好声明一个渠道"硬拒绝**退役**，改为**按配置声明的渠道集合分派**（C12）：接口定名统一为 **`declared_channels(cfg)` + `channel_for_adapter(cfg, adapter_id)`**（另有 `tiers_of(channel_id)` / `tier_of(channel_id, tier_id)`；**不引入"槽位"命名**），既有 `tiers`（`:275`）与 `tier()`（`:318`）降级为**单渠道兼容视图**；**`channels.<id>.adapter` 的取值域不变**——既有 `pilot_llm` **原样保留**（不做"收敛为槽位 id"的改造），新增投放渠道的 `adapter` 按其**真实装配入口**命名即可；`core/billing/budget.py:433` 的 `assemble_guard` 扩参 `channel_id=`（装配点先用 `channel_for_adapter(cfg, <装配入口>)` 解析再传入；取值 ∉ `declared_channels(cfg)` ⇒ 报错；单渠道下 `assemble_guard(config_path)` **保留可用**）；`agents/pilot/backends.py:280`、`ops/smoke_llm.py:206` 两个既有装配点按新口径传入（**不新增第三个装配点**）；**旧扁平形状仍可读**（显式归入其声明的渠道、多渠道 + 扁平形状 ⇒ 歧义报错、不得静默误判）。
3. **CLI 语义保留（不削弱）**：`ops/billing.py:102-110` 的 `_channel_of` 改为按 `declared_channels(cfg)` 判定——未声明值 ⇒ **退出 2** 且文案**必须保留「不一致」子串**（`tests/contract/test_billing_contracts.py:1291` 依存）；`:113-119` 的 `_read_tier_rows` 与 `:172` 的缺档判定改经 `tiers_of` / `tier_of`；新增只读子命令 `ops/billing.py channels`（声明渠道集合 → 装配引用（`adapter`）归属 → 各渠道档位/余量/拒绝计数 → 凭证就绪矩阵 → 每渠道账本路径；退出码 0，缺项或不一致 ⇒ 2）；`tests/contract/test_billing_contracts.py:1146` 的 `--channel nope` 仍**必须**退出 2（逐条穷举结论见 research.md 决策 6）；`.github/workflows/billing_alerts.yml:27` **一字不改且必须继续通过**。
4. **投放前置门禁**：`agents/promo/loop.py:403-418` 的**进程内按轮上限原位保留**；跨进程账本门禁挂在**装配面的投放调用包装**上——新增 `core/billing/runlog.py` 的 `RecordingChannelCall`（与既有 `RecordingGateway`（`:313`）同构、同处无第二份实现），在 `create_campaign` **之前** `guard.check(request)`（`stage` = 该渠道的投放环节 id）、调用后 `reservation.settle(actual_spent)`（实测超预估如实入账 + `over_limit` 告警，019 口径不变）；超限 ⇒ **适配器调用之前拒绝**（`agents/promo/loop.py:425` 的 `create_campaign` 零次调用）、零入账、`alerts.jsonl` 留痕（`kind=budget_refused`）；拒绝理由**点名**命中的是"进程内按轮上限"还是"账本额度"（FR-008 / C14）。
5. **对账与校准复用**：投放渠道的账单导入声明落在**短剧态的** `channels.<投放渠道>.bill`（格式 id + 列映射 + 分类取值域）⇒ 直接走 019 的 `core/billing/bill.py:321` 与 `core/billing/reconcile.py:233`，**不新造第二套对账**；最小规模入口落在**投放侧**（不假定 `ops/smoke_llm.py` 的 LLM 冒烟口径，`specs/019-real-channel-billing/spec.md:102`；最小规模档 = **该渠道投放环节档位的 `limit_usd`**，不新增额度键）；扩量走 `core/billing/calibration.py` 的既有先决检查与 `core/yaml_edit.py` 的定点改写（复用，不改）。
6. **凭证矩阵 + 两形态装配面**：`ops/check_credentials.py` 增投放渠道条目（形状：渠道 → 装配引用 → `{set, length}`，**绝不回显值**、不进产物），变量名以 `agents/promo/platform/http_real.py:151-155` 为**权威**；反向机检由 `tests/unit/test_credential_env_lock.py` 承担；声明真实平台而凭证缺失 ⇒ **装配期显式拒绝启动**（`agents/pilot/backends.py:262-267` 的装配期拒绝语义），零落盘、零扣费、**绝不**静默回落模拟（C16）。**两形态都必须能装配通过**：`configs/movie.yaml` **不登记**投放渠道（投放属短剧线 C 路径）——FR-014 的"两形态均须声明"指的是**新增参数键的 schema 齐备**（两形态都能加载、缺键即报错），**不是**要求两形态登记同一渠道或取同值；电影态装配面按 `declared_channels(cfg)` 分派，该路径**不得**因投放渠道或其凭证而失败；投放凭证缺失只在**短剧态声明真实投放后端**时拦截。

## 阶段 5：P3——校准结论迁移（US3）

**TDD 序**：先写 `tests/unit/test_calibration_transfer.py`、`tests/contract/test_transfer_contracts.py`（新）

1. 新增 `core/calibration/transfer.py`：迁移件 = 来源标识（形态 / 评估器 `id@version` / 周期 / 样本量 / 来源件引用）+ 迁移口径 + **可比性条件与判定**（可迁移 / 不可迁移 + 原因）；可比性条件**由配置声明**（两形态均声明、缺项即报错）；不可迁移 ⇒ **拒绝迁移**并如实登记原因（不得静默丢弃、不得降级为"参考"）。
2. append-only 存储：镜像 `core/degraded/evidence.py` 与 019 的 `system_digest` 范式（复用 `core/billing/bill.py:406`/`:416` 的摘要与写入语义，不新造第二套）；改写尝试 100% 被拒。
3. 人工两键：**独立脚本** `ops/transfer.py`（与 019 的 `ops/billing.py` 同风格、**退出码语义一致** 0/1/2）提供 `transfer | transfer-confirm | transfer-shelve | transfer-report` 四个子命令（**薄封装**：参数解析 + JSON 输出，判定与落盘仍在 `core/calibration/transfer.py`），提案 → 采纳/搁置的状态机与 010 的 `ProposalStatus`（`core/calibration/models.py:60`）同构；**采纳不改变任何既有节点的 `eval_breakdown` 与得分**（原则一/二）。
4. 机检：短剧线数字**不得**出现在电影线证据面（"模拟/异形态数值冒充本形态证据"恒 0）；无短剧线结论可迁移时如实标注"无可迁移结论（来源缺失）"并给出继续观察条件。

## 阶段 6：端到端演示、配置登记与门禁同步

1. 新增 `ops/demo_shortdrama_feedback.py`：Mock 平台 + 夹具指标/账单，零真实花费、零外部网络、零凭证；覆盖"日级多轮零覆盖 → 同（活动, 周期）幂等拒绝 → 覆盖∧连续窗口（连续夹具通过、缺 2 天夹具不通过且断档逐段报出）→ 渠道命名空间下多渠道分派 → 投放门禁调用前拒绝 → 对账 → 迁移提案与人工采纳"；退出码语义与既有工具一致（0/1/2）。
2. **配置登记清单（逐点同步，缺一即"逃逸门禁"）**：
   - ① `tests/unit/test_form_switch.py:256` 的顶层差异集 —— `calibration` 与 `budget` 已在集合内，**新增键在既有段内** ⇒ 该断言本身不变，但差异语义由本特性自己的用例承担（019 已按此判断，口径一致）；本次新增的**取值差异**含短剧态 `budget.runs.min_window_days: 14`（电影态 7，见 research.md 决策 11）与 `budget.channels` 的投放渠道条目（**仅短剧态**）；
   - ② `tests/unit/test_config_integrity.py:23` 的 `CONFIG_CLASSES` —— `calibration` / `drift` / `budget` 三项已在表内，**不新增类**；`:46` 的 `REQUIRED_PATHS` 增"缺项即红"条目（新增的必需配置项，含 `calibration` 的量纲/窗口口径项与 `budget.channels.<id>.tiers` 的**嵌套**路径——旧扁平键仍可读，但**缺嵌套 `tiers` 即报错**）；
   - ③ `tests/contract/test_pilot_contracts.py:418` 的段差异集（**该文件内编号 C13**）—— 若该断言遍历顶层段集合，本次不新增顶层段则不变；
   - ④ `agents/pilot/pilot.py:377` 的 `config_completeness` 预检清单（`:496` 调用）—— 若新增项参与装配期检查，须登记；**同一文件的既有档位校验（`:446-448` 的 `cfg.tiers` 非空 + 逐档 `limit_usd`）按新形状改经 `tiers_of(channel_id)`，文案保留「预算不可用：budget.tiers 为空」**；
   - ⑤ `tests/conftest.py:4109` 的 `_billing_budget_payload` / `:4127` 的档位遍历 —— **必须**同步：夹具按**装配引用**收敛为**单渠道**并把该渠道档位以**旧扁平 `tiers`** 暴露（调用体零改动，同时覆盖 C11 的旧形状读路径）；`:1871` 的 `drift_config` 夹具与 `:462` 的 promo 回流快照夹具（补归属日字段）同批更新。
   登记清单的**权威面**是 019 已列的**五处**（`specs/019-real-channel-billing/quickstart.md:99` 所列），本特性只做增量登记、不新造第六处。
3. 门禁同步：`.github/workflows/billing_alerts.yml` **文件不改**（`alert-check --channel llm` 必须继续通过）；覆盖率口径不降（≥85%，含 web）；对抗 / 无偏性 / Immutable 审计 / 成本回归四条门禁零放松。

## 复杂度跟踪（新增抽象论证）

| 新增抽象 | 为什么需要 | 为什么不选更简方案 |
| --- | --- | --- |
| 新模块 `core/calibration/periods.py`（4 函数） | 周期量纲被**四个**模块共用（`core/calibration/rounds.py:69`、`core/calibration/selection.py:28`/`:108`、`core/calibration/ledger.py:84`、`core/calibration/drift_metrics.py:53-54`），必须是唯一口径；且 cadence 是参数而非形态名 ⇒ 业务无关 | *在各处就地写 `if period_days == 1`*：四处各一份口径、必然漂移，且 cadence 分支散落违反原则五的"新增配置项而非分支代码"；*把 `iso_week_label` 改成按日*：周级路径标签会变 ⇒ 违反原则一/二（历史产物与节点必须逐字节不变） |
| 新模块 `core/calibration/transfer.py` | 迁移是"结论的再利用"机制（可比性判定 + append-only + 人工两键），既非漂移检测也非权重再拟合，混进 `refit.py` 会让"自动拟合"与"跨形态迁移"两条口径纠缠 | *塞进 `core/calibration/refit.py`*：`refit.py`（`:1-30`）承接的是**本形态**的权重提案，加跨形态迁移会让"改不改权重"这一关键边界模糊（本特性**不自动迁权重**）；*写成 `ops/` 脚本*：机制无法被单元/契约测试复用，`core` 侧断言也覆盖不到 |
| 渠道命名空间 `channels.<id>.tiers.<环节>` | 原则三条款①要求"按环节分档"且 FR-007 要求"按渠道分派、同一档位不跨渠道串用"；019 的单渠道装配口径（`core/billing/budget.py:357`）在多渠道下无法表达 | *让两渠道共用一套扁平 `tiers`*：同一环节名（如 `screenplay`）在投放渠道下语义不同，共用即"串用"；*为投放单独建一份配置段*：等于第二套门禁口径，违反"禁止旁路门禁"；*改用 `tiers.<渠道>.<环节>`*（渠道在内层）：渠道集合的枚举与"未声明渠道即拒"的判定会退化为嵌套遍历，与 CLI 的 `--channel` 取值校验不直接对应 |
| 迁移 `0011_*` 的**两件 DDL**（① 锚点表加可空归属日列；② 新表 `promo_daily_metrics`） | 两件都是"必须可被存储层拒绝改写"的纪律：FR-006 要求"周期窗口按归属日过滤"，而 010 的 `AnchorScore`（`core/calibration/models.py:70`）与锚点表（`core/calibration/db.py:23`）**都没有**归属日字段 ⇒ 过滤键无处取；日级分片的唯一性键（活动, 周期）又必须落在**有触发器冻结**的记录面上（运营表 `promo_campaigns` 刻意无触发器，`agents/promo/db.py:1-5`） | *把归属日记进 `created_at`*：会把"平台时间戳"与"归属日"合并（口径澄清 C 明确两者不等同，且 `platform_timestamp` 语义不许漂移）；*新增 JSON 列*：锚点表是 INSERT-only 冻结对象，加 JSON 列会让"归属日"变成可容纳任意内容的敞口，与既有"逐列显式、列即契约"风格不符；*只落文件不落库*：过滤发生在 DB 侧（`core/calibration/selection.py:108` 读锚点候选），文件侧无法参与；*改 `promo_campaigns` 的唯一键为含周期*：运营表刻意无触发器 ⇒ 冻结纪律无处落地，且一并抬高运营状态机的兼容风险（改**新表**则既有键 `(round_id, material_id)` 零改动，`agents/promo/db.py:47`） |
| 迁移件 append-only 存储 | SC-007 要求"改写/省略尝试 100% 被拒"，且 FR-012 要求可回溯；单文件写无法证明"没有条目被删掉" | *复用 010 台账 JSONL 追加*：只有追加没有**内容摘要**，删除中间行不可发现；*落 PG 表*：迁移件是跨形态的运营产物，且会引入第二个迁移与运维面（019 决策 3 已按同一理由否决 PG 账本） |

## 风险与回滚

| 风险 | 等级 | 缓解 / 回滚 |
| --- | --- | --- |
| **周期标签口径变更污染周级历史产物**（最硬的兼容风险：010/012 的标签是既有契约面） | 高 | 硬性前置：`period_label(day, 7)` 与 `iso_week_label` 的**逐字节一致**断言先落地（阶段 2 第 1 步）；历史产物与历史节点**零回改**；回滚 = 恢复 `rounds.py:69` 的原调用（`period_label` 保留但不再接线），周级产物不受影响 |
| **半开窗口使周级判定结果与历史窗口不一致** | 中 | 属**兼容性修正**（今天缺省窗口跨 `period_days + 1` 天是缺陷）：口径进产物、跨"口径变更日"比较在 `note` 显式标注、历史已落盘件零回改；回滚 = 恢复 `selection.py:28` 的 `+1 天`，新窗口即刻回到旧口径（但口径字段仍在产物中，可辨） |
| **渠道命名空间改造触碰 019 的既有断言**（019 是刚交付的特性，回归面广） | 高 | 处理原则**只有一条**："按扩展更新、**不削弱**"（不得以删断言换取通过）；逐条穷举结论见 research.md 决策 6；`core/billing/` 的零字面量门禁（`tests/unit/test_billing_core_purity.py:110`）随新写入面同步扩展；`.github/workflows/billing_alerts.yml:27` 与 `tests/contract/test_billing_contracts.py:1146` 两条"不可削弱"的红线单列回归 |
| **归属日字段使既有适配器与夹具构造点变红** | 中 | 字段**加在末位且允许缺省**（历史行/历史快照仍可构造），存在性检查只在**新写入路径**上强制；夹具更新清单见 research.md 决策 10 |
| **真实回流 ≥2 周无法在本特性内达成** | 高（不可消除） | **诚实分层**：本特性交付机制 + 离线复现，产物写"机制已就绪 / 真实回流待运营"并给出机检到的真实覆盖天数与缺口；**禁止**以模拟回流冒充真实（`RUN_SOURCES` 取值纪律 + 覆盖只计真实来源双重守住） |
| **平台侧确无"指标归属日"字段** | 中（运营侧开放问题 1） | 不发明：按"未标定"如实标注、该条不落锚点；平台适配器**必须**在拿到该字段后才产出快照，故"未标定"期唯一后果是回流条数为 0 且原因明确（不是静默丢数据） |
| **投放门禁与 015 按轮上限口径混淆** | 中 | 两套门禁**共同生效**、拒绝理由点名命中的是哪一条；单列用例断言"两道分别可辨"（research.md 决策 7） |
| **合规审查未完成即扩量**（`docs/三期立项书.md:271` 列为高风险） | 高（业务侧） | 技术侧**不发明**审查凭据形式：未定前**不得**扩量，配置与代码里**不得**出现"视为通过"的分支；登记为业务侧开放问题 |
| **电影态被波及：装配因投放渠道或其凭证而失败**（越界把 C 路径搬进电影线） | 中 | `configs/movie.yaml` **不登记**投放渠道（FR-014 的"两形态均须声明" = **新增参数键的 schema 齐备**，不是要求两形态登记同一渠道）；装配面按 `declared_channels(cfg)` 分派 ⇒ 未声明的装配引用不参与；**两形态都必须能装配通过**（阶段 4 第 6 条），投放凭证缺失只在短剧态声明真实投放后端时拦截；回滚 = 投放渠道条目只保留在短剧态配置里 |

## 与既有特性的兼容性

| 既有特性 | 兼容面 | 本特性的处理 |
| --- | --- | --- |
| **019-real-channel-billing** | `core/billing/` 五模块、`budget:` 段、`ops/billing.py` CLI、`.github/workflows/billing_alerts.yml` | **兼容性扩展**：命名空间改造 + 放开单渠道硬拒绝；旧扁平形状仍可读（显式归入声明渠道，歧义即报错）；既有断言按扩展更新、**不削弱**；账本/告警/运行记录按渠道分目录的既有产物形状不变、历史账本零回改；不重做账单/对账/校准机制 |
| **015-pilot-shortdrama** | promo 平台适配器与回流管道、`promo_campaigns` 运营表（既有唯一键 `(round_id, material_id)` 与"刻意无触发器"的口径）、`agents/promo/config.py:70-72` 的按轮上限 | 适配器**协议不重写**（只增字段）；**运营表零改动**——日级分片记录落**新表 `promo_daily_metrics`**（唯一键（campaign_id, period）+ INSERT-only 触发器，即迁移 `0011_*` 的第二件 DDL），`promo_campaigns` 的既有键与历史行**原样保留**；`node_id` 改为**含周期的确定性派生**（如 `{material_id}-node@{period}`，`agents/promo/ingest.py:91`），**历史 `node_id` 不回改**；按轮上限**保留并存**（不与 019 账本混同）；既有"一次活动一次快照"的历史行**仍可读、不被误判为另一次采集** |
| **010-weekly-calibration** | 台账 / 快照 / 报告 / 轮次 / 锚点表与触发器 / 权重提案 | 周级路径**逐字节不变**（标签派生在 `period_days == 7` 时等价）；`insert_anchor` 仍是锚点**唯一接口**；锚点唯一键与 INSERT-only 触发器**不动**；快照是"周期物化"而非"某一轮产物"⇒ 路径形状不变、`core/calibration/drift_metrics.py:140-142` 的只读读取面不变；提案状态机复用为迁移件的人工两键 |
| **012-judge-drift-detection** | 漂移配置 / 检测记录 / 报表 / 门禁 | 漂移窗口单位随 cadence（周级=5 周不变；`configs/movie.yaml` 的 `window: 5` 语义与取值不变）；窗口单位进 `metric_hash` ⇒ 口径变更体现为**新 `detector_version`**；检测记录 append-only 语义不变（`core/calibration/drift_metrics.py:331-348`）；010 产物**零写入**的既有纪律不破 |
| **013-frontend** | `web/` 只读视图读 `reports/` 与 `drift/reports/`（`web/parity.py` 的周期路径） | 报告文件仍按周期命名（周级路径不变；日级新增 `YYYY-MM-DD` 形状）；前端仍**只读**，本特性**不加 web 写入口**；若 `web/parity.py` 有周期形状断言 ⇒ 列入 research.md 决策 10 的变红清单 |
| **018-feature-film-pipeline** | `pilot` 段与装配点 `agents/pilot/backends.py:280`、最小规模校准入口 | 装配点按装配引用解析渠道（不再 `sole_channel`）；`ops/smoke_llm.py:206` 同口径；`pilot` 段的体量档与排练档**不动** |
| **017-dev-agent-degraded** | 宪章原则四的例外条款与 `core/degraded/evidence.py` 的摘要范式 | 020 **不适用**该例外（不新增策略代码）；append-only 范式**镜像** `core/degraded/evidence.py`（复用 `core/billing/bill.py:406`/`:416` 的既有实现），不新造 |

## 本特性不做什么（写进计划与规格防回潮）

- **不做 G5 多形态插件验证**（`docs/三期立项书.md:167` 的 `021-form-plugin-validation`）：本特性只做短剧形态的机制闭环，**不**新增形态、**不**做插件化评估器接入；
- **不做 B 路径真实生成对接**：生成侧厂商凭证、协议校准与计费口径属 G2 结转项与运营侧输入，`docs/pilot-upgrade-manifest.json` 的 B 路径 `status: not_delivered` 不变；
- **不做多租户、不做公网服务化**（三期不做项不变）：无服务端、无鉴权面、无多租户隔离；
- **不做自动权重迁移**：只做**结论迁移**，权重再拟合仍走 010 的提案 → 人工确认路径（原则一：`human` 锚点与评估器版本冻结）；
- **不发明平台名、凭证变量名与预算档数字**：它们是运营侧输入（`docs/三期立项书.md:280`），未给定期间如实登记"未标定"、按最小规模档运行；
- **不做旁路门禁**：投放**必须**走 019 的门禁（原则三），不得为媒体渠道另造一套；
- 沿 019 的不做项继续不做：**Decimal 金额重构**、**多主机共享额度**、**适配器协议重写**、**`CostRecord` 增列角色/档案**、**web 侧写入与 billing 看板**。

## 宪章复核（阶段 1 后）

原则三 v2.0.0 的三条新增条款在**媒体渠道**上的落实分别由 **C11**（渠道命名空间 `channels.<id>.tiers.<环节>` 与旧扁平形状兼容读）、**C12**（装配与额度按配置声明的渠道集合分派：`declared_channels(cfg)` / `channel_for_adapter(cfg, adapter_id)` / `tiers_of(channel_id)` / `tier_of(channel_id, tier_id)`，未声明渠道仍退出 2）、**C14**（投放申请前置门禁：调用前拒绝、平台 0 次调用、零入账、与 015 进程内按轮上限**可辨**的拒绝分型）、**C16**（最小规模先行 + 凭证就绪矩阵与装配期拒绝启动）承载；**C13** 逐条举证"019 既有断言零删除、零放宽"（12 个 `--channel` 实参 + 每日工作流）。**原则一**由 **C1/C3/C4/C8** 承载：周期标签在周级下逐字节不变、快照不重写（周期物化且内容变化必记账）、漂移窗口单位随 cadence 进口径哈希、锚点写入仍走唯一 INSERT 接口且归属日只加一列（历史行 NULL 不回改）。**原则二**不受影响：发现树、`CostRecord`、`eval_breakdown` 与得分零改动，日级分片是**追加**而非改写。**原则五**由 `periods.py` / `transfer.py` 的业务无关性（cadence 作参数、零形态分支）+ 全部新参数两形态声明 + 零新增分支代码承载。**原则六**由**归属日来源可指认**（平台显式字段、缺失即失败并给原因，C7）、**窗口口径进产物**（C5）、**漂移所读快照可追溯**（指纹 + 锚点数，C4）、**报告三时间可见**（C9）、**断档逐段报出不插值**（C9/C18）、**可比性不成立即拒绝迁移**（C15）、**人工两键**（C15）承载。

**门禁通过**；三项前提——(a) `period_label(day, 7)` 与 `iso_week_label` 的逐字节一致断言必须**先于**标签接线落地（否则原则一失守）；(b) 019 的既有断言必须以**扩展**方式更新且**不得削弱**（`tests/contract/test_billing_contracts.py:1146` 与 `.github/workflows/billing_alerts.yml:27` 两条红线单列回归）；(c) 归属日字段在两个适配器上"缺失即失败"的断言必须常驻（否则口径澄清 C 的"禁止兜底"退化为注释）。
