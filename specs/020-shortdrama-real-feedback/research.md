# 调研：短剧形态的真实投放与日级回流（020-shortdrama-real-feedback）

> 阶段 0 产出。每条决策 = **问题 → 决策 → 依据/理由 → 被否决 → 影响面**。规格依据见 [spec.md](spec.md)，
> 架构落点见 [plan.md](plan.md)。本特性与 019 的关系是**兼容性扩展**（019 交付 `core/billing/` 五模块
> 与两条真实渠道纪律，并把媒体投放渠道整条结转 G4，`specs/019-real-channel-billing/spec.md:18`）；
> 与 015/010/012 的关系是**复用口径 + 逐字节兼容**。
>
> **命名纪律**：本文**不发明**字段名、数字与平台名。凡涉及"新字段叫什么"，一律指向
> `contracts/daily-ingest.md`（C7/C8）与 `data-model.md` 的定名；凡涉及"取值多少"，一律指向
> `configs/*.yaml` 的运营侧填值 + "未标定"如实标注。

## 决策 1：周期标签由 cadence 派生，**周级逐字节不变**（`core/calibration/periods.py`）

**问题**：`configs/shortdrama.yaml:399` 已声明 `calibration.period_days: 1`（对照 `configs/movie.yaml:392`
的 `7`），但周期标签**一律**取 ISO 周——`core/calibration/rounds.py:28` 的 `iso_week_label` 在 `:69` 被用于
`close_round`，另有 `ops/calibrate.py:190`、`ops/screenplay.py:473`、`ops/dev.py:423` 三处调用点。标签决定
台账 `BiasRecord.period`、快照文件名（`core/calibration/ledger.py:84`）、信度报告文件名
（`core/calibration/report.py:55`）与漂移检测的周期正则（`core/calibration/drift_metrics.py:53`）
⇒ **同一 ISO 周内的多轮日级运转互相覆盖**（见决策 3/4）。规格已裁决量纲（Clarifications 第 7 条）。

**决策**：新增业务无关模块 `core/calibration/periods.py`，四个函数：

- `period_label(day, period_days)`：`period_days == 1` ⇒ `YYYY-MM-DD`；`== 7` ⇒ ISO 周 `YYYY-Www`；
  **其他值 ⇒ 显式 `ValidationError`「未支持的 cadence」**（不发明量纲、不猜、不退化）；
- `period_start(label, period_days)`：标签 → 该周期首日（日级即标签本身，周级取 ISO 周首日）；
- `period_window(start_day, period_days)`：**半开** `[start, start + period_days)`（见决策 2）；
- `period_regex(period_days)`：该 cadence 下的标签正则（替代 `core/calibration/drift_metrics.py:53` 的固定周正则）。

**周级逐字节不变的做法**：把 `iso_week_label` 的**唯一实现**移入 `periods.py`，`period_days == 7` 走
同一份代码；`core/calibration/rounds.py` 保留 `iso_week_label` 并**再导出**（`from core.calibration.periods
import iso_week_label`），使 `ops/screenplay.py:473`、`ops/dev.py:423`、
`tests/contract/test_screenplay_calibration.py:206` 的既有 `from core.calibration.rounds import iso_week_label`
**零改动继续可用**（不可反向：`periods.py` 不 import `rounds.py`，否则成环）。

**依据/理由**：① 标签被四个模块共用 ⇒ 必须唯一口径，否则四处各写一份 `if period_days == 1` 必然漂移；
② 按 `period_days` 参数化而非读形态名的原因是**原则五**：`core/` 不得出现形态分支，cadence 是**数值参数**
（`1`/`7` 的判定即量纲本身，不属于"形态分支"——形态分支是 `if form == "shortdrama"` 这类按名判断，
`tests/unit/test_form_switch.py:256` 的差异集合断言与零分支断言已把这条钉死）；③ "其他值显式拒绝"是
**原则六**的直接推论：不发明第三档量纲（如"双周""小时"），也不静默退化为周级——静默退化会让
`configs/shortdrama.yaml:408` 那种"注释说 3 天、实际 3 周"的名不副实重演；④ 周级逐字节不变是
**原则一/二**的硬约束：010 的周级产物与历史节点已是冻结证据，标签一旦变化即等于改写历史。

**被否决**：
- *把 `iso_week_label` 直接改成"按 `period_days` 分支"*：四处调用点各自要拿到 cadence，函数签名被迫膨胀；
  且"周级语义"与"日级语义"混在一个以"周"命名的函数里，命名即误导。
- *新增 `iso_day_label` 并让日级单独走一条路*：两条路径 = 两套标签口径，台账/快照/报告的拼路径代码
  会出现按量纲分支（正是原则五要禁止的"新增分支代码"）。
- *支持任意 `period_days`（如 3 天、14 天）*：规格只裁决了 `1` 与 `7` 两档；多档会立刻引出"非 ISO 周的
  周期首日怎么定"（锚定哪一天？月份边界？）这一**尚未裁决**的量纲问题 ⇒ 按"不发明"拒绝。
- *把 cadence 写进 `core/calibration/config.py` 的模块常量*：等于码内默认值，违反 FR-014
  "缺项即报错、不取码内默认"。

**影响面**：
- 新增 `core/calibration/periods.py` 与其单元套件；`core/calibration/rounds.py:69` 改调用形态
  （`period_label(round_.period_end, config.period_days)`），`CalibrationConfig` 需可读取 `period_days`
  （既有字段，`core/calibration/config.py:24`）；
- `ops/calibrate.py:190`、`ops/screenplay.py:473`、`ops/dev.py:423` 改为经 `periods.period_label`（周级取值不变）；
- `ops/calibrate.py` 的 `propose` 与 `close` 两条子命令的 period 取值口径必须一致（今天同为
  `iso_week_label`，改后同为 `period_label`），否则台账与提案会看到两个周期；
- 契约落点：`contracts/period-cadence.md` **C1**。

## 决策 2：窗口统一半开 `[start, start + period_days)`；口径进产物、跨变更日必须标注

**问题**：`ops/calibrate.py:43` 的缺省 `period_start = 今天 − period_days`，而
`core/calibration/selection.py:28` 的 `_period_window` 把 `period_end` **加一天**（注释即写"含首尾"）
⇒ 缺省窗口实际跨 `period_days + 1` 天（日级下即"2 天窗"）。规格已裁决统一半开（Clarifications 第 8 条）。

**决策**：半开窗口落在**两处既有实现点**（`core/calibration/selection.py:28`、`ops/calibrate.py:43`），
由 `periods.period_window` 提供唯一实现；`end - start == period_days` 作为**机检条件**（按日历日）；
产物**必须**写 `window_semantics = "half_open"` 与 `period_days`；**跨"口径变更日"的窗口比较**必须在
报告 `note` 里**显式标注该变更日**，禁止静默比较；历史已落盘产物**零回改**。

**口径变更日从哪来**：由配置声明（`configs/*.yaml` 的 `calibration` 段新增项，**两形态均须声明、缺项即报错**），
即"新口径自哪一天起生效"。这样"变更日"是一个**可审计的配置事实**，而不是实现里的隐含假设。

**依据/理由**：① 半开是既有代码的**主口径**——`:28` 的返回注释本身就写 `[start, end)`，只是 `+1 天`
让实际区间变成 `period_days + 1` 天；故这是**兼容性修正**而非口径重设；② `end - start == period_days`
是唯一能把"含首尾"错误永久挡住的可机检条件（`SC-002` 明确要求"`period_days + 1` 天窗口出现次数恒为 0"）；
③ "口径进产物 + 跨变更日标注"回答的是**原则六**的问题：两份周级 → 日级的窗口如果可比性不同口径，
必须让读者看得见，而不是给一个"看起来正常"的差分；④ "历史零回改"是**原则一/二**：已落盘的报告与
已冻结的结论不允许回头修。

**被否决**：
- *保留含首尾、只在文档里说明*：`period_days + 1` 天窗口与"日级周期=1 天"直接矛盾，且不可机检。
- *含首尾但不去重（即把 `period_end` 当天算入两天窗）*：日级下"今天既是今天的窗口也是昨天的窗口"，
  同一锚点会同时进入两个周期 ⇒ 台账重复计数，直接破坏"同周期多轮零覆盖"的可判定性。
- *不做口径标注，只在 CHANGELOG 里记*：CHANGELOG 不是产物，`SC-002` 要求的是**产物级**可机检
  （`window_semantics`/`period_days` 写入率 100%）。
- *重算并回写历史报告以统一口径*：改写冻结产物，违反原则一/二；且 010 的契约用例
  （`tests/contract/test_calibration_contracts.py:1-10` 的"逐字节一致"机检）会立刻变红——这正是
  不该做的事。

