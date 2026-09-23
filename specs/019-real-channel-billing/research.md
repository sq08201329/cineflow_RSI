# 调研：真实渠道与账单对账（019-real-channel-billing）

> 阶段 0 产出。每条决策 = 结论 + 理由 + 被否决的替代方案。规格依据见 [spec.md](spec.md)，
> 架构落点见 [plan.md](plan.md)。本特性唯实例化 **LLM 渠道**（网关之外的付费调用面里唯一凭证
> 已就绪者）；媒体渠道与生成侧渠道随 G4 / G2 接入，机制按渠道无关设计以便复用。

## 决策 1：落点与包边界 = 新包 `core/billing/`（业务无关），不落 `core/llm_gateway/`、不放 `ops/`

**结论**（契约 C1）：新增 `core/billing/`，**5 模块**：`budget.py`（`BudgetConfig`/`BudgetTier` +
`FileLedger` 跨进程账本 + `SpendGuard`/`Reservation` 注入网关的门禁实现）、`bill.py`（厂商账单规范化
导入面 + 导入器注册表 + 批次身份与幂等拒绝）、`reconcile.py`（差异报告与分类）、`calibration.py`
（校准记录与扩量前置）、`runlog.py`（按时间索引的渠道运行记录与窗口机检）。
包内**零厂商字面量、零渠道 id 字面量、零环节与格式 id 字面量、零形态名**：渠道 id、环节 id、格式 id、
档案 id、端点全作参数或配置值传入（参数化，镜像 `core/degraded/` 的 `agent_id` 纪律）。
依赖方向 `core/billing → core/llm_gateway`（**单向**，取 `SpendRequest`/`GatewayError` 类型），
`core/llm_gateway` 对 `core/billing` **零 import**（`spend_guard=None` 时网关行为逐字节不变）；
不 import `agents.*`/`dreaming.*`。

**理由**：这五件事是"渠道计费纪律"（前置额度、账单证据链、逐项分类、校准前置、时间证据）的载体，
**与 LLM 无关**——G4 的媒体投放、G2 的生成侧真实厂商接入时应当复用同一套，而不是各写一份会漂移的实现
（017 抽取 `core/degraded/` 的同一论证）。落 `core/` 也符合宪章"新增目录必须先归入 `core` 或 `agents`"。
零字面量不是洁癖：`tests/unit/test_no_vendor_literals.py:21-24` 对 `core/`（`core/llm_gateway/` 除外）、
`agents/`、`dreaming/` 做 **AST + 文本双层机检**——配置里声明的档案 id 与端点 host 一旦出现在业务代码里
即红；`core/billing` 落在被扫描范围内，天然受这门门禁约束，并由 C1 的
`tests/unit/test_billing_core_purity.py` **四条断言**加固（① 零形态字面量/分支 ② 零厂商词与配置声明的
档案 id/端点 ③ **零配置声明的渠道 id、环节 id、账单格式 id**（反向扫描配置键，白名单=注册表内置通用格式）
④ 零 `from agents.`）。

**被否决**：
- *放 `core/llm_gateway/`*：该目录是厂商字面量的**豁免区**（`test_no_vendor_literals.py:23`），把账单与
  对账放进去会让人误以为这里可以写厂商端点/格式，且网关包的职责是"统一计费、缓存、重试"，
  承载账单对账即职责错位。
- *放 `agents/`*：账单与额度是跨 Agent 的运营机制（角色面 4 个、阶段面 6 个），放任何一个 Agent 都不对。
- *只写成 `ops/` 脚本*：`ops/` 不在覆盖率口径内的机制层（CI 只对 `core/agents/dreaming/web` 测覆盖率），
  且 G4 复用时只能复制，纪律修正要改两遍。

## 决策 2：档案布局与 append-only 范式（C3/C4）：镜像 `core/degraded/evidence.py`，不新造机制