**影响面**：`core/calibration/selection.py:28`（`_period_window`）、`ops/calibrate.py:43`（缺省起点）、
`ops/calibrate.py:150`（`report` 子命令输出）、`core/calibration/report.py:20-58`（报告增口径字段与 `note`）、
`core/calibration/config.py:33`（新增必需配置项）、两份形态配置；契约落点
`contracts/period-cadence.md` **C2/C5**。

## 决策 3：快照 = **周期物化**；漂移"每周期读一份"并登记所读快照指纹与锚点数

**问题**：010 的快照语义在代码里是"某轮落一份"（`core/calibration/ledger.py:55` 的 `write_anchor_snapshots`
按 `(agent, evaluator, period)` 单文件 `write_text`，同周期多轮会**覆盖**），而 012 的读取语义是"每周期
恰好一个文件"（`core/calibration/drift_metrics.py:140-142` 的 `snapshot_path` 只读、`:145-151` 缺文件返
`None`）。规格已裁决：快照是**周期物化**（Clarifications 第 12 条）。

**决策**：
- **主键 =（评估器, 周期）**，路径形状**不变**（`snapshots/{agent}/{evaluator}/{period}.json`，
  `core/calibration/drift_metrics.py:140-142` 的只读读取面零改动）；
- 内容 = 该周期**全部**锚点的分布（含迟到/回补后到达的锚点）；
- 漂移**每周期读一份**（读取时刻取该周期**最后一次物化**；缺文件 ⇒ 该周期**不入窗口**、缺口如实报，
  沿用 `read_snapshot` 返回 `None` 的既有语义）；
- 同周期多轮若使快照**内容变化** ⇒ **必须记账**：台账行登记该周期的**锚点数**与**快照指纹**
  （内容哈希）；"内容变了但无人知道"的次数恒为 0（SC-002）；
- 漂移产物**必须**登记**所读快照的指纹**与**该周期锚点数**，使"读的是哪一份"可追溯。

**依据/理由**：① 漂移比较的是**分布**，分布的自然粒度就是"一个周期一份"；把它做成"一轮一份"会让
同周期多轮产生互相矛盾的基线（每次读到的都是最后一轮写的，但那不是"该周期的分布"）；② "内容变化必须
记账"是**原则六**：迟到/回补会真实改变分布，这一改变必须留痕（否则漂移结论的输入不可复算）；③
"登记读的是哪一份"回应的是**可证伪性**——只有指纹可见，"这次漂移判定用的是哪份快照"才可指认；
④ 路径形状不变是**兼容性**要求：012 的既有产物、`web/` 的只读读取面（`web/parity.py` 的周期路径）
与 010 的历史快照全部按该形状落盘，改形状即等于改写历史。

**被否决**：
- *一轮一份快照（路径带轮标识）*：012 的读取面（`snapshot_path`）要改、历史快照要迁移 ⇒ 触碰冻结证据；
  且"周期物化"的语义被降级为"轮次产物"，与规格裁决相反。
- *拒绝同周期第二次物化（只认第一次）*：迟到/回补的锚点将**永久无法进入分布**，而规格明确要求
  "迟到/回补按归属日归入对应周期"（FR-006）⇒ 拒绝 = 丢数据。
- *静默改写（允许覆盖但不记账）*：SC-002 的"快照静默改写次数恒为 0"直接失守。
- *把指纹算在读取侧*：读取侧算指纹只能证明"我读到的和当时一致"，不能证明"物化过程中变过几次"；
  记账必须发生在**写入侧**。
- *登记锚点数用快照内既有的 `samples` 字段代替台账行*：`samples` 在 `core/calibration/ledger.py:79`
  已存在（快照内容的一部分），但**台账行**是"何时物化了什么"的时间序列，两者不是同一个量；时间序列
  必须落在台账（append-only）上。

**影响面**：`core/calibration/ledger.py:25`（`append_ledger` 的登记字段）、`:55`（`write_anchor_snapshots`
的物化语义与记账）、`core/calibration/drift_metrics.py:358-454`（`detect_drift` 登记指纹与锚点数）、
`core/calibration/drift_models.py:179`（`DriftMetrics` 增可选登记字段）；契约落点
`contracts/period-cadence.md` **C3**（快照 = 周期物化：主键（评估器, 周期）+ 零覆盖 + 内容变化必记账）
与 **C4**（漂移读取口径：每周期一份 + 窗口同量纲 + 指纹/锚点数登记）、
`contracts/daily-ingest.md` **C9**（报告三时间可见 + 覆盖按归属日）。

## 决策 4：日级分片的唯一性键与幂等边界——**两层规则、分属不同层、不得互相吞并**

**问题**：现状是"一次投放活动一次快照"——`agents/promo/db.py:33` 注明 `node_id`"落盘后回填，回填即终态
不再变"、`:44` 的 `metrics`"回流后写入一次"，回流只处理 `status == "delivered"` 的行
（`agents/promo/ingest.py:58`）并随即置为 `ingested`（`:130`），运营表唯一键是
`(round_id, material_id)`（`agents/promo/db.py:47`）⇒ **同一投放活动无法连续多日各采一次指标**。
规格已裁决"按采集日分片 + 唯一性键（活动, 周期）"（Clarifications 第 9 条）并**明确区分两层**
（第 12/14 条）。

**决策**：两条规则**并存**、各管一层：

| 层 | 唯一性/幂等规则 | 违规后果 |
| --- | --- | --- |
| **快照层（按事件）** | 唯一性键 = **（活动, 周期）**；同一活动跨多日 = **不同周期** ⇒ 合法追加新快照；同一（活动, 周期）重复回流 ⇒ **幂等拒绝**（零变更） | 拒绝该条，**整批不中断**；`node_id` **不再是**终态唯一标识 |
| **外环产物层（按轮）** | **同一周期内的多轮**各自的台账行与信度报告**必须并留存**（文件名带轮标识）、**零覆盖** | "后写覆盖前写"出现次数恒为 0（SC-002 机检） |

**依据/理由**：① 两层回答的是两个不同问题——快照层回答"这批指标是否已经采过"（防重复计费与重复证据），
外环产物层回答"同一天的第二次校准是否覆盖了第一次的结论"（防结论被静默改写）；把它们合并成一条规则
必然顾此失彼（合并为"拒绝"⇒ 日级多轮无法运转；合并为"覆盖"⇒ 结论可被静默覆盖）；② "外环产物按轮
并留存"是对既有实现的**修正**：`core/calibration/report.py:55` 今天把报告写成 `reports/{period}.json`
单文件，`:36` 甚至"同周期取末行"（注释即写"同周期可能追加多次，取末行"）——这在周级下无碍（一周一收口），
在日级下会让"同一天多轮"只剩最后一轮；③ "整批不中断"沿用 010 锚点录入的既有语义
（`core/calibration/anchors.py:74-130` 的逐条拒绝 + 计数，`:41` 的唯一键在 DB 层兜底），不新造；
④ `node_id` 口径必须显式降级："回填即终态"这句话在多日分片下**不再成立**（同一活动会有多个节点），
若只在代码里改实现而不同步更新该口径，后续读者会按"终态唯一"误解数据。

**兼容规则**：**历史"一次活动一次快照"的行仍可读，不得被误判为另一次采集。** 落地方式：唯一性键的
扩展对历史行**只读成立**（历史行缺少周期键 ⇒ 按其 `created_at` 的日期回退解释，如实标注，不回改）；
判"是否已采集过"时以（活动, 周期）为键、周期由归属日派生（见决策 5），历史行按回退口径参与判定但
**不因回退而被视为已被采集**（否则历史行会阻止第一次真正的日级采集）。

**被否决**：
- *唯一性键只加"采集日"而不加"周期"*：采集日与归属日会分离（迟到/回补时必然不同，规格 FR-006），
  用采集日做键会让"同一归属日、不同采集日"的两条**都通过**⇒ 同一周期被采两次、分布重复计数。
- *唯一性键只加"周期"而不管采集日*：一天内多次采样会被全部拒绝，而运营侧"当日重跑"是常态
  （当日 `delivered` 行可能分两批回流）——规格对"同（活动, 周期）"的裁决正是**拒绝**，故本项被否决
  的理由是它与规格裁决冲突而非工程不便；实际落地以规格为准（拒绝）。
- *外环产物也按（活动, 周期）拒绝*：会把"同一周期内第二次校准轮"变成错误，而规格明确要求多轮并留存。
- *让 `node_id` 继续做终态唯一标识*：`node_id` 由 `material_id` 派生（`agents/promo/ingest.py:91` 的
  `f"{row.material_id}-node"`），多日分片下必然撞主键 ⇒ 第二次采集被 `DuplicateError` 静默跳过
  （`:119-122` 今天把它当"幂等已落盘"）——**这正是"多日采集不可能"的实现根因**。裁决处置：`node_id`
  改为**含周期的确定性派生**（如 `{material_id}-node@{period}`，仍由 `round_id`/`material_id`/周期
  确定派生、可复算），**历史 `node_id` 一律不回改**（已落盘节点是冻结证据，原则一/二）；唯一性键本身
  落在新表 `promo_daily_metrics` 上（决策 9）。
- *把唯一性键加在 `promo_campaigns` 的既有键上*：该表**刻意无 immutable 触发器**（`agents/promo/db.py:1-5`），
  冻结纪律无处落地。裁决处置：新增 `promo_daily_metrics`（唯一键（campaign_id, period）+ INSERT-only
  触发器），`promo_campaigns` 的既有唯一键 `(round_id, material_id)`（`:47`）**原样保留**（决策 9）
  ⇒ 运营状态机与历史行零改动。

**影响面**：`agents/promo/db.py:33/44/47`（既有键与口径不改；新增 `promo_daily_metrics` 表定义）、
`agents/promo/daily.py`（新增：分片记录的写入与同键幂等拒绝）、`agents/promo/ingest.py:58/90-135`
（`node_id` 改含周期派生，**历史值不回改**）、`agents/promo/anchors.py:43-45`（按 `status == "ingested"`
全表取行，多日分片下需按周期过滤）、`ops/migrations/versions/0011_*.py`（新表 + 触发器，决策 9）；
`core/calibration/ledger.py:84`（快照文件名不变但语义变为周期物化）、`core/calibration/report.py:36/55`
（按轮并留存）；契约落点 `contracts/daily-ingest.md` **C6**（日级分片与（活动, 周期）唯一性）、
`contracts/period-cadence.md` **C3**（快照 = 周期物化与零覆盖）。

## 决策 5：归属日的来源 = 平台接口**显式给出**的指标日期；缺失即失败，历史锚点按 `created_at` 回退

**问题**：规格把"周期归属"裁决为**归属日**（平台指标所描述的日期，不是采集时刻，
Clarifications 第 11/15 条），但今天**无从取得**：`agents/promo/platform/base.py:79` 的 `MetricSnapshot`
字段只有 `ctr` / `completion_rate` / `conversions` / `impressions` / `clicks` / `platform_timestamp` /
`data_version`；`agents/promo/anchors.py:22-28` 的 `_snapshot_created_at` 把 `platform_timestamp` 当
"真值产生时刻"（缺失才以采集时刻兜底）；`core/calibration/selection.py:108` 的周期窗口按节点
`created_at` 过滤，而回流节点的 `created_at` 取**采集墙钟**（`agents/promo/ingest.py:117` 的 `time.time()`）。

**决策**：
1. `agents/promo/platform/base.py:71` 的 `MetricSnapshot` 增**平台指标日期**字段，**字段名钉死 `metric_date`**
   （`contracts/daily-ingest.md:99-130`），数据类形态 = **末位可选** `metric_date: str | None = None`
   ——取可选的理由只有一个：兼容 `agents/promo/evaluators/platform_metrics.py:46-54` 从 dict **重建历史行**。
   **机检边界（必须写清，不容含糊）**：
   ① **新采集写入路径**产生 `metric_date is None` 的次数**恒为 0**（两个适配器缺该字段即失败，**不得**构造
   `None`、**不得**以拉取/采集时刻兜底）；
   ② 历史重建缺该字段 ⇒ 按 `created_at` 的**日期**回退，并在报告登记"**归属日缺失锚点数**"（如实标注，
   不是静默补值）；
   ③ `platform_timestamp`（`agents/promo/platform/base.py:79`）语义不变（真值产生时刻）、与 `metric_date`
   **不等同**；命名约束：不复用 `platform_timestamp`、不复用 `data_version`（那是平台数据版本）。
2. **两个适配器都必须产出该字段**：`agents/promo/platform/simulated.py:94-112`（缺省取自身
   `platform_timestamp` 的 UTC 日期 ⇒ 与 `:110` 的固定时间戳同源、逐字节可复现；可显式注入以跨日推进
   夹具，**禁止**由 `now()` 派生——确定性纪律）与 `agents/promo/platform/http_real.py:208-227`（在
   `_snapshot` 里读平台指标响应，**缺失即 `MetricValidationError`**，沿用该函数既有的"缺字段即拒、
   不补零"风格；格式不符亦拒）。
3. **缺失即显式失败**并给出原因「**平台未提供指标归属日**」；该条**不落锚点**、不参与周期归属；
   **禁止**以拉取/采集时刻兜底（兜底会把缺口 E 原样重新引入）。
4. `platform_timestamp` **保持**"平台时间戳（真值产生时刻）"语义；平台真值锚点的 `created_at` **保持**
   平台时间戳、回流节点 `created_at` **保持**采集墙钟；**归属日 / 采集墙钟 / 平台时间戳三者并列可见**
   于报告（SC-003）。
5. 周期窗口**按归属日过滤**（`core/calibration/selection.py:108` 的过滤键改造）；迟到/回补按**归属日**
   归入对应周期，**不得**静默改写已冻结的历史锚点。
6. **兼容**：历史锚点无该字段 ⇒ 允许按 `created_at` 的日期**回退**，并在报告登记"**归属日缺失锚点数**"
   （如实标注）；**新写入不得走回退**。
7. 若平台侧确不提供该字段 ⇒ 记入开放问题 1 由运营侧确认；未确认期间**不得发明**、如实标"未标定"，
   后果是回流条数为 0 且原因明确（不是静默丢数据）。

**依据/理由**：① 归属日是**周期归属**的唯一依据，而周期是台账/快照/报告/漂移四处的分片键 ⇒ 归属日一旦
可以被"采集时刻"顶替，"周期"就变成"采集时间的函数"，迟到/回补必然落错周期；② "禁止兜底"是**原则六**
"指标口径必须可被证伪"的直译——兜底不可证伪（读者无法区分"平台给的日期"与"我们拉取的日期"）；
③ "两个适配器都必须产出"是防止"模拟侧有、真实侧没有"的口径分叉（模拟平台是可运行基线，真实平台是
生产面，两者字段必须同构，否则真实接入当天才会暴露缺口）；④ 历史回退 + 回退计数是**原则一**与**原则六**
的交点：历史锚点不可改写（原则一），但也不能假装它们有归属日（原则六）⇒ 只能回退 + 如实登记；
⑤ 三者并列是**可证伪性**要求：报告里只有归属日就无法发现"归属日被误当成平台时间戳"，只有平台时间戳
就无法发现"采集墙钟落后于指标日期"（迟到）。

**被否决**：
- *以采集时刻（`time.time()`）兜底*：规格明文禁止；且会把迟到/回补的条目错误归入采集日。
- *以 `platform_timestamp` 当作归属日*：两者是不同量（规格 SC-003 明确"把 `platform_timestamp` 当归属日
  的次数恒为 0"），且 `platform_timestamp` 是秒级浮点、语义为"真值产生时刻"。
- *以 `data_version` 或 `external_id` 派生归属日*：语义错位（前者是数据版本、后者是平台活动 id）。
- *回退口径也写进新锚点*：新写入必须非空，否则"归属日缺失锚点数"永远降不下来。
- *把归属日只落在文件（报告/快照）而不落锚点表*：周期窗口过滤发生在 DB 侧（`core/calibration/selection.py:108`
  读的是 010 的锚点候选），文件侧无法参与 ⇒ 见决策 9。
- *为归属日新增一套"平台字段名"配置项*：把协议字段带进配置面会制造"同一平台多种写法"的配置漂移
  （019 决策 5 已就同类问题作出同一判断）。

**影响面**：`agents/promo/platform/base.py:71-92`、`agents/promo/platform/simulated.py:94-112`、
`agents/promo/platform/http_real.py:194-236`、`agents/promo/evaluators/platform_metrics.py:46-54`
（**注意**：该评估器从 `raw` 字典**重建** `MetricSnapshot`，历史行缺该字段 ⇒ 重建路径必须容忍缺失，
故字段在数据类上取**末位可选**、存在性检查放在**新写入路径**）、`agents/promo/anchors.py:22-28/57-67`、
`agents/promo/daily.py`（新增：归属日归入与幂等拒绝的落点）、`agents/promo/ingest.py:33/58/117/130`、
`core/calibration/selection.py:108`、`core/calibration/models.py:70`、
`core/calibration/db.py:23`；**夹具与既有用例**见决策 10；契约落点 `contracts/daily-ingest.md` **C7/C8**。