**结论**（契约 C3/C4）：落盘布局（根可配 `budget.ledger.root`，默认仓库根 `billing/`）
`billing/{channel}/bills/{bill_id}.json`、`reports/{period}.json`、`calibrations/{calibration_id}.json`、
`runs/{date}.json`、`ledger.json`、`alerts.jsonl`；三类一次性快照统一采用既有范式：JSON + `system_digest`
内容哈希机检 + 同键重产拒绝 + 人工批注（`overrides`/`annotations`）**只追加**；运行记录另用
`head_digest` **链式摘要**（追加只增，改写/删除任一条即断链报错，当日 `sealed` 后追加拒绝）；
跨进程账本为**可变状态**（只经 C11 的加锁与原子替换、带单调 `revision`），而花费与拒绝的**事实**
另经只增的 `alerts.jsonl` 落痕——"可变余量 + 只增事实"双轨，审计不依赖内存。
`{date}` = `peak_windows.timezone` 的本地日期（与额度时间窗同一日历）；禁止写入其它特性目录与仓库权威配置。

**理由**：宪章 FR-009 明写"镜像 009/017 的 `system_digest` 机检范式"，而该范式已有一套成熟实现：
系统字段清单（`core/degraded/evidence.py:33-44`）、`system_digest()`（`:108`）、"同周期已存在即拒绝"
（`:239`）、人工推翻只追加且校验哈希通过才允许写（`:261`）。第二套实现就是第二套会漂移的纪律。
运行记录额外需要链式摘要：单文件的 `system_digest` 只能证明"当前内容未被改"，无法证明
"没有条目被删掉"——追加型记录必须用链（前一条摘要入本条），否则"删掉断档那天的条目"会静默通过。
账本与告警分轨的理由：账本必须可变（余量随调用变化），而"哪次被拒、哪次超限"是**事实**，
不能落在一个会被覆盖的可变文件里。

**被否决**：*每类证据件各写一套完整性校验*（多份实现、多处漂移）；*只写"创建时间 + 只读文件权限"*
（权限挡不住同用户改写，且无法机检"系统字段是否被改过"）；*运行记录也用单 `system_digest`*
（删条目不可发现）；*把证据写进 PG 表*（见决策 3）；*账本里同时承担事实留痕*（可变文件被覆盖后，
拒绝与超限的历史就没了）。

## 决策 3：跨进程账本 = 单主机文件账本（`ledger.json` + `fcntl.flock` + 原子替换），**不引入 PG 迁移**

**结论**（契约 C11）：额度账本为 `billing/{channel}/ledger.json`：加锁（`fcntl.flock(LOCK_EX)`，排他）→
读 → 判定/更新（含 `reserved_usd` 预留）→ 写临时文件 → `os.replace` 原子替换 → 解锁；每写
`revision += 1`（单调，供"无丢失更新"机检）；锁超时（`budget.ledger.lock_timeout_seconds`）⇒
`BudgetLedgerError` 拒绝调用（不无锁写、不静默放行）。语义边界**明写**：**单主机多进程安全；
多主机共享额度需换 PG 行锁/事务（未做，登记为边界）**，不得以本实现声称跨主机安全。

**理由**：真实渠道调用面就是"运营机 + 本机 pilot 进程"，单主机多进程即覆盖规格边界情况"多实例并行
（额度校验不得只在进程内）"的全部现实形态；而引入 PG 需要新表 + Alembic 迁移 + 触发器/权限纪律
（本仓不可变纪律的既有代价），收益仅落在"多主机"这一**未发生的场景**上——治理规则要求复杂度被论证，
这里论证的结果是"不引入"。此外本特性**全无**数据模型变更（决策 8 同源），账单与报告本就是文件产物，
账本与它们同构更利于审计与人工查看与 CLI 直读（`tiers` 子命令）。

**被否决**：*新 PG 表 + 迁移*（为未发生的多主机场景付迁移与运维代价，且与"账单/报告是文件"的形态割裂）；
*进程内内存账本*（违反规格边界情况，多实例并行时额度被重复占用）；*`sqlite3`*（同样不做多主机，
却引入一个额外的存储引擎与文件锁语义，比 `flock` 更重）；*锁被占用时无锁写*（写坏账本）。

## 决策 4：两维价目的形状与旧快照兼容：可选四格矩阵 + 双形态读取 + 不重写冻结件