## 决策 6：019 渠道命名空间与 `sole_channel()` 收敛口径（**穷举全部 `--channel` 调用点**）

**问题**：019 的门禁面**硬约束单渠道**——`core/billing/budget.py:357` 的 `sole_channel()` 明写
"`budget.channels` 必须恰好声明一个渠道，实际 …：装配面不猜用哪个（多渠道需先补齐按渠道分派的装配口径）"；
`:275` 的 `tiers` 是**扁平**的"环节 → 额度"；`:433` 的 `assemble_guard` 经 `sole_channel` 取渠道；
`ops/billing.py:102-110` 的 CLI 同样经它校验，`ops/billing.py:113-119` 的 `_read_tier_rows` 直接遍历
扁平 `cfg.tiers`；守卫 `check`（`core/billing/budget.py:824`）在 `:841`/`:848` 也直接查扁平表。规格已裁决补命名空间并放开
单渠道硬拒绝（Clarifications 第 10 条）。

**决策**：

1. **命名空间**：`tiers` 由扁平改为 `channels.<id>.tiers.<环节>`（形态配置层）；`BudgetConfig` 的对外
   取值面改为"按渠道解析档位"——接口定名统一为 **`declared_channels(cfg)`** 与
   **`channel_for_adapter(cfg, adapter_id)`**（另有 `tiers_of(channel_id)` / `tier_of(channel_id, tier_id)`；
   **弃用"槽位（slot）"这一命名**），既有 `tiers`（`core/billing/budget.py:275`）与 `tier()`（`:318`）
   降级为**单渠道兼容视图**；`:344` 的 `to_snapshot()`、`:841`/`:848` 的守卫查表、`ops/billing.py:119`
   的行渲染全部改经渠道。**同一档位不得跨渠道串用**（账本/告警/运行记录已按渠道分目录，
   额度亦按渠道分子表）。
2. **放开单渠道硬拒绝**：`sole_channel()` 的语义从"必须恰好一个"改为"**按配置声明的渠道集合分派额度**"：
   装配面**按装配引用**解析唯一渠道——`channels.<id>.adapter` 是既有的"真实调用面的装配引用"字段
   （`core/billing/budget.py:259`），且**其取值域不变**：既有值 `pilot_llm` **原样保留**（**不做**"收敛为
   槽位 id"的改造），新增投放渠道的 `adapter` 按其**真实装配入口**命名即可。装配点声明自己是谁
   （`agents/pilot/backends.py:280` 是链内唯一装配点、`ops/smoke_llm.py:206` 是校准入口、投放侧是投放
   装配点），按 `adapter` 反查出**唯一**渠道；**同一装配引用对应多个渠道 ⇒ 歧义即报错**（不猜）。
   渠道 id 仍**全部来自配置**，`core/billing/` 零渠道字面量不变（`tests/unit/test_billing_core_purity.py:151`）。
   新增一条与"渠道登记面"有关的边界（**对应裁决 4**）：`configs/movie.yaml` **不登记**投放渠道（投放属
   短剧线 C 路径）；FR-014 的"两形态均须声明"指**新增参数键的 schema 齐备**（两形态都能加载、缺键即报错），
   **不是**要求两形态登记同一渠道或取同值；**两形态都必须能装配通过**——电影态装配面按
   `declared_channels(cfg)` 分派（未声明的装配引用不参与），**不得**因投放渠道或其凭证而失败。
   **跨件差异须同步（本计划不动对方文件）**：`contracts/channel-budget.md` 的 C11 现写"`adapter` 取值域
   固定为装配面槽位 id、既有值 `pilot_llm` 更新为 `llm`"、C12 写 `channel_for_slot`——按本裁决，
   **这两处表述作废**（保留 `pilot_llm`、接口名为 `declared_channels` / `channel_for_adapter`）。
3. **旧扁平形状仍可读**：读到扁平 `tiers` 时，按迁移规则**显式归入其声明的渠道**——扁平形状只允许在
   "配置恰好声明一个渠道"时使用（此时归属唯一、无歧义）；**多渠道 + 扁平形状 ⇒ 报错**（不得静默误判为
   某一个渠道）。扁平形状**不迁移、不改写**（历史账本与历史节点零回改）。
4. **CLI 语义保留、不得削弱**：`ops/billing.py:102-110` 的 `--channel` 校验改为"取值必须在
   `budget.channels` 声明集合内，否则**退出 2**"；未声明渠道**仍必须退出 2**（这条是红线）。

**`tests/contract/test_billing_contracts.py` 全部 `--channel` 调用点逐条结论**（穷举，共 12 处；口径与
`contracts/channel-budget.md` 的 C13 表逐行一致：**保留** = 断言与退出码一字不改；**更新** = 断言强度不变、
只改实现点或文案子串；**本表零"删除"结论**）：

| # | 位置（均在 `tests/contract/test_billing_contracts.py` 内） | 子命令与取值 | 现行断言 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | `:995` | `import-bill --channel <声明渠道>`，取值来自 `:986` 的 `next(iter(BudgetConfig.from_yaml(config).channels))` | 退出码 0，账单落 `billing/{channel}/bills/` | **保留**（C13 表第 1 行）：夹具为单渠道 ⇒ `_channel(cfg)`（`:146-147`）仍取到声明渠道；实现点与断言一字不改 |
| 2 | `:1013` | `reconcile --channel <声明渠道>`（取值同 `:986`） | `unexplained` 非空且退出码 ≠ 0 | **保留** |
| 3 | `:1037` | `alert-check --channel <声明渠道>` | `code == 1 and has_alerts is True` | **保留** |
| 4 | `:1048` | `reconcile --channel <声明渠道> --bill-id ghost`（取值来自 `:1042`） | `code == 1` 且错误含「拒绝产出」，不产"零差异"报告 | **保留**（019 的红线之一） |
| 5 | `:1144` | `tiers --channel <声明渠道>`（取值来自 `:1143`） | 退出码 0 | **保留**，且**须在多渠道配置下也 0**——这正是"退役 `sole_channel()` 硬拒绝"的验收点 |
| 6 | `:1146` | `tiers --channel "nope"` | 退出码 **2**（未声明的渠道不得开工） | **保留（硬要求，不得改）**：实现点 = `_channel_of` 改按声明集判定（逐点指向见本表后的实现点说明） |
| 7 | `:1149` | `runs --channel <声明渠道> --end …` | `code == 1 and meets is False` | **保留**（窗口口径未变） |
| 8 | `:1155` | `raise-tier --channel <声明渠道> --calibration ghost` | `code == 1 and reason == "uncalibrated_raise"` | **保留**：`--tier` 改经 `cfg.tier_of(channel_id, …)` 解析（`screenplay` 在 LLM 渠道下仍声明 ⇒ 取值不变） |
| 9 | `:1216` | `import-bill --channel <声明渠道>`（取值来自 `:1207`） | 退出码 0 | **保留** |
| 10 | `:1238` | `reconcile --channel <声明渠道>`（账单被篡改） | `code == 1` 且错误含「拒绝产出」 | **保留** |
| 11 | `:1274` | `calibrate --channel <声明渠道> --tier ghost`（取值来自 `:1270`） | `code == 2 and "ghost" in out["error"]` | **更新（最小）**：实现点改 `cfg.tier_of(channel_id, args.tier)`；文案**必须保留 `ghost`**、退出码仍 2（用例体不改） |
| 12 | `:1290` | `tiers --channel "ghost"` | `code == 2 and "不一致" in out["error"]` | **更新（最小）**：实现点改按声明集判定；文案**必须保留「不一致」子串**、退出码仍 2，语义逐字保留（用例体不改） |

**行 6 / 8 / 11 / 12 的实现点**（均属"只改实现点、断言与文案子串一字不改"）：`ops/billing.py:102-110`
的 `_channel_of` 改按声明集判定；`ops/billing.py:172` 的缺档判定改 `cfg.tier_of(channel_id, args.tier)`；
`ops/billing.py:119` 的档位行渲染改 `cfg.tiers_of(channel_id)`；`core/billing/budget.py:1016` 的
`_parse_channels` 按新形状逐键校验（缺 `tiers` / `adapter` 域外均 `BudgetConfigError`）。

**`.github/workflows/billing_alerts.yml` 调用点**（穷举，共 1 处）：

| 位置 | 调用 | 结论 |
| --- | --- | --- |
| `.github/workflows/billing_alerts.yml:27` | `uv run python ops/billing.py alert-check --channel llm --config configs/movie.yaml` | **保留必须继续通过**：`llm` 渠道在两形态的 `budget.channels` 中均声明；退出码语义（0 无告警 / 1 有告警 / 2 用法或配置错误）不变；该工作流文件**不改**（只读产物、零真实调用、零凭证） |

**同族调用点（单元套件，同一结论规则；逐条进入决策 10 的清单）**：
`tests/unit/test_billing_cli.py`（`:125` 缺 `--channel`、`:129`、`:144`、`:162` "no-such-channel"、
`:172`、`:192`、`:225`、`:247`、`:255`、`:282`、`:315`、`:336`、`:383`、`:422`、`:455`）、
`tests/unit/test_billing_alerts.py`（`:185`、`:203`、`:218`、`:233`、`:249`、`:269`、`:287`、`:293`、
`:306`、`:320` "ghost"）、`tests/unit/test_billing_reconcile.py`（`:385`、`:404`、`:432`、`:457`、`:475`、
`:491`、`:503`、`:520`）、`tests/unit/test_billing_calibration.py`（`:306`、`:317` "ghost"、`:330`、`:360`、
`:386`）、`tests/unit/test_billing_bill_import.py:341`。

**依据/理由**：① 规格 FR-007 把"补命名空间 + 放开单渠道硬拒绝"定为本特性的**结构性前置**：媒体渠道
与 LLM 渠道的**环节语义不同**（前者的环节是投放/回流，后者的环节是生成与 judge），共用一套扁平 `tiers`
必然出现"同一键名两种含义"；② 019 自己的注释已经预告了正确方向——`sole_channel()` 说"多渠道需先补齐
**按渠道分派的装配口径**"，本决策正是补齐它，而不是绕过门禁；③ 用既有的 `adapter` 字段做装配引用解析，
**不新增配置键**、不让渠道 id 出现在代码里（原则五 + `test_billing_core_purity.py:151`）；
④ "旧扁平形状仍可读 + 歧义即报错"是 019"不做静默误判"纪律的延续（同一纪律在分类上体现为
"无分类即不可解释"，在渠道上体现为"歧义即报错"）；⑤ 逐条穷举调用点的原因：**这是对 019 的兼容性扩展，
必须证明扩展后每条既有断言要么保留、要么按扩展更新且不削弱**——只列"我们改了 budget 段"不足以证明
"019 的门禁没被削弱"。

**被否决**：
- *为投放渠道另建一套门禁（旁路）*：规格明文禁止；且会造成"两个真实渠道两套账本口径"，账单对账的
  一致性无法保证。
- *保持单渠道、用两套配置互斥（同一时刻只声明一个渠道）*：`FR-007`/`SC-004` 要求"多渠道并存时额度与
  账本按渠道分派正确率 100%"，互斥方案等于把"多渠道"降级为"分时单渠道"。
- *扁平 `tiers` 静默归入"第一个渠道"*：即"静默误判为另一个渠道"，规格明文禁止。
- *扁平 `tiers` 无条件报错（即使只有一个渠道）*：会让全部既有夹具与历史配置立刻失效，且与"旧扁平形状
  仍可读"直接冲突。
- *把渠道 id 写进 `core/billing/` 的常量表*：违反零字面量门禁（`tests/unit/test_billing_core_purity.py:151`）。
- *改 `.github/workflows/billing_alerts.yml:27` 以适配新语义*：该调用今天即合法（`llm` 已声明），改了反而
  制造"工作流随特性漂移"的伪证据。

**影响面**：`core/billing/budget.py:254-388`（`ChannelSpec` / `BudgetConfig` / `to_snapshot` / `sole_channel`）、
`:433-470`（`assemble_guard`）、`:763-900`（`SpendGuard.check` 的档位解析）、`:1016-1100`（`_parse_channels` /
`_parse_tiers`）；`ops/billing.py:102-119`；`agents/pilot/backends.py:280`；`ops/smoke_llm.py:206`；
两份形态配置的 `budget` 段（`configs/movie.yaml:509`、`configs/shortdrama.yaml:512`）——**投放渠道只在
`configs/shortdrama.yaml` 的 `budget.channels` 登记**，`configs/movie.yaml` **不登记**媒体渠道
（两形态的 schema 齐备与"都能装配通过"见本决策第 2 条的边界，对应裁决 4）；测试面见决策 10；
契约落点 `contracts/channel-budget.md` **C11/C12/C13/C14**（命名空间与旧形状读 / 装配与分派 /
019 断言不削弱清单 / 投放调用受同一门禁）。

## 决策 7：015 的**进程内按轮上限**与 019 的**跨进程文件账本**分工——共同生效、口径可辨

**问题**：promo 侧今天已有自己的投放上限：`agents/promo/config.py:70-72` 的
`budget_cap_usd = exploration_per_round_usd × promo_pilot_ratio`，前置校验在
`agents/promo/loop.py:403-418`（用 `round(x*100)` 处理最小货币单位边界），已花费读自**运营表**
（`_round_spent`，`:240`）；019 的门禁是**跨进程文件账本 + 厂商账单口径**（`core/billing/budget.py` 的
`FileLedger` + `SpendGuard`）。规格明文要求二者**不得混同、不得互相替代**（FR-008、边界情况第 9 条）。

**决策**：
- **015 的按轮上限保留**（不动其语义与位置）：它是**估算门禁**，粒度是"本轮 × 本节环节"，数据源是本轮
  运营表的花费合计，超限即拒投并落一条 `NodeStatus.EVALUATED` 的 0 分节点（`:403-418` + `:452-463`
  的 `_insert_campaign_guarded` + `adapter.pause`）。
- **在它之前再加一道 019 的跨进程账本门禁**：复用 `core/billing/budget.py:824` 的 `SpendGuard.check`
  （渠道无关，只认 `channel_id` + `cfg` + `SpendRequest` 协议 `:133-143`）；`estimated_usd` 由**投放侧**
  按申请额提供（申请额在投放前已确定，不需要第二套估算口径）；通过则占额、结算时 `settle`。
- **拒绝理由必须点名命中的是哪一条**：超 015 上限 ⇒ 理由写明"按轮上限"（沿用既有中文文案风格）；
  超 019 额度 ⇒ `BudgetRefusedError` 的 `reason` 为 `over_limit` 且 detail 带剩余额度（`:881-887` 既有语义）。
  两类错误**互斥且各自可辨**；**调用前拒绝、平台调用 0 次、零入账**三条对两道门禁**共同成立**。
- **两者都必须在投放申请路径上生效**：不得用其中一道替代另一道（`SC-004`）。

**依据/理由**：① 两道门禁的问题面不同：015 回答"本轮投放是否超了本节环节的探索预算"（**进程内、按轮、
估算**），019 回答"这个真实渠道本窗口累计花了多少、是否还有额度"（**跨进程、按窗口、文件账本 + 厂商口径**）；
② 规格与宪章原则三要求的是**后者**（"调用前必须通过按环节分档的预算额度"），但同时 015 的按轮上限是
既有有效护栏，删掉它等于让"单轮失控"这条风险重新裸露；③ "口径可辨"的价值在**归因**：如果两种拒绝
共用一个错误类型与文案，运营侧无法判断该去改配置额度还是该缩单轮申请额。

**被否决**：
- *用 019 账本替代 015 按轮上限*：会丢掉"单轮之内"的约束（账本按 `run`/`day`/`period` 窗口，
  同一天内多轮共享同一窗口 ⇒ 单轮可以吃掉全天额度）。
- *用 015 按轮上限替代 019 账本*：违反原则三（宪章要求的是跨进程额度 + 账单对账，015 的上限读自运营表、
  且不产生账单证据链）。
- *把 019 门禁做成 promo 适配器的内部逻辑（适配器自己校验）*：门禁必须在**调用前**、且是**装配面注入**
  的机制件（019 的既有纪律）；放进适配器等于第二套实现，且会被"换适配器"绕过。
- *两道门禁合并为一个"总上限"*：口径合并后不可归因，且两道的数据源与窗口粒度不同，合并数学上没有意义。

**影响面**：`agents/promo/loop.py:403-425`（前置校验链的两道门禁并列与拒绝分型；按轮上限**原位保留**）、
`agents/promo/loop.py:240`（`_round_spent` 保持只作按轮口径）、`agents/promo/config.py:70-72`（不动）、
`agents/pilot/backends.py:399-406`（投放装配点的 `RecordingChannelCall` 门禁包装，与 `RecordingGateway`
同构）；契约落点 `contracts/channel-budget.md` **C14**（投放调用受同一门禁 + 与进程内按轮上限的分辨）。

## 决策 8：迁移件的**可比性条件配置化**；只迁结论、**不自动迁权重**