**结论**（契约 C5/C6/C8）：档案新增**可选** `price_matrix`：四格 `{peak_miss, peak_hit, off_peak_miss,
off_peak_hit}`，每格 `{prompt_per_1k, completion_per_1k}`；**声明即四格齐备**（缺任一格或任一格缺键 ⇒
`ProfileConfigError`，且**不回落**基础价）。未声明时基础 `prices` **适用于四格**（等价现状），并在口径
备注登记"未区分峰谷/缓存"。单点取价 `price_cell(profile, *, moment, cache_hit) -> (cell_key, prices)`
由网关折算与预算估算**同取**（"估算与折算同源"的延伸，`gateway.py:144-159`）。快照条目新增**可选**键
`price_matrix` 与 `declared_dimensions`；峰谷时区/窗口/归属口径**不重复进档案快照**，而是作为
`peak_windows_snapshot` 随 `config_snapshot["budget_tiers"]` 冻结（C9）；**读取端双向兼容**（按
`price_matrix` 是否在键集中分派，不按版本号猜），**不重写已落盘的冻结快照**。

**理由**：宪章原则一要求"改价不影响历史节点成本复算"，而快照是历史成本的唯一权威
（`core/llm_gateway/profiles.py:98-112` 的 `to_snapshot`，随树冻结进 `config_snapshot["llm_profiles"]`）。
因此新维度只能**加在快照里**、旧快照只能**按原语义解读**：任何"迁移旧快照"的动作都等于改写冻结证据。
四格显式枚举（而非"峰谷表 + 折扣表"）使"缺格"可机检、取价可复算、差异可归因；"折算与估算同取
`price_cell`"则避免门禁估一套价、入账算另一套价。现状的价目是**单一费率表 + 文字免责**
（`configs/movie.yaml:464`："峰时缓存未命中上限（保守高估…）"），二期文档亦承认
"峰谷与缓存命中率**无法在单一费率表里表达**，属**已登记的估算限制**"
（`docs/二期升级路径-真实生成与投放.md:301-302`；同表 `:298-299` 已按"峰时/错峰"两档列出厂商费率，
却**无法**在档案里表达）——本决策正是把该限制变成可表达的维度。

**被否决**：*两个独立费率表（峰谷表 × 命中折扣）*：组合语义要靠约定（叠乘/取低/取高），无法机检也无法
取证；*只保留文字 `price_note`*：即现状，规格已判定其"在数据模型里无法表达"；*新形状 + 迁移旧快照*：
改写冻结证据，直接违反原则一；*必填四格（不做可选）*：会让"尚未区分峰谷"的既有档案（含 `zero_marginal`
的自建档案）装配即失败——把"未区分"从可登记状态变成不可运行状态，代价大于收益；
*把峰谷窗口/时区塞进档案快照*（让档案快照依赖 `budget:` 段，档案与形态配置耦合，C8 明确不这么做）。

## 决策 5：缓存维度归属 = **厂商 prompt 缓存命中**，不是本仓网关缓存

**结论**（契约 C5/C7）：`price_matrix` 的命中维**只由厂商响应报告的命中 prompt token 驱动**，承载字段为
`BackendResult` **末位追加**的可选 `cached_prompt_tokens: int | None = None`（既有后端与测试构造零变化），
由 `backends/http.py` 读厂商 usage 得到（协议面，**不新增档案配置键**），`LLMResult` 如实透传。
网关的本地内容哈希缓存命中**不进任何档位**：它发生在后端调用之前、零成本、零 token
（`core/llm_gateway/gateway.py:284-294`），厂商账单里根本没有这一行。厂商**未报告**（缺失/`None`）时，
**按未命中档计费**并登记"厂商未报告命中 token（按未命中计）"；`cached_prompt_tokens > prompt_tokens`
⇒ **报错**（不静默钳制）。

**理由**：两者是同名异物，混淆会让"记账 vs 账单"凭空分叉：本地缓存命中在账单侧不存在，若计入命中档，
报告会出现无法解释的差额（并可能诱导把差额归因为"厂商口径"）。此外今日后端**没有**承载该字段——
`BackendResult` 只有 `prompt_tokens`/`completion_tokens`（`gateway.py:38-52`），
`HttpBackend.complete` 也只取这两个（`core/llm_gateway/backends/http.py:223-224`，`_usage_of` 在 `:325`），
故需新增**可选**字段（缺省 = 厂商未报告）。这与网关既有的"usage 缺失即报错、不许静默按 0 计费"
（`http.py:61-68`）不冲突：缺失的是**附加维度**，不是主用量。命中数的合理性校验（不得大于 prompt token）
把它挡在"可疑响应"之外，而不是折算出一个好看的数。