**问题**：规格 FR-012 要求"迁移件必须带来源标识、迁移口径、**可比性条件及其判定**"，且
"**可比性条件必须先声明、后判定**，不成立即拒绝迁移并登记原因，不得降格为'仅供参考'"，
同时 `docs/影视全Agents公司技术方案.md:186` 的原文思路是"按迁移学习思路平移到电影线作为**先验**"。

**决策**：
- 新增 `core/calibration/transfer.py`（业务无关、渠道无关），迁移件字段面 = 来源标识（形态 /
  评估器 `id@version` / 周期 / 样本量 / 来源件引用）+ 迁移口径 + **可比性条件与判定**（可迁移 /
  不可迁移 + 原因）+ 采纳状态与留痕；**具体字段名由 `contracts/transfer-ops.md` C15 与
  `data-model.md` 定名**。
- **可比性条件由配置声明**（`configs/movie.yaml` 与 `configs/shortdrama.yaml` **均须声明**、
  **缺项即报错、不取码内默认**）。条件的**类别**（不下发具体数值）至少覆盖：
  ① 来源件的评估器在目标形态**同 id 同 version** 已注册（版本冻结，原则一）；
  ② 来源样本量满足目标形态的样本下限；
  ③ 两形态的判定口径哈希一致（如漂移的 `detector_version`，见 `core/calibration/drift_metrics.py:135-137`）；
  ④ **周期量纲**必须明确（日级 → 周级的结论**不得直接比**；如要迁移必须声明显式换算口径，未声明即不可迁移）；
  ⑤ 来源必须是**真实来源**（`RUN_SOURCES` 口径，`core/billing/runlog.py:28`）——模拟回流结论**不得**迁移。
- **不自动迁权重**：采纳动作**不改**任何既有节点的 `eval_breakdown` 与得分（原则一/二）；权重再拟合
  仍走 010 的提案 → 人工确认路径（`core/calibration/models.py:60` 的 `ProposalStatus` 状态机 + `:226-239`
  的 `confirm`/`shelve`/`fail`）。
- **不可迁移 ⇒ 拒绝迁移**并如实登记原因；**不得**静默丢弃、**不得**降级为"参考"数字。

**依据/理由**：① "先声明、后判定"是**可证伪性**（原则六）的要求：条件如果由代码临时决定，判定就不可
复核；配置化后"为什么这条不可迁移"能被读者独立重算；② 条件类别中"口径哈希一致"与"评估器版本一致"
直接来自**原则一**（评估器版本冻结、口径即版本）；③ "量纲必须明确"是本特性的量纲裁决在迁移面上的投影：
日级窗口的偏差与周级窗口的偏差不是同一个统计量；④ "只迁结论不迁权重"是规格明文（"不做模型/权重自动
迁移"）+ 原则一（`human` 锚点与评估器权重都不许被自动改写）；⑤ "真实来源才可迁移"守住诚实分层：
否则 020 的模拟夹具会把"模拟结论"送进电影线的先验面。

**被否决**：
- *只在迁移件里写自由文本 `reason`*：不可机检"判定完备率 100%"（`SC-007`），也无法聚合。
- *把可比性条件写死在 `transfer.py` 里*：违反 FR-014"全部新增参数形态配置化、缺失即报错"。
- *可比性不成立时降级为"仅供参考"的数值*：规格明文禁止（"不得以'仅供参考'的数字蒙混过关"）。
- *自动把短剧线的信度/偏差写进电影线的评估器 `calibration` 字段*：等于用异形态数值改写本形态证据面，
  `SC-007` 的"误迁移次数恒为 0"与"短剧数字不得出现在电影线证据面"双双失守。
- *复用 010 的台账文件承载迁移件*：台账是"每轮每评估器一行偏差"，迁移件是"一条带条件判定的结论引用"，
  语义不同；混写会让两边的 append-only 机检互相干扰 ⇒ 迁移件独立 append-only 存储（决策 9 同源论证）。

**影响面**：新增 `core/calibration/transfer.py` 与其套件；两份形态配置的 `calibration` 段增可比性条件；
新增**独立脚本** `ops/transfer.py`（子命令 `transfer` / `transfer-confirm` / `transfer-shelve` /
`transfer-report`，与 019 的 `ops/billing.py` 同风格、**退出码语义一致** 0/1/2；薄封装：参数解析 + JSON 输出，
判定与落盘仍在 `core/calibration/transfer.py`）与离线演示步骤；契约落点
`contracts/transfer-ops.md` **C15**（迁移件与可比性判定）与 **C17**（CLI / 离线演示与退出码语义）。

## 决策 9：迁移 `0011_*` = **两件 DDL**（① 锚点表加可空归属日列；② 新表 `promo_daily_metrics`）

**问题**：两件事都需要落库，但今天都没有承载面——① 周期窗口过滤发生在 DB 侧（`core/calibration/selection.py:108`
从 010 的锚点候选里过滤），而 `AnchorScore`（`core/calibration/models.py:70-96`）与锚点表
（`core/calibration/db.py:23-42`）**都没有**归属日字段（字段面是 `anchor_id` / `node_id` / `artifact_hash` /
`agent_id` / `source` / `score` / `reviewer` / `round_id` / `created_at`，**无 JSON 列**）；② 日级分片的唯一性键
**（活动, 周期）**必须落在**有存储层冻结**的记录面上，而候选落点 `promo_campaigns` 是运营状态表、
**刻意不加 immutable 触发器**（`agents/promo/db.py:1-5`）。规格允许加 `0011_*`（裁决 J）。

**决策**：新增 alembic 迁移 `0011_*`（`down_revision = "0010_dev_jobs"`，`ops/migrations/versions/0010_dev_jobs.py`
是当前头），**两件 DDL**：

1. `calibration_anchors` 增一列**可空**归属日（文本日期）；同步 `core/calibration/db.py:23` 的表定义；
2. 新增表 `promo_daily_metrics`（日级分片记录）：唯一键 **（campaign_id, period）** + **INSERT-only 触发器**
   （镜像 `core/calibration/db.py:47` 的 SQLite / `:61` 的 PG 双版触发器范式）。

**为什么唯一性键落在新表、而不是改 `promo_campaigns` 的键**：`promo_campaigns` **刻意无触发器**
（`agents/promo/db.py:1-5`），把"每（活动, 周期）一条、写入即冻结"的纪律挂在它身上，等于要求同一张表
**同时**承担可变运营状态（状态机要写回 `ingested`/`failed`）与不可变证据（要拒绝 `UPDATE`）——两者直接冲突。
新表把"冻结的日级记录"独立出来，且**不改** `promo_campaigns` 的既有唯一键 `(round_id, material_id)`
（`agents/promo/db.py:47`）⇒ 既有运营语义与既有历史行**零改动**，兼容风险最小。

**兼容规则（必须写明）**：
1. **锚点表只加列、不改列、不改唯一键、不动触发器**：`core/calibration/db.py` 的 INSERT-only 触发器
   （`:47` 的 SQLite 版 / `:61` 的 PG 版）与唯一键 `(node_id, reviewer, round_id)`（`:41`）全部不变——
   `ALTER TABLE ADD COLUMN` 是 DDL，不是 `UPDATE`/`DELETE`，不触发该触发器；
2. **历史行保持 NULL、不回填、不回改**：`AnchorScore.created_at` 仍是唯一的历史时间锚（原则一/二）；
3. **读取侧回退**：`load_anchors`（`core/calibration/anchors.py:47-67`）读出 NULL ⇒ 归属日按 `created_at`
   的**日期**回退（内存态可分辨"回退值"与"真实值"，因为真实值非空）；报告登记"**归属日缺失锚点数**"；
4. **新写入不得走回退**：`agents/promo/anchors.py:57-67` 写锚点时归属日必须非空（来源 = 平台显式字段）；
   人评锚点（`core/calibration/anchors.py:74-130` 的 `intake_anchors`）不涉及归属日 ⇒ 保持 NULL，
   且其周期归属仍按 `created_at`（人评锚点的时间就是录入时间，这是既有语义，不属"归属日缺失"）；
5. **`promo_campaigns` 的既有键与历史行零改动**：日级分片记录写 `promo_daily_metrics`，运营表仍按
   `(round_id, material_id)`（`agents/promo/db.py:47`）维护状态机 ⇒ 历史"一次活动一次快照"行仍可读、
   不被误判为另一次采集；
6. **降级安全**：`downgrade()` 对锚点表只 `DROP COLUMN`、对新表只 `DROP TABLE`，不触碰任何既有行数据。

**依据/理由**：① 归属日是**过滤键**、日级分片键是**幂等键**，两者都属"必须可被存储层拒绝改写"的纪律，
而本仓"存储层拒绝"的既有实现就是触发器（`core/calibration/db.py:47`、
`ops/migrations/versions/0001_tree_immutable.py`）⇒ 两者都需要 DDL；② 可空列是最小侵入的加法：既有 INSERT
语句（`core/calibration/anchors.py:30-40` 的显式列清单）不传该列即得 NULL，历史行为逐字节不变；
③ "新写入非空、历史允许空"是唯一能同时满足"归属日必须来自平台"（FR-006）与"历史锚点零回改"（原则一）的方案；
④ 新表而非改旧键的理由见上段；⑤ 不选 JSON 列的理由：锚点表是**冻结证据**，"每列即契约"是它今天的特点
（列类型与 CHECK 约束把取值域写死在 DDL 里），引入 JSON 列会打开一个任意内容的敞口，与"逐条可指认"的
原则六相悖。

**被否决**：
- *把归属日写进 `created_at`（用归属日覆盖平台时间戳）*：破坏 `platform_timestamp` 语义（规格 FR-006
  明文要求它保持"真值产生时刻"），且会让"三者并列可见"无法实现。
- *新增 JSON 列*：见理由⑤。
- *只落文件（报告/快照）不落库*：周期窗口过滤读的是 DB（`core/calibration/selection.py:108`）。
- **改 `promo_campaigns` 的唯一键为含周期（本决策初稿方案，已由裁决作废）**：该表刻意无触发器
  （`agents/promo/db.py:1-5`）⇒ 冻结纪律无处落地；且改既有唯一键会让运营状态机与历史行一并承担兼容风险。
- *新增独立表 `anchor_attribution`*：引入第二张表 + 第二套唯一键，且"锚点与归属日"是一对一属性，
  拆表只会带来 JOIN 与一致性风险（**这与"日级分片记录该不该独立成表"是两种判断**：后者承载的是一对多的
  独立事实（每活动每周期一条），不是某张表的附加属性）。
- *靠 `round_id` 承载归属日*：轨道不同——`round_id` 是校准轮次标识，一个周期可以有多轮（决策 4），
  而归属日是**指标所描述的日期**。

**影响面**：`ops/migrations/versions/0011_*.py`（新，两件 DDL）、`core/calibration/db.py:23`、
`core/calibration/models.py:70-96`、`core/calibration/anchors.py:25-67`、`core/calibration/selection.py:108`、
`agents/promo/anchors.py:22-28/57-67`、`agents/promo/daily.py`（新表的写入面）；
`tests/integration/` 的迁移套件（**由父代理在宿主上跑**，本计划只登记其存在）；契约落点
`contracts/daily-ingest.md` **C6/C8**。

## 决策 10：会变红的既有测试与夹具清单及处理方式

**问题**：本特性同时触碰两组既有契约面——① 010/012 的**周期标签量纲与产物路径**；
② 019 的**预算段形状与 CLI 语义**。规格边界情况第 17 条要求"必须同步更新，不得为过测试而放宽断言"。

**决策：处理原则只有一条——"按扩展更新、不削弱"**。逐条清单如下（"保留"= 不需改动且必须继续通过；
"扩展"= 断言面按新行为增补但既有断言不删）。

| # | 测试 / 夹具 | 位置 | 为什么会红 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | `test_外环日级` | `tests/unit/test_form_switch.py:188-194` | 只断言配置数字（`short.period_days == 1 and < movie.period_days`），不涉运转 ⇒ **本身不会红**，但正是"日级名不副实"的容忍源 | **扩展**：加"cadence 派生标签（日级 ⇒ 日期）"、"漂移窗口单位（日级 = 天）"、"同周期零覆盖"三条**运转**断言；**不得**继续把配置数字断言当作"日级已运转"的证据 |
| 2 | 锚点得分分布快照用例 | `tests/unit/test_anchor_snapshots.py:23`（夹具写死 `_PERIOD = "2026-W39"`） | 周级标签语义不变 ⇒ 值不变；但快照 payload 若增记账字段（锚点数/指纹）或台账行结构变化 | **保留既有断言**（周级路径不变）；快照/台账新增字段 ⇒ **扩展**断言 |
| 3 | 漂移检测用例 | `tests/unit/test_drift_detect.py:32`（`_CURRENT = "2026-W39"`）、`:117-137`（窗口 = 最近 `window` 个周期） | movie 夹具为周级且 `window: 5` ⇒ 量纲等价、取值不变；但 `thresholds_snapshot` 若增窗口单位键 | **保留**既有断言；新增键 ⇒ **扩展**；另立日级（`window: 3` = 3 天）新用例 |
| 4 | 漂移配置"阈值快照形态"用例 | `tests/unit/test_drift_config.py:74-79` | **会红**：它断言 `thresholds_snapshot()` **逐键全等**（`psi`/`quantile`/`min_samples`/`window`） | **扩展**：新增窗口单位与 cadence 键；**不得删键换取通过**；`:93-136` 的"缺项即红"参数化须增 `period_days` 缺项条目 |
| 5 | 漂移口径版本用例 | `tests/unit/test_drift_versioning.py:49-123` | `metric_hash` 纳入周期量纲 ⇒ 哈希后缀变化 | **保留**（用例断言的是"同口径稳定 / 改口径即变 / 处置参数不影响"，全部按 `detector_version(...)` 现算，不写死哈希）；`tests/unit/test_drift_detect.py:283`、`tests/contract/test_drift_contracts.py:184` 等处为 `startswith` 断言 ⇒ 不受影响 |
| 6 | 外环周校准契约聚合 | `tests/contract/test_calibration_contracts.py:1-10`（C1~C9 端到端；含"权重生效后历史节点逐字节一致"与"昂贵动作调用计数为 0"） | 周级夹具 ⇒ 标签取值不变 ⇒ 两条机检**必须继续通过** | **保留且不得改判据**；日级运转断言**另立**新契约用例（不得把周级用例改成日级） |
| 7 | 剧本线校准契约 | `tests/contract/test_screenplay_calibration.py:206`（`iso_week_label(_PERIOD[1])`，配置为 movie） | `period_days == 7` ⇒ 取值不变 | **保留**（`iso_week_label` 经再导出继续可用，见决策 1） |
| 8 | 漂移契约 | `tests/contract/test_drift_contracts.py:42`（`_PERIOD = "2026-W39"`）、`:458` | 周级 ⇒ 不变 | **保留**；`：551` 的"010 产物零写入"机检继续成立（本特性对快照的写入仍在 010 的写入面内且不新增文件形状） |
| 9 | 平台真值锚点用例 | `tests/unit/test_platform_anchors.py`（写死 `EXPECTED_SCORE`、读 `metrics.platform_metrics` 夹具） | 归属日字段新增 ⇒ 夹具快照 dict 缺该字段；若存在性检查误放在读路径上，历史行会被拒 | **必须**：存在性检查只在**新写入路径**强制（数据类末位可选）⇒ 读路径**保留**；夹具补字段 ⇒ **扩展** |
| 10 | promo 回流夹具 | `tests/conftest.py:439-470`（`make_platform_backfill`，`:462` 的 `platform_timestamp`） | 归属日路径**无夹具可测** | **必须扩展**：夹具快照补归属日字段（并补"缺该字段 ⇒ 显式失败"的负例夹具）；`:445` 的 docstring 口径同步 |
| 11 | 越界指标适配器夹具 | `tests/unit/test_ingest_metrics.py:137`（直接构造 `MetricSnapshot`） | 若字段为末位可选 ⇒ 构造零改动 | **保留**（若把存在性检查并入 `validate_metrics` 的默认路径 ⇒ 需显式传，届时按扩展更新） |
| 12 | 两渠道账本隔离用例 | `tests/unit/test_billing_paths.py:114`（`test_两渠道同周期互不覆盖`） | **会红**：它用 `budget_config_factory(channels={...})` 直接构造两渠道，而 `tiers` 仍是扁平形状 ⇒ 命名空间改造后构造面失效 | **扩展构造面**（键移到渠道下）、**保留语义断言**（两渠道账本互不覆盖、同一档位不跨渠道串用）；并**新增**"多渠道 + 扁平 tiers ⇒ 歧义报错"的负例 |
| 13 | budget 夹具工厂 | `tests/conftest.py:4109`（`_billing_budget_payload`）与其 `:4127` 的 `for tier in payload["tiers"].values()` | 遍历路径从扁平 `tiers` 变为 `channels.<id>.tiers` ⇒ 会 `KeyError` 或静默不生效（限额度失效 ⇒ 夹具"小额度假"承诺破裂） | **必须同步改造**（这是"夹具假绿"的高风险点：夹具若静默不压额度，门禁用例会以错误前提通过） |
| 14 | `core/billing` 纯净性机检 | `tests/unit/test_billing_core_purity.py:110`（五模块被扫描）、`:151`（零配置声明的渠道 id）、`:157`（零配置声明的环节 id） | 命名空间改造后，反向扫描的**配置路径**（今天读 `budget.tiers` 键）必须改为 `budget.channels.*.tiers.*`；新增写入面（渠道/档位解析）也在扫描范围内 | **扩展扫描面**（路径 + 新增源文件覆盖）；**不得删断言**（否则"零渠道字面量"失去牙齿） |
| 15 | 019 契约套件的 `--channel` 调用点 | `tests/contract/test_billing_contracts.py` 的 12 处（**逐条结论见决策 6**） | 取值来源（`next(iter(...))`）在单渠道夹具下仍成立；`--channel ghost` 的实现点改按声明集判定 | **逐条**（与 C13 表逐行一致）：**10 处保留**——含 `:1146`（**硬要求、不得改**）与 `:995`/`:1013`/`:1037`/`:1048`/`:1149`/`:1155`/`:1216`/`:1238`（用例体一字不改）；**2 处更新（最小）**——`:1274` 文案须保留 `ghost`、`:1290` 文案须保留「不一致」子串，退出码仍 2；**零"删除"结论** |
| 16 | 019 单元套件的 `--channel` 调用点 | `tests/unit/test_billing_{cli,alerts,reconcile,calibration,bill_import}.py`（行号见决策 6 末段） | 同上 | 同 #15 的规则逐条处理；`tests/unit/test_billing_cli.py:125`（缺 `--channel`）与 `:162`（`no-such-channel`）仍**必须**退出 2 |
| 17 | 定时告警门禁 | `.github/workflows/billing_alerts.yml:27` | 多渠道后 `--channel llm` 的解析路径变化 | **保留必须继续通过**；工作流文件**不改** |
| 18 | web 只读对齐用例 | `tests/contract/test_web_parity.py:39-44`（报告路径 `reports/{period}.json`、漂移报告路径） | 周级路径不变 ⇒ 取值不变；日级新增 `YYYY-MM-DD` 形状 | **保留**（周级）；web 仍**只读**、本特性**不加写入口** |
| 19 | 019 的七子命令 `--help` 循环 | `tests/contract/test_billing_contracts.py:1129-1140`（对七个既有子命令逐条 `--help` 断言退出 0） | 新增第 8 条只读子命令 `channels` | **保留**（循环只覆盖既有七条，新增一条不破坏它）；`ops/billing.py:5` 的 docstring「七条齐备」措辞同步为八条属**文档同步、不是断言削弱**（C12 兼容规则末条） |