**被否决**：*把本地缓存命中当作命中档*（如上，会让记账与账单分叉，且掩盖真实 token 用量）；
*厂商未报告时归入"未知"并跳过计费*（等于静默零成本，违反本仓"不允许静默零成本"的既有纪律，
也违反规格"未校准时命中档位不得默认按 0"）；*把命中判定放到网关缓存层*（那是本地概念，与厂商口径无关）；
*新增档案配置键声明厂商的命中字段名*（把协议字段带进配置面，反而制造"同一厂商多种写法"的配置漂移）。

## 决策 6：门禁注入方式 = 可选注入（`spend_guard=None`），预估口径取既有上界估算，拒绝即类型化错误

**结论**（契约 C9/C10）：`LLMGateway(backend, price_book, *, …, spend_guard=None)`——**可选**注入，
`None` 时网关行为与现状逐字节一致；但**全部** `core/`、`agents/`、`ops/` 内的 `LLMGateway(...)` 构造
必须**显式**声明 `spend_guard=`（含显式 `None`），由 `tests/unit/test_billing_core_purity.py` 机检。
固定调用序列（顺序即语义）：① 角色路由与取价（`prices_for`，C5 格位）→ ② 本地缓存判定（命中即返：
零成本、零后端调用、**不占额**，故不过门禁）→ ③ 门禁 `check`（预估额 vs 剩余额度）→ ④ 后端调用 →
⑤ 入账（`total_cost_usd`/`call_count`/`_breakdown`）→ ⑥ `reservation.settle(actual)`。
拒绝 = `BudgetRefusedError`（`GatewayError` 子类）：`call_count`/`total_cost_usd`/`_breakdown` **均不变**、
缓存不写、异常上抛（调用计数 0、入账 0），原因落 `billing/{channel}/alerts.jsonl`（`kind=budget_refused`）
+ 账本 `refusals` 计数。**预估口径 = 既有成本上界估算的唯一实现**（`prompt = max(1, len(prompt)//2)` token +
`max_tokens` 满额；先例 `agents/screenplay/loop.py:281-289`）：实现收敛到 `core/llm_gateway`，
`agents/screenplay/loop.py:672` 的调用改为薄调用，**guard 不自行估算**（避免两套口径），
由网关填入 `SpendRequest.estimated_usd`。注入点唯一 = `agents/pilot/backends.py:210-214`。

**理由**：① 前置判定是宪章原文（"调用前**必须**通过……超限即拒绝，**禁止**先花后报"），而今天 LLM 腿
连上限都没有（`agents/pilot/pilot.py:182-191` 只验价目表存在、额度记 `0.0`；网关只累计、
`gateway.py:100-104` 无拒绝路径）；② 平台侧已有同一语义的先例——生成申请前按分口径校验并拒绝
（`agents/visual/loop.py:369-371`，用 `round(x*100)` 处理最小货币单位），故本特性兑现的是**同一纪律在
LLM 腿上的落地**，而非新发明；③ 预估必须保守：调用前拿不到真实 token，宁可高估拒绝，不可低估放行
（SC-004"超预算的真实调用次数恒为 0"），而"上界估算仍可能被超"的残余风险按 C10 如实登记
（余量可为负 ⇒ `over_limit=true` 告警 + 后续拒绝，不回滚、不改写）；④ 不重造估算函数：既有上界估算
（`agents/screenplay/loop.py:281-289`，`agents/dev/loop.py:167` 有同一份副本）收敛到 `core/llm_gateway`
后成为**唯一口径**，`SpendRequest.estimated_usd` 由网关填写，guard 只做判定。
排除分型（规格边界"认证失败与配额限制须与预算超限区分"）：`budget_refused`（本系统门禁）≠ 认证失败
（401/403 → `PermanentBackendError`，`backends/http.py:31`）≠ 限流（429 → `TransientBackendError`，
`backends/http.py:29`），三类各有独立错误类型与告警 `kind`。

**被否决**：*网关内建强制门禁*（把所有未接门禁的既有路径变成不可运行）；*调用点各自校验*（8 个调用点
各写一遍，漏写即失守，且拒绝后仍需清理已经发生的调用）；*只在运行结束后对账/告警*（违反"禁止先花后报"）；
*用真实 tokenizer 精确预估*（引入新依赖且仍是估算，收益不抵成本，且 tokenizer 版本本身又是一处口径漂移）；
*在 `core/billing` 侧另写一套估算*（同一语义两份口径，必然漂移）。

## 决策 7：并发额度语义 = C11 已落定的"预留—结算"（check-and-hold）+ 单调 `revision`

**结论**（契约 C11）：额度账本 `billing/{channel}/ledger.json`（JSON + `flock(LOCK_EX)` + `os.replace`
原子替换，每写 `revision += 1` 单调递增供"无丢失更新"机检）在一次加锁内完成"读余量 → 与预估额比较 →
够则 `reserved += estimated_usd`（同事务占用）→ 解锁"；`settle(actual)` 时
`reserved -= estimated; spent += actual`。余量 = `limit − spent − reserved`。锁超时
（`budget.ledger.lock_timeout_seconds`）⇒ `BudgetLedgerError` 拒绝调用（**不无锁写、不静默放行**）；
崩溃残留的未结算预留**如实呈现**（`tiers` 列出未结算预留），不静默清零。

**理由**：规格边界情况要求"同一渠道的并发调用共享同一额度（额度校验不得只在进程内）"，而 SC-004 要求
超预算调用恒 0。若只做"锁内只读校验"，两个并发调用可各自通过（各自看到的余量都不含对方在途），
越限量级 = 并发数 × 单次预估——SC-004 在物理上不成立。预留把"已过检但未结算"的额度显式占用，
使并发下的上限判定仍然成立；替换前先写临时文件再 `os.replace`，读方永远看到完整账本；
崩溃遗留不静默清除，是原则六"不静默、不编造"的直接推论（处置由运营决定，登记为运维边界）。

**被否决**：*锁内只读校验*（如上，SC-004 不成立）；*按最坏情况预扣整额*（把额度按最坏情况锁死，
正常调用互相阻塞，运营上不可接受）；*要求单进程串行化*（把并发问题推给运维，且真实运营机必然多进程）；
*自动清理超时预留*（会在事后把已发生花费的额度"还回去"，等于账本撒谎）；*锁超时后无锁写*（写坏账本）。

## 决策 8：金额精度 = 沿用 float + 容差三量分列；**不做 Decimal 重构**

**结论**（契约 C13）：金额继续用 `float`（既有 `cost_usd` / `REAL` 口径），呈现保留固定小数位；对账容差
**分三个量**：**同源**对账沿用 `core/orchestration/ledger.py:19` 的 `DEFAULT_TOLERANCE_USD = 1e-9`
（只吸收浮点误差，既有语义不动）；**厂商账单**对账的零差异容差 = `budget.reconcile.amount_tolerance_usd`、
告警阈值 = `budget.reconcile.alert_threshold_usd`（两者均为运营口径、必须由配置声明，缺项即报错）；
`|delta| ≤ amount_tolerance_usd` 只表示金额可忽略，**仍须分类与备注**（不等于免分类）。

**理由**：全链路已是 float（网关折算、`CostRecord`、样片包 `cost.json` 的 `1e-9` 校验
`agents/pilot/package.py:122-128`），平台侧还有"按分四舍五入"的既有先例（`agents/visual/loop.py:369-371`）。
改造为 Decimal 会把全部既有落盘金额、比较与序列化口径一并卷入，属跨特性重构而非本特性所需。
但厂商账单的差额是**真实业务事实**（按千 token / 分钟四舍五入，差额到分是常态），若复用 `1e-9` 会把
每一张账单都判成"账目不一致"，故容差必须二分、且账单侧容差由运营声明。