**变红清单之外，明确不改的**：`tests/adversarial/`、`tests/unbiasedness/`、`tests/integration/`
（后者的迁移套件会因 `0011_*` 而需要跑，但由父代理在宿主上执行——本计划只登记其存在与"历史行 NULL 不回改"
的断言要求）、`tests/contract/test_pilot_film_contracts.py`、`tests/unit/test_promo_loop.py`
（按轮上限语义不动，`:69`/`:78`/`:92` 的断言继续成立）。

**依据/理由**：① 规格边界情况第 17 条把"会因此变红的既有测试与夹具"列为**必须同步更新的交付物**，
理由是这些断言面正是"量纲"的**唯一守卫**——守卫放宽则"日级名不副实"会立刻复发；② 硬要求"不得为过
测试而放宽断言"指向的是本仓一贯纪律（019 自己的"不削弱"表述同源）；③ 第 13 项被单独标为高风险，
因为**夹具静默失效会造成假绿**——这比测试变红更危险（红是信号，假绿是失信）。

**被否决**：
- *删掉会红的断言（如 `thresholds_snapshot` 的全等断言）*：等于让"口径进产物"失去守卫。
- *让两形态都退化为周级以规避量纲改动*：把"可配置"变成空话（017/019 已两次否决同类做法）。
- *把日级断言塞进既有周级用例（改夹具周期为日级）*：会让"周级逐字节不变"这条兼容性证据消失。
- *等实现完再统一改测试*：违反 TDD 纪律（先写测试 → 确认测试有效 → 再实现），且会让"变红清单"
  变成事后追认。

**影响面**：上表 **19 项**，覆盖 `tests/unit/`（8 个文件 + `conftest.py` 三处夹具）、`tests/contract/`（5 个文件）、
`.github/workflows/billing_alerts.yml:27`；契约落点 `contracts/period-cadence.md` **C5**（口径进产物与变更留痕）、
`contracts/channel-budget.md` **C13**（019 既有断言不削弱清单——本清单的 019 侧就是该契约的逐行举证）。
019 侧的**附加落点（非 `--channel` 面，同一扩展的必改清单）**不在此表重复，逐条见
`contracts/channel-budget.md:188-203`：`agents/pilot/pilot.py:446-448`（`cfg.tiers` 非空校验）、
`ops/billing.py:172`、`ops/demo_billing.py:126/128/839`、`tests/unit/test_billing_config.py:45-46/149-166`、
`tests/unit/test_billing_snapshot_freeze.py:72-73`、`tests/unit/test_billing_ledger_concurrency.py:215`
——结论同样是"零删断言、零放宽"，改动集中在实现点与夹具。

## 决策 11：短剧态运行窗口下限取 **14 天**（"≥2 周"来自立项书 G4 验收原文，不是发明数字）

**问题**：`budget.runs.min_window_days` / `gap_tolerance_days` 是 019 交付的覆盖窗口下限与断档容差
（`core/billing/runlog.py:226-311` 的 `window_coverage`），两形态现值相同（`configs/movie.yaml:597-599`
与 `configs/shortdrama.yaml:600-602` 均为 `min_window_days: 7` / `gap_tolerance_days: 0`）。
020 要用这套口径做"短剧线真实数据回流 ≥2 周"的机检（FR-002），而短剧态现值 **7 天与验收原文不一致**。

**决策**：
- **短剧态** `budget.runs.min_window_days: 14`（改 `configs/shortdrama.yaml:600-602`）；
- **电影态保持 7**（`configs/movie.yaml:597-599` **零改动**）；
- `gap_tolerance_days` **保持现值**（两形态均 0）并**留在开放问题**（预热期与容差由运营与制片侧给定，
  见 spec 开放问题 3）；未给定前按"不容断档"运行，缺口逐段如实报出、**不插值**；
- 该数字**不是本特性发明**：`docs/三期立项书.md:166` 的 G4 行与 `:212` 的周 9~12 里程碑行明写验收
  "短剧线真实数据回流 ≥2 周"。

**依据/理由**：① 覆盖判定口径（`covered_days ≥ min_window_days` **∧** `max_gap_days ≤ gap_tolerance_days`）
是 019 已验证的实现（`core/billing/runlog.py:226`），020 只**填配置值**、**不新造判定**；② 把"≥2 周"写成
14 天是**忠实编码**；若沿用 7 天，机检会在**半周**就通过——即"用 1 周的窗口去证 2 周的验收"，
正是规格反复点名的"名不副实"；③ 电影态不改：C 路径投放属短剧线，电影态的窗口口径对应 019 自己的
里程碑（"≥1 周"），改它属越界；④ `gap_tolerance_days` 不动的原因：放宽容差等于**降低**验收强度，
属运营侧决定，不得由本特性代劳。

**被否决**：
- *两形态都改 14*：改动电影态既有配置与 019 的里程碑口径（019 的 SC-001 是"≥1 周"）。
- *把 14 写进代码或取码内默认*：违反 FR-014（新增参数必须配置声明、缺项即报错），且"2 周"是**形态参数**
  而非常量。
- *顺带放宽 `gap_tolerance_days` 以"让运营好过"*：降低验收强度、掩盖断档，与"断档如实报、不插值"冲突。
- *只在报告里写"≥2 周"而窗口仍取 7*：机检与结论不一致（**假绿**，比红灯更危险）。

**影响面**：`configs/shortdrama.yaml:600-602`（**唯一必改点**）；`core/billing/runlog.py` **零改动**（口径与
实现全复用）；**既有断言逐条核对为"不红"**：`tests/unit/test_billing_runlog.py:61` 的
`min_window_days == 7` 用的是 movie 派生夹具（电影态仍 7 ⇒ 保留）、`tests/conftest.py:3512` 的内联夹具
自声明 `runs: {min_window_days: 7, gap_tolerance_days: 0}`（不派生自 `configs/*.yaml` ⇒ 保留）；
plan 阶段 6 的配置登记清单须登记该取值差异（两形态取值不同、缺键即报错）；契约落点
`contracts/daily-ingest.md` **C9**（报告三时间可见 + 覆盖按归属日、断档逐段如实报）。