**被否决**：*引入 Decimal 全链路重构*（与 016 契约、`CostRecord`、样片包校验、web 查询全冲突，
收益仅在小数位呈现，YAGNI）；*账单对账也复用 `1e-9`*（假阳性爆炸，报告失去意义）；*四舍五入到分后比较*：
把"小于一分的差异"静默吞掉，与"每条差异必须可解释"冲突。

## 决策 9：配置落点 = 顶层 `budget:` 段，登记清单共**五处**（④⑤ 为勘查新增）

**结论**（与 [data-model.md](data-model.md) 首节逐条一致）：新增顶层 `budget:` 段
（`channels`（渠道登记 + 账单格式 id/来源形态）/ `tiers`（按环节分档额度，含 `window` 与单元素
`on_exhausted: refuse`）/ `peak_windows`（时区 + 归属 + 峰时区间）/ `calibration` / `reconcile` /
`ledger`（根目录 + 锁超时）/ `runs`（`min_window_days`）），**两形态均须声明、缺项即装配报错**。
登记清单**五处**：① `tests/unit/test_form_switch.py:259-276` 的顶层差异集；②
`tests/unit/test_config_integrity.py:23-37` 的 `CONFIG_CLASSES`（增
`("budget", "core.billing.budget", "BudgetConfig")`）与 `:43-67` 的 `REQUIRED_PATHS`（增
`("budget", ("budget", "tiers"))`）；③ `tests/contract/test_pilot_contracts.py:412` 的 C13 差异集；
**④ `agents/pilot/pilot.py:101` 的 `config_completeness` 预检清单**（`:167` 调用）；
**⑤ `tests/conftest.py:3154` 的精简 movie 夹具**。①②③ 是既定钉点，④⑤ 是本次勘查新发现的清单缺口。

**理由**：原则五要求形态差异由配置承载、且"形态配置的例外情况必须是新增配置项"。①②③ 是 017 已经
踩过的坑（`test_form_switch` 只管形态差异键集；`test_config_integrity` 只管加载器完整性与"缺项即红"；
C13 是 `tests/contract/` 侧的同类断言）——新配置段不登记即**逃逸门禁**：没有任何测试会发现它在某一形态
缺失、或解析器静默取默认。④ 是本次勘查新发现的：本特性选择"**缺额度不得启动**"（FR-001），因此预检
必须把 `budget` 段纳入完整性校验，否则"忘记声明额度"会在模拟路径悄悄跑通、切真实后端时才炸；
⑤ 随之判定（精简夹具若参与 pilot 装配则必须补段）。

**被否决**：*只写新的 `test_billing_config.py`*（新测试不会覆盖既有门禁清单，等于让 `budget` 成为唯一
不受完整性门禁约束的配置段）；*让两形态取相同值以规避差异键集*（等于把"可配置"变成空话，017 已明确
否决同一做法）；*把额度塞进既有 `pilot` 段*（语义错位：`pilot` 段是后端装配开关，额度是运营护栏）；
*把 `budget` 并入 `evaluator_weights` 的权重差异循环*（它遍历的是评估器权重，本段不是权重——见下）。

**判断项（与 017 的差异）**：`test_form_switch.py:156` 的"权重差异循环"**不适用**于本段（它遍历
`evaluator_weights` 的七个 Agent），故未登记进该循环；等价约束改由 019 自己的测试承担（断言两形态的
额度/峰谷窗口由配置承载、取值差异显式声明），差异集仍须登记（①③）。

## 决策 10：告警门禁的落点 = append-only 告警件 + `ops/billing.py alert-check` + 定时工作流；**无账单不得产"零差异"报告**

**结论**（契约 C14）：立项书 §4 新增门禁"真实渠道调用的**预算与账单差异告警**"落三处：
① **运维产物** `billing/{channel}/alerts.jsonl`（只追加；`kind ∈ {budget_refused, over_limit,
unexplained_delta, delta_over_threshold, tier_raised, uncalibrated_raise}`，镜像
`core/deployment/auto_deploy.py:44` 的既有留痕先例）；② **机检两条腿**——`ops/billing.py alert-check`
（只读既有产物：报告未解释项 + `alerts.jsonl` 增量）与 `reconcile` 的退出码语义（0 无告警 / 1 有告警 /
2 用法或配置错误），加 `tests/contract/test_billing_contracts.py` 的常驻契约用例（报告含未解释项却返回 0、
省略未解释项、缺 `alerts` 落盘 ⇒ 红）；③ 定时执行形态对齐 `.github/workflows/cost_regression.yml:8-10`
（cron 错峰 + `workflow_dispatch`），**工作流文件本体由本计划定义**（`billing_alerts.yml`）。

**无账单不得产"零差异"报告**：无账单批次时 `reconcile` **拒绝产出**（`ReconciliationError`，退出码 1/2），
不得产"零差异"报告——这是"未激活 ≠ 通过"在契约里的兑现方式（比在报告里声明"未激活"更强：把"没有数据"
直接变成不可产出）。

**理由**：门禁要么可执行、要么不存在。既有 `cost_regression` 在仓库无数据时 `ok=true` 放行（该工作流自身
注释即说明），而新门禁的语义是"真实渠道花费与账单是否对得上"——没有账单时声称"通过"就是假绿，
假绿比红灯更危险（SC-002/SC-009 要求差异可解释，隐含要求"有数据才谈得上通过"）；故此处选择**拒绝产出**
而非"标注未激活后放行"。机检**零真实调用、只读既有产物**，可每日跑。CI 侧只跑判定逻辑（契约/单元），
**不跑真实调用**：宪章测试纪律明写"测试禁止依赖真实昂贵调用"，运行记录的产生属运营动作
（具备凭证的机器上按日最小规模调用），机检用夹具日期。

**被否决**：*只在报告里标红、不做非零退出*（无人消费，等同不存在）；*无数据即报绿*（假绿）；
*无数据时输出 `not_activated` 后放行*（首稿方案，弱于"拒绝产出"：门禁在无账单期形同虚设）；
*在 CI 里跑真实调用以产生数据*（违反测试纪律，且 CI 无凭证、会真扣费）；*把门禁塞进既有
`cost_regression.yml`*（两个门禁的判据与告警处置完全不同，合并后无法定位）。

## 决策 11：对账差异分类的完备性来源 = 规格六类固定枚举 + "每条差异必须带分类"机检

**结论**（契约 C13）：分类词表取规格 FR-007 的六类——**计费口径 / 未入账 / 时序错位 / 免费额度与折扣 / 币种汇率 /
未结账**——**固定、不增不减**；每条差异必须具备 `classification` + `delta_usd`（实测偏差）+ `note`
（口径备注），缺失或取值域外 ⇒ 记作 `unclassified` ⇒ `unexplained=true` ⇒ **告警**；配对口径为
`(档案 id, 周期)`，金额以账单币种为准（异币种先按 C2 的 `fx` 折算）；**零差异仍须分类与备注**
（`|delta| ≤ reconcile.amount_tolerance_usd` 只表示金额可忽略，不等于免分类）；报告必须含 `bill_refs[]`，
**缺 `bill_refs` 的报告一律拒绝产出**（网关记账不得自证）；时序错位与未结账**不得**据此判定"网关记账
有误"（留待下期对账，报告文本与字段须写明）。

**理由**：规格把"完备性"定义在**机检条件**上而非"分类表是否穷尽"上（FR-007"每条差异必须带分类，
无分类即视为不可解释"、SC-002"差异可解释率 100%"、SC-009"不允许'差额不明'的条目"）。因此机制上要保证的
是"**没有差异可以不带分类地留在报告里**"，而不是"分类枚举覆盖一切真实原因"——后者无法先验证明，前者
可以机检。六个类目来自规格与立项书（含边界情况"账单迟到按未结账分类、不得据此判定网关记账有误"），
且枚举固定使报告可聚合、可跨周期比较。

**被否决**：*自由文本 `reason`*（不可机检"每条都有分类"，也不可聚合）；*按金额自动归类*（会把口径差与
真差错混为一谈，恰好掩盖"不知道为什么差"）；*分类写在报告说明里而非逐条*（SC-009 要求逐条指认）；
*允许"待查"作为第七类*（等于给未解释项发合格证，与"未解释即告警"直接冲突）。

## 决策 12：连续运行证据件 = 按日 append-only + 覆盖机检（报缺口、不插值）

**结论**（契约 C15，镜像 C3 的链式摘要）：`billing/{channel}/runs/{date}.json`（按自然日分片，日期口径 =
配置声明的 `peak_windows.timezone`，与额度时间窗同一日历）：每次真实调用一条 entry（`at` / `stage` /
`source ∈ {real, simulated, fallback}` / `adapter_ref` / `profile_id` / `result` / `cost_source` /
`fallback_reason`），`entries` 追加只增、`head_digest` 链式摘要（改写或删除任一条即断链报错），
当日 `sealed` 后追加拒绝。窗口机检 `window_coverage(channel_id, *, end, min_days)` 输出
`{covered_days, gaps[], continuous, meets}`，其中 **`covered_days` 只计 `source=real` 的日期**
（回落日不算真实运行日），`meets = covered_days ≥ min_days`，`continuous = not gaps` **单独呈现**；
**缺口如实报出，禁止插值补齐**。**口径登记**：SC-001"连续运行 ≥1 周"的机检按覆盖天数判定，
断档以 `gaps`/`continuous` 如实标注，不静默算作通过。

**理由**：规格 SC-001 要求"LLM 真实渠道连续运行 ≥1 周"**可机检**，而今天**没有任何时间序列证据件**——
`pilot/runs/{run_id}.json` 的 `run_id` 由配置 + 输入指纹派生（`agents/pilot/pilot.py:261-262`），
同一配置重复运行会**覆盖同一文件**，天然不可能表达"连续多少天"。按日分片 + 链式摘要与决策 2 的范式
一致，且"报缺口不插值"是原则三"禁止编造/插值"的同构纪律（该条针对历史得分，此处针对时间覆盖）。
时区进入日期口径，是因为"连续运行 ≥7 天"是运营语义（本机工作日），必须与峰谷归属共用同一份声明，
并把该口径写进报告备注（C6/FR-011 同源要求）。"只计 `source=real`"与"回落必须显式声明"
（`fallback=true` + 原因 + 来源，FR-013）是同一条纪律的两面：既不许静默回落，也不许把回落日算作真运行日。

**被否决**：*复用 `pilot/runs/`*（会被覆盖）；*单一大文件累积*（每次重写整文件，与 append-only 冲突，
且并发写无原子语义）；*落 PG 表*（见决策 3）；*覆盖率插值/按需补记*（伪造证据）；
*把 `source=fallback|simulated` 的日期也算进 `covered_days`*（把"跑过模拟"当"真实运行"，SC-001 失守）。

## 决策 13：**不做**清单（写进计划与规格防回潮）

- **媒体渠道与投放侧协议校准**（立项书 §3.1 G4）：账号、凭证、合规审查、真钱与 G4 同源；
  立项书 §7 把"厂商 API 与协议假设不符"列为**高**风险，该整条**结转 G4**，不在本特性退休；
- **生成侧真实厂商对接**（G2）：不在本特性为生成侧渠道发明前置条件，也不重写已在位的适配器协议
  （`core/platform_http.py`、`agents/*/platform/http_real.py` 原样复用）；
- **Decimal 金额重构**（决策 8）；
- **多主机共享额度**（决策 3）：单主机文件账本的边界，需换 PG 时另立特性；
- **自动比价路由与汇率引擎**：多币种只以"记录汇率来源与时点"表达（规格假设）；
- **`CostRecord` 增列角色/档案**（立项书 §3.1 遗留 6 的方案 B）：走方案 A（运行报告层），
  完整收口留给后续特性；历史节点不可按角色/档案回溯如实登记为局限；
- **web 侧写入与 billing 看板**：前端只读（原则五），本特性不加 web 面；
- **阶段级额度归属**：**不属"不做"**——已由 C9 落定（调用点声明 `chat(..., stage=<环节 id>)` + 静态断言
  取值 ∈ `budget.tiers` 键集；注入 guard 而缺 `stage=` ⇒ 拒绝 `tier_undeclared`）；本特性不做的是
  **厂商按 token 计费时刻或账单周期摊分峰谷的双口径换算**（C6 登记为口径校准内容）。
