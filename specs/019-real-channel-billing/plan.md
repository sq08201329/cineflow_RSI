# 实现计划：真实渠道与账单对账（LLM 渠道 —— 前置预算门禁 + 厂商账单差异报告 + 两维价目）

**分支**: `019-real-channel-billing` | **日期**: 2026-09-23 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/019-real-channel-billing/spec.md` 的功能规格说明（含 2026-09-23 澄清会话八条决议）

## 概要

把"真实渠道"从"能调通"推进到"**花得起、对得上、算得清**"。本特性唯实例化 **LLM 渠道**（网关之外的
付费调用面里唯一凭证已就绪、真实调用已被验证过的通道），补上今天完全缺失的三件机制：

1. **前置预算门禁（LLM 腿）**：该腿**今天没有任何上限**——预检对剧本 Agent 只检查"模型价目表存在"
   并把额度记为 `0.0`（`agents/pilot/pilot.py:182-191`），网关只累计、从不拒绝
   （`core/llm_gateway/gateway.py:100-104`）。本特性让 `LLMGateway` 接收**注入的、可选的** spend
   guard：在**后端调用之前**（固定六步序列的第③步）按（预估额 vs 剩余额度）判定，拒绝即抛
   `BudgetRefusedError`、网关调用计数 0、入账 0、原因落 `alerts.jsonl`；额度按**环节**分档声明
   （调用点声明 `stage=`）、随 `config_snapshot["budget_tiers"]` 冻结（C9/C10）。
2. **厂商账单导入与差异报告**：把宪章原则三"网关记账**仅**为内部口径、**禁止**作为'成本已核实'的
   唯一依据"落到产物面——规范化导入面（来源/批次/币种/周期/口径备注，全成或全败）+ 逐项差异报告
   （**每条差异必须带分类**，无分类即 `unclassified` ⇒ 告警；报告缺 `bill_refs` 即拒绝产出）
   + append-only 机检（一次性快照 `system_digest`、运行记录 `head_digest` 链，镜像
   `core/degraded/evidence.py:108` 的范式；C2/C3/C13/C14）。
3. **两维价目与连续运行证据**：价目从"恰好两个键 + 一条硬编码线性式"（`core/llm_gateway/profiles.py:51`、
   `:92-96`）升级为**峰谷 × 厂商 prompt 缓存命中**的四格矩阵（可选声明，声明即四格齐备；单点取价
   `price_cell` 由折算与估算同取；**第二条折算路径 `ModelProfile.cost_usd`（`:92-96` 的硬编码线性式）
   改为委派 `price_cell`**，全仓只留一份折算口径），快照双形态兼容、**历史节点成本复算逐字节不变**（原则一）；
   并以**按时间索引的渠道运行记录**（`covered_days` 只计 `source=real`、断档如实报缺口）让
   "连续运行 ≥1 周"成为可机检事实，而非叙述（C5~C8、C15）。

技术主线不是"再写几个脚本"，而是新增**业务无关**包 `core/billing/`（5 模块）承接渠道无关机制，网关注入一个
**可选的** spend guard（契约 C9/C10）。**行为**上未注入时网关逐字不变（既有用例零改动），但契约要求
`core/`、`agents/`、`ops/` 内**每一处** `LLMGateway(...)` 构造都**显式**声明 `spend_guard=`（含显式 `None`
= 显式声明"本处不接门禁"，由 `test_billing_core_purity.py` 机检），故 10 处非测试构造点需各补一个关键字参数
（真实 2 处 + `ops/` 内 mock/桩 8 处；机械改动、行为零变化）；依赖方向 `core/billing → core/llm_gateway`（单向，取 `SpendRequest`/`GatewayError`
类型），`core/llm_gateway` 对 `core/billing` **零 import**（C1）。

**诚实边界（逐条登记，不掩盖）**：媒体渠道与生成侧真实厂商**不在本特性**（分别结转 G4 / G2，
立项书 §3.1 G3 行与 §7「厂商 API 与协议假设不符」高风险整条结转）；多主机账本、Decimal 金额、
自动比价路由与汇率引擎**不做**；账目"角色 × 档案"分解走**运行报告层**（不动 `CostRecord`，
立项书 §3.1 遗留 6 的完整收口不在本特性，见"规格缺口与处理"第 4 条）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: **零新增运行时依赖**——stdlib（`json` / `csv` / `fcntl` / `os.replace` / `datetime`）
+ 既有 `core/{llm_gateway,orchestration,degraded}` 与 `ops/` CLI 惯用法；账单导入只做**泛化行式解析**
（列映射由配置声明），不引入厂商 SDK、不引入表格库

**存储**: **无数据库迁移**——账单 / 差异报告 / 校准记录为一次性快照（`system_digest` 机检），
运行记录为按日追加（`head_digest` 链），告警与拒绝留痕为 `alerts.jsonl`（只增），全部落
`billing/{channel}/…`（根可配，C4）；跨进程额度账本为**单主机文件账本** `ledger.json`
（JSON + `fcntl.flock` + `os.replace` + 单调 `revision`，C11）。发现树与既有 PG 表**一律不动**
（原则二字段口径不触碰）

**测试**: pytest（TDD）；`tests/unit/test_billing_*.py`（门禁拒绝零调用零入账、额度缺项即报错、
四格取价、旧快照语义不变、历史复算逐字节不变、重复批次拒绝、格式未识别报错、六类分类逐项、
无分类即告警、校准 append-only、运行记录断档报缺口）；契约钉名的三个套件：
`tests/unit/test_billing_core_purity.py`（C1：① 零形态字面量/分支 ② 零厂商词与配置声明的档案 id/端点
③ 零**配置声明的渠道 id、环节 id 与账单格式 id** ④ 零 `from agents.`，另含 C10"任何 `LLMGateway(...)`
构造必须显式传 `spend_guard=`"的覆盖断言）、`tests/unit/test_billing_paths.py`（C4 路径口径）、
`tests/unit/test_billing_price_matrix.py`（C5/C8 四格取价与"改价不漂移"四件套）；
`tests/contract/test_billing_contracts.py`（C1~C16 聚合，含 C14 的告警门禁契约用例）。
另需扩展既有断言：`tests/unit/test_no_vendor_literals.py` 的调用点断言新增"每个 `.chat(` 声明
`stage=` 且取值 ∈ 两形态 `budget.tiers` 键集"（调用点计数仍为 8，C9/C10）。并同步登记既有门禁清单
**五处**（见阶段 1 与"规格缺口与处理"第 6 条）

**目标平台**: Linux（WSL2 + CI）。`fcntl` 为 POSIX 专有——与"单主机账本"一并登记为边界

**项目类型**: `core/billing/`（新，业务无关）+ 网关门禁注入点（薄改）+ 配置段 / CLI / 演示 / 测试同步

**性能目标**: **不设性能门禁**——单次调用量级为"一次加锁读改写 + 一次文件写"，账单条目为百级；
本特性的优先级是**失败语义**（宁可拒绝、不可先花后报），不是吞吐

**约束**: 金额沿用既有 float 口径（`cost_usd` + 容差，`core/orchestration/ledger.py:19`），
**不做 Decimal 重构**；`core/billing` 不得出现厂商/渠道/环节/账单格式/形态字面量
（`tests/unit/test_no_vendor_literals.py:21-24` 的双层机检 + `test_form_switch.py:290-316` 的零形态分支
断言本就覆盖 `core/`，再以 C1 的 `test_billing_core_purity.py` 四条断言加固）；
**不得新增 `LLMGateway.chat` 调用点**（该测试钉死 8 处，`test_no_vendor_literals.py:103`），
但 8 处须补声明 `stage=<环节 id>`（C9）；`core/` 保持业务无关、依赖单向（原则五）

**规模/范围**: 新包 `core/billing/`（5 模块）+ 网关/档案/后端三处薄改（行为向后兼容）+ `budget:` 顶层
配置段（两形态）+ 8 处调用点补 `stage=` + `ops/billing.py` CLI（七子命令）+ `ops/demo_billing.py`
离线六步演示 + 新门禁工作流；**不含**: 媒体渠道与投放侧协议校准（G4）、生成侧厂商对接（G2）、
适配器协议重写（`core/platform_http.py` 与 `agents/*/platform/http_real.py` 原样复用）、`CostRecord` 增列

## 现状勘查与两处口径澄清（先核实，后设计）

**事实清单（本特性要补的空缺，逐条可核实）**

| # | 事实 | 本特性处理 |
| --- | --- | --- |
| 1 | LLM 腿**无上限**：预检对剧本 Agent 只要求"模型价目表存在"，额度记 `0.0`（`agents/pilot/pilot.py:182-191`）；网关只累计 `total_cost_usd`/`call_count`（`gateway.py:100-104`），无拒绝路径 | 门禁注入网关，调用前判定（FR-002/003） |
| 2 | 平台侧**已有**逐轮前置门禁范式：生成申请前按分口径校验并拒绝（`agents/visual/loop.py:369-371`，`round(x*100)` 最小货币单位边界）；`core/platform_http.py:19-21` 亦预留"估价端点（预算门禁申请前的校验依据）" | LLM 腿沿用**同一语义**（前置、拒绝即零成本、原因落盘），判定挪到网关边界 |
| 3 | 价目形状固定两键（`profiles.py:51`）+ 一条硬编码线性式（`:92-96`，同时就是 `ModelProfile.cost_usd` 这条**第二折算路径**）；快照 `prices` 按该两键固定（`:98-112`）。峰谷与缓存命中**只在文字里登记**（`configs/movie.yaml:464`："峰时缓存未命中上限（保守高估…）"） | 升级为 `price_matrix`（四格，可选声明），快照双形态兼容（FR-010）；`cost_usd` 改为委派 `price_cell`，折算口径收敛为一处（C5） |
| 4 | 网关**本地缓存命中**：命中即返回 `cost_usd=0.0` 且**不调后端**（`gateway.py:284-294`，计数在 `:104`） | 与**厂商 prompt 缓存命中**严格区分（见**口径澄清 A**） |
| 5 | 后端只解析 `prompt_tokens`/`completion_tokens`（`core/llm_gateway/backends/http.py:223-224`），`BackendResult` 无厂商命中 token 字段（`gateway.py:38-52`） | **实现缺口 → C7 已落定**：`BackendResult` 末位追加可选 `cached_prompt_tokens: int \| None = None`；厂商未报告即按未命中档 + 口径备注；`> prompt_tokens` 报错 |
| 6 | 运行记录按 `run_id`（配置 + 输入指纹派生）落 `pilot/runs/{run_id}.json`（`agents/pilot/pilot.py:261-262`）——**重复运行覆盖同一文件**，无时间序列 | 新增按时间索引的 `ChannelRunLog`（FR-012） |
| 7 | append-only + 内容哈希机检有现成范式（`core/degraded/evidence.py:33-44`、`:108`、`:239`、`:261`） | 账单/报告/校准/运行记录**镜像同一范式**（FR-009） |
| 8 | 凭证就绪矩阵已按配置档案生成（`ops/check_credentials.py:159`）、最小规模真实跑批入口已存在（`ops/smoke_llm.py:295` 的 `--round`） | 校准复用既有最小规模口径，不新造跑批面（FR-004） |
| 9 | 真实后端失败**不回落**模拟：装配期即拒（`agents/pilot/backends.py:235-243`），网关不设回落路径 | FR-013 现状已满足；本特性只补**来源标注**（运行记录/报告标注 real \| simulated） |

**口径澄清 A（最易误读）：两维价目里的"缓存命中"= 厂商 prompt 缓存，不是本仓网关缓存。** 网关缓存命中
发生在**后端调用之前**（`gateway.py:284-294`）：零厂商调用、零 token、零成本、**不占额**、
**厂商账单里没有这一行**。把它计入"命中档位"会让记账与账单凭空分叉。故（C5/C7）：`price_matrix` 的
命中维**只由厂商响应报告的命中 prompt token（`BackendResult.cached_prompt_tokens`）驱动**；
网关缓存命中**不进任何档位**（记账 0，单独计数 `cache_hits`，报告里与厂商口径并列但**不混算**）。

**口径澄清 B：`core/orchestration/ledger.py` 的容差与厂商账单容差不是同一个量。** 前者
`DEFAULT_TOLERANCE_USD = 1e-9`（`:19`）用于**同源**对账（同一账本两条路径的浮点误差）；厂商账单侧是
**运营口径**（真实账单按千 token / 分钟四舍五入，差额到分是常态）：零差异容差 =
`budget.reconcile.amount_tolerance_usd`、告警阈值 = `budget.reconcile.alert_threshold_usd`，两者必须由
配置声明（缺项即报错）、**不可复用 1e-9**；且 `|delta| ≤ 容差` **仍须分类与备注**（C13：零差异不等于
免分类）。

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则三 · 新增条款①：真实渠道调用**必须**受预算门禁约束——调用前**必须**通过按环节分档的预算额度（超限即拒绝，**禁止**先花后报） | 网关注入可选 spend guard，在**固定序列第③步**（后端调用之前）判定；额度在 `budget.tiers` 按环节分档声明，缺项即**装配期拒绝启动**（C9）；拒绝 ⇒ `BudgetRefusedError` + **网关侧**计数/入账/分解均不变 + 原因落 `alerts.jsonl`（C10）；**调用点必须走零成本分支**——`BudgetRefusedError` 须**先于** `except GatewayError`/`except Exception` 捕获，FAILED 记录照写但**被拒的那一笔不入账**（该笔全零；同轮**已发生**花费照记）、原因点名拒绝与额度（只保证"网关侧不变"**不足以**保证"拒绝即零入账"：调用点的"失败照计预估成本"分支会把拒绝记成花费；普查与区分断言见 C10 调用点分支规则、T1950/T1951）；额度随 `config_snapshot["budget_tiers"]` 冻结 | ✅ 满足 |
| 原则三 · 新增条款②：调用后**必须**产出与厂商账单的差异报告；网关记账**仅**为内部口径，**禁止**作为"成本已核实"的唯一依据 | `bill.py` 规范化导入（来源/批次/币种/周期/原始行摘要/口径备注，未识别格式报错、重复批次拒绝，C2）+ `reconcile.py` 逐项差异报告（固定六类 + 实测偏差 + 口径备注 + `unexplained`，C13），报告**必须含 `bill_refs[]`**（缺即拒绝产出）；`accounting_note`（`gateway.py:242`）的口径在报告中等价重述并加强 | ✅ 满足 |
| 原则三 · 新增条款③：任一真实渠道**必须**先以最小规模验证协议与计费口径，验证通过后方可扩量 | `calibration.py` 校准记录（配置价目快照含 `price_matrix` / 实测花费与样本量 / 偏差 / 口径备注 / 时间，append-only，C12）是**扩量的前置**：`raise_tier` 六条拒绝条件 + `calibrated_by` 留痕（配置在拒绝时**未被改写**）；最小规模沿用 `ops/smoke_llm.py --round`（`:295`）的量级口径 | ✅ 满足 |
| 原则一：评估器确定性与版本冻结（价目随快照冻结、历史成本复算不漂移） | 价目维度与档位随 `ProfileSnapshot` 冻结进 `config_snapshot["llm_profiles"]`，峰谷时区/归属随 `config_snapshot["budget_tiers"]` 冻结（C6/C8）；取价口径变更 ⇒ 快照指纹变化（新节点新口径）；**旧快照形状解读保持原语义**（无 `price_matrix` ⇒ 四格同价）、复算历史节点逐字节不变（C8 四件套断言） | ✅ 满足 |
| 原则二：节点不可变与全量谱系 | 发现树与 `CostRecord` **一律不动**；账单/报告/校准为一次性快照（`system_digest` 机检）、运行记录为 `head_digest` 链、账本"可变状态 + 只增事实（`alerts.jsonl`）"双轨（C3）；拒绝的调用不入账（未发生花费；**且调用点须走零成本分支**，不得把拒绝记成预估成本，见 C10），失败的调用照常入账（网关既有语义 `gateway.py:326-335` 不变） | ✅ 满足 |
| 原则四：沙箱隔离与前缀不可泄露 | 不涉及策略执行、不触模拟器、不引入新执行路径；账单文件读取只走运营侧本地路径 | ✅ 不适用/不触及 |
| 原则五：单向依赖与形态配置化 | 新包落 `core/billing/`（**业务无关**：零厂商/渠道/环节/格式/形态字面量，C1 四条断言常驻）；依赖方向 `core/billing → core/llm_gateway` **单向**、网关对 billing **零 import**、不 import `agents.*`/`dreaming.*`；`budget:` 段承载全部新参数（两形态均须声明）且**零形态分支**（无 `form ==` 判断） | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 口径可被证伪：六类分类 + 实测偏差 + 口径备注逐条可指认（不允许"差额不明"）；局限如实标注（多主机未做、Decimal 未做、厂商双口径摊分未做、历史节点不可按角色/档案回溯、未标定额度、实测可超预估 ⇒ 余量可为负）；**未校准即扩量**与**超限调用**均有 0 次机检；不做假数据、不伪造运行记录（`covered_days` 只计 `source=real`） | ✅ 满足 |
| 治理：复杂度必须被论证 | 新增 `core/billing/`、"可注入 spend guard"、"四格价目矩阵"、"分类枚举"、"预留—结算 + `revision`"六项抽象逐条写入复杂度跟踪表，对照被否决的更简方案 | ✅ 满足 |
| 测试纪律 | TDD（测试任务先于实现）；**不依赖真实昂贵调用**——全部用例走 Mock 后端 + 夹具账单，真实调用只由运营侧按日执行；覆盖率 ≥85%（口径不降，含 web）；对抗/无偏性门禁不放松 | ✅ 满足 |

**门禁通过。** 三条新增条款（原则三 v2.0.0）分别由 **C9/C10**（分档声明 + 前置判定 + 拒绝零入账）、
**C2/C13/C14**（导入批次留痕 + 逐项分类差异 + 告警门禁）、**C12**（校准记录 append-only + 扩量六条拒绝）
承载；原则一的满足以"旧快照原语义 + 复算逐字节不变"的断言（C5/C6/C7/C8）为前提，未落实即视为未通过。

## 项目结构

### 文档（此功能）

```text
specs/019-real-channel-billing/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── core-billing.md           # 包落点与业务无关性 / 账单规范化与批次幂等 / append-only 摘要机检 / 磁盘布局（C1~C4）
│   ├── llm-pricing.md            # 两维价目模型与兼容 / 峰谷·时区·归属 / 缓存维度来源 / 快照升级与不漂移（C5~C8）
│   ├── budget-gate.md            # 分档 schema 与缺项拒绝 / 前置校验与拒绝分型 / 跨进程账本 / 校准先决（C9~C12）
│   └── reconcile-ops.md          # 分类完备性 / 新告警门禁 / 运行记录与 ≥7 天窗口 / CLI 与退出码（C13~C16）
└── tasks.md                      # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/billing/                     # 新：与业务无关的渠道计费机制（本特性唯实例化 LLM 渠道）
├── __init__.py                   # 包说明（渠道无关；零厂商/渠道/环节/格式/形态字面量）
├── budget.py                     # BudgetTier / BudgetConfig.from_yaml（budget: 段解析与校验，缺项即报错）
│                                 # + FileLedger（flock + 原子替换 + revision + 预留/结算 + 窗口滚动）
│                                 # + SpendGuard.check(request) -> Reservation / Reservation.settle(actual)
├── bill.py                       # normalize_bill + 导入器注册表（register_importer/importer_for）
│                                 # + 批次身份 (channel_id, bill_id) 与幂等拒绝 + UnknownBillFormatError
├── reconcile.py                  # reconcile(...)：网关记账 vs 账单逐项比对 + 固定六类 + unclassified ⇒ 告警
├── calibration.py                # record_calibration / require_calibration / raise_tier（校准 = 扩量先决）
└── runlog.py                     # append_run / load_run + window_coverage（covered_days 仅计 real，报缺口）

core/llm_gateway/gateway.py       # 既有：可选 spend_guard（含 SpendRequest 类型）+ 固定六步序列
core/llm_gateway/profiles.py      # 既有：price_matrix 解析/校验 + price_cell + 快照双形态（不改旧语义）
core/llm_gateway/backends/http.py # 既有：读厂商 usage 的 cached_prompt_tokens（缺字段 = 未命中档）

agents/pilot/pilot.py             # 既有：预检登记 budget 段（config_completeness）与额度（缺项即拒绝启动）
agents/pilot/backends.py          # 既有：装配点给网关注入 spend guard（链内唯一装配点 + 校准入口，共两处）
（各 Agent 调用点）                # 既有：8 处 `.chat(` 声明 `stage=<环节 id>`（C9；计数仍为 8）
core/yaml_edit.py                 # 既有：raise-tier 的定点改写（复用，不改）
ops/billing.py                    # 新 CLI（七子命令）：tiers / calibrate / import-bill / reconcile /
                                  #   alert-check / runs / raise-tier
ops/demo_billing.py               # 离线演示六步（夹具账单 + Mock 网关：零真实调用、零网络）
configs/movie.yaml / shortdrama.yaml  # 新增顶层 budget: 段（channels/tiers/peak_windows/calibration/
                                      #   reconcile/ledger/runs）——两形态取值不同
.github/workflows/billing_alerts.yml  # 新门禁的定时执行（**本体由本计划定义**，C14）：cron 错峰 +
                                      #   workflow_dispatch，跑 `ops/billing.py alert-check --channel <id>`
                                      #   （只读既有产物、零真实调用；非零退出即告警）

tests/unit/test_billing_*.py      # 门禁/账单导入/对账/校准/运行记录
tests/unit/test_billing_refusal_branch.py # C10 调用点零成本分支（拒绝 ≠ 花费；含调用点普查逐处断言）
tests/unit/test_billing_core_purity.py   # C1 四条纯净性断言 + C10"构造须显式传 spend_guard="
tests/unit/test_billing_paths.py         # C4 路径口径
tests/unit/test_billing_price_matrix.py  # C5/C8 四格取价与"改价不漂移"四件套
tests/contract/test_billing_contracts.py # C1~C16 聚合（含 C14 告警门禁契约用例）
tests/unit/test_no_vendor_literals.py    # 既有：调用点断言扩展"声明 stage= 且 ∈ budget.tiers 键集"
tests/unit/test_form_switch.py / test_config_integrity.py  # 既有门禁清单登记（见缺口 6）
tests/contract/test_pilot_contracts.py                     # 段差异集登记（该文件内编号 C13；见缺口 6）
tests/conftest.py                 # 精简 movie 夹具（登记点 ⑤；门禁在装配期强制则须补 budget: 段）
```

**结构决策**: 账单导入与对账**不是**网关的职责，也**不是**某个 Agent 的业务——它是"渠道计费纪律"的
载体（append-only 证据、逐项差异分类、校准前置），媒体渠道（G4）与生成侧渠道（G2）接入时复用同一套。
故落 `core/billing/`（业务无关、渠道 id / 环节 id / 格式 id 全部来自配置，依赖单向 `→ core/llm_gateway`）；
**不放** `core/llm_gateway/`——那是 `test_no_vendor_literals.py:23` 明确豁免厂商字面量的区域，把非厂商件
放进去会让人误以为可以写厂商字面量，也会让网关包承载非网关职责。门禁的装配面 = **链内唯一装配点 + 校准入口，共两处**
（`agents/pilot/backends.py:210-214` 里构造 `LLMGateway(...)` 处，函数入口为 `:184 build_backends`；
另一处为 `ops/smoke_llm.py:193-198`），
与 015 的"A/B 一行切换"同源：切换真实渠道仍是配置动作；产物落 `billing/{channel}/…`（C4，按渠道分目录，
不写其它特性目录与仓库权威配置）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 落点与包边界 = `core/billing/`（**5 模块**，业务无关），不落 `core/llm_gateway/`、不放 `ops/` 脚本堆；
   依赖单向 `core/billing → core/llm_gateway`，网关对 billing 零 import（C1）
2. 档案布局 = `billing/{channel}/{bills,reports,calibrations,runs}/…` + `ledger.json` +
   `alerts.jsonl`（C4）；一次性快照 append-only + `system_digest`、运行记录 `head_digest` 链式摘要、
   账本"可变状态 + 只增事实"双轨（C3）——全部**镜像** `core/degraded/evidence.py`，不新造范式
3. 跨进程账本 = **单主机文件账本**（C11：JSON + `fcntl.flock` + `os.replace` + 单调 `revision`）；
   **不引入 PG 迁移**，多主机共享额度登记为**未做边界**（YAGNI 论证见研究决策 3）
4. 两维价目 = 档案上的可选 `price_matrix` 四格（`{peak_miss, peak_hit, off_peak_miss, off_peak_hit}`，
   每格两键）；**声明即四格齐备**；未声明 ⇒ 四格皆取基础 `prices` 并记口径"未区分峰谷/缓存"；
   单点取价 `price_cell(profile, moment, cache_hit)` 供折算与估算**同源**（C5）；快照读取端双向兼容、
   **不重写**已落盘旧快照（C8）
5. 缓存维度归属 = **厂商 prompt 缓存命中**（不是本仓网关缓存）；承载字段为 `BackendResult` **末位追加**
   的可选 `cached_prompt_tokens`，读自厂商 usage；缺失/`None` ⇒ 未命中档并记"厂商未报告命中 token
   （按未命中计）"；`> prompt_tokens` 报错（C7）
6. 门禁注入方式 = `LLMGateway(..., spend_guard=None)` **可选注入**（行为逐字不变；但每处构造须**显式**
   声明 `spend_guard=`，含显式 `None`）；固定六步序列（取价 → 本地缓存判定 → 门禁 check → 后端调用 →
   入账 → `settle`）；预估口径 = **既有上界估算的唯一实现**（`max(1, len(prompt)//2)` + `max_tokens` 满额，
   先例 `agents/screenplay/loop.py:281-289`）收敛到 `core/llm_gateway`，由网关填 `SpendRequest.estimated_usd`；
   拒绝 = `BudgetRefusedError`（**网关侧**计数/入账/分解均不变，原因落 `alerts.jsonl`；**调用点须先于
   `except GatewayError` 捕获并走零成本分支**——否则拒绝会被既有的"失败照计预估成本"分支记成花费，
   见 C10 调用点分支规则与 T1950/T1951）（C9/C10）
7. 并发额度语义 = **C11 已落定的"预留—结算"**：锁内同事务 `reserved += estimated`，`settle` 时
   `reserved -= estimated; spent += actual`，余量 = `limit − spent − reserved`；锁超时 ⇒ `BudgetLedgerError`
   拒绝（不无锁写）；崩溃残留预留**如实呈现**不静默清零（原先"规格未定义"的缺口已由此闭合）
8. 金额精度 = 沿用 float + 容差**二分**（同源对账 1e-9 既有；账单侧容差 = 配置声明的
   `reconcile.amount_tolerance_usd`，缺项即报错）；**不做 Decimal**
9. 配置落点 = 顶层 `budget:` 段（`channels`/`tiers`/`peak_windows`/`calibration`/`reconcile`/`ledger`/`runs`，
   两形态均须声明），登记清单共**五处**：`test_form_switch.py:259-276` 差异集、`test_config_integrity.py`
   的 `CONFIG_CLASSES`/`REQUIRED_PATHS`、`test_pilot_contracts.py:412` 的段差异集（**该文件内编号 C13**）、
   `agents/pilot/pilot.py:101 config_completeness`（预检清单）、`tests/conftest.py:3154`（精简夹具）。
   **判断调用**：`test_form_switch.py:156` 的"权重差异循环"**不适用**于本段（它遍历 `evaluator_weights`
   的七个 Agent）；等价约束改由 019 自己的测试承担
10. 告警门禁落点 = `alerts.jsonl`（只增，`kind` 六值）+ `ops/billing.py alert-check`/`reconcile` 的非零
    退出 + `tests/contract/test_billing_contracts.py` 契约用例常驻 + 定时工作流；**无账单批次 ⇒ 报告
    拒绝产出**（不产"零差异"报告，门禁不得被"无账单"绕过）；CI 只跑判定逻辑（测试纪律禁止真实昂贵调用）
11. 对账差异分类的完备性来源 = 固定六类（**不增不减**）+ "每条差异必须带分类"机检；缺失/取值域外 ⇒
    `unclassified` ⇒ `unexplained=true` ⇒ 告警；**零差异仍须分类与备注**；报告必须含 `bill_refs[]`
    （网关记账不得自证）（C13）
12. 连续运行证据件 = 按日 append-only + `head_digest` 链（C3/C15）；`covered_days` **只计 `source=real`**
    的日期，`gaps` 如实列出、**禁止插值**，`meets = covered_days ≥ min_window_days` **∧**
    `max_gap_days ≤ gap_tolerance_days`（**覆盖 + 连续双条件**，容差 0 = 不容断档），`continuous` 与
    `gaps`/`max_gap_days` 照旧并列呈现（通过不谎报为"连续"）；记录产生属运营动作
    （具备凭证的机器上按日最小规模调用），CI 只做机检
13. **不做**：媒体渠道与投放侧协议校准（G4）、生成侧厂商对接（G2）、Decimal、多主机账本、自动比价
    路由与汇率引擎、厂商按 token 计费时刻摊分峰谷的双口径换算（C6 登记为口径校准内容）、
    适配器协议重写、`CostRecord` 增列、web 写入口与 billing 看板

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：`budget:` 段 schema（`channels`/`tiers`/`peak_windows`/`calibration`/
  `reconcile`/`ledger`/`runs`）/ `BudgetTier`（含 `reserved_usd`）与跨进程账本条目 / `VendorBill` 与
  `BillEntry` / `ReconciliationReport` 与差异项 / `CalibrationRecord` / `ChannelRunLog` 与窗口覆盖报告 /
  `price_matrix` 与 legacy 形状 / 磁盘布局（**由并行任务产出，已与本计划逐条对齐**）
- [contracts/core-billing.md](contracts/core-billing.md)（**C1~C4**）：C1 包落点与业务无关性（5 模块、
  依赖方向 `core/billing → core/llm_gateway` 单向、`test_billing_core_purity.py` 四条断言）；
  C2 账单规范化 + 导入器注册表 + 批次幂等（`UnknownBillFormatError`、全成或全败、身份
  `(channel_id, bill_id)`、`fx` 缺来源即报错）；C3 全量 append-only + 摘要机检（一次性快照
  `system_digest`、运行记录 `head_digest` 链、账本"可变 / 事实只增"双轨）；C4 磁盘布局
  （`billing/{channel}/{bills,reports,calibrations,runs}/…` + `ledger.json` + `alerts.jsonl`）
- [contracts/llm-pricing.md](contracts/llm-pricing.md)（**C5~C8**）：C5 两维价目模型 + 兼容规则
  （四格齐备、未声明 ⇒ 四格取 `prices`、本地缓存与厂商缓存严格区分、`price_cell` 与估算/折算同源）；
  C6 峰谷 + 时区 + 归属（`attribution` 单元素 `call_start`、`[start,end)`、跨切换不拆分、三处可见）；
  C7 缓存维度来源（`BackendResult.cached_prompt_tokens` 末位追加、缺报即未命中档 + 口径备注、
  `> prompt_tokens` 报错）；C8 快照升级 + legacy 原语义 + 改价不漂移四件套
- [contracts/budget-gate.md](contracts/budget-gate.md)（**C9~C12**）：C9 分档 schema + 配置声明 +
  缺项拒绝启动（`on_exhausted` 单元素 `refuse`、`chat(..., stage=<环节 id>)` 环节归属与静态断言、
  额度随 `config_snapshot["budget_tiers"]` 冻结）；C10 前置校验语义 + 拒绝分型 + 0 调用 0 入账
  （固定六步序列、唯一预估口径 `max(1, len(prompt)//2)`、`BudgetRefusedError`、三类错误互斥、
  `agents/pilot/backends.py:210-214` · `ops/smoke_llm.py:193-198`，**链内唯一装配点 + 校准入口，共两处**）；C11 跨进程文件账本（flock + 原子替换 + `revision`、
  预留/结算、崩溃残留如实呈现、多主机未做登记为边界）；C12 校准记录 = 扩量先决条件
  （六条拒绝条件 + `core/yaml_edit.py` 定点改写 + `calibrated_by` 留痕）
- [contracts/reconcile-ops.md](contracts/reconcile-ops.md)（**C13~C16**）：C13 分类完备性（固定六类 +
  `unclassified` ⇒ 告警、零差异仍须分类、报告必须含 `bill_refs`）；C14 新告警门禁（`alerts.jsonl` +
  `alert-check`/`reconcile` 非零退出 + 契约用例常驻 + 无账单不得产"零差异"报告）；C15 运行记录 +
  ≥7 天窗口（`covered_days` 仅计 `source=real`、`gaps` 不插值、`meets = covered_days ≥ min_window_days`
  **∧** `max_gap_days ≤ gap_tolerance_days`、`continuous` 与 `gaps` 并列呈现）；
  C16 CLI/演示面与退出码（0/1/2；`ops/billing.py` 七子命令、`ops/demo_billing.py` 离线六步）
- [quickstart.md](quickstart.md)：验证命令 + 运营侧流程（最小规模校准 → 扩量 → 按日运行 → 导账单 → 对账
  → 告警）+ 验收口径 + 验证记录回填区

**FR → 契约落点对照**（不新增需求，只做覆盖核对；依据各契约文件首行的对应关系声明）：

| FR | 承载契约 | 说明 |
| --- | --- | --- |
| FR-001 按环节分档额度、配置声明、随快照冻结 | **C9**（+C1 装配面两处） | `budget.tiers` 键 = 环节 id；缺项即拒绝启动 |
| FR-002 额度校验前置、超限拒绝、0 调用 0 入账 | **C10** | 固定六步序列 + `BudgetRefusedError` + 原因落 `alerts.jsonl` + **调用点零成本分支**（C10 绑定条款） |
| FR-003 LLM 腿同样受额度约束 | **C10**（+C9 `stage=`、C1 装配面两处） | 网关是唯一入口；8 处 `.chat(` 声明环节；拒绝与其它网关失败可辨 |
| FR-004 最小规模校准记录（append-only） | **C12**（+C3 证据纪律、C15 花费可回溯） | 最小规模 = `ops/smoke_llm.py --round` 单轮；`calibrate` **只读**既有记录、不发厂商调用；校准记录不凭报告自证 |
| FR-005 未校准不得扩量 | **C12** | 六条拒绝条件 + `calibrated_by` 留痕（配置未被改写） |
| FR-006 账单规范化导入面、重复批次拒绝、格式未识别报错 | **C2**（+C3/C4 落盘与布局） | 全成或全败、零部分导入 |
| FR-007 逐项差异 + 分类 + 实测偏差 + 口径备注 | **C13** | 固定六类；无分类 ⇒ `unclassified` ⇒ 告警 |
| FR-008 告警落盘；网关记账不得自证 | **C13/C14**（+C3 `bill_refs`） | 缺 `bill_refs` 的报告拒绝产出 |
| FR-009 报告/账单/校准/运行记录 append-only 不可改写 | **C3**（+C4 布局） | `system_digest` + `head_digest` 链 |
| FR-010 两维价目表达；快照冻结；历史复算不漂移 | **C5/C8**（+C6 峰谷维度、C7 缓存来源） | legacy 快照原语义、永不重写；缓存维 = 厂商 prompt token，本地缓存不进档位 |
| FR-011 跨峰谷归属明确并写入报告口径 | **C6** | `attribution=call_start`、`[start,end)`、三处可见 |
| FR-012 时间索引运行记录 + ≥7 天可机检 | **C15** | `covered_days`/`gaps`/`max_gap_days`/`continuous`/`meets`（**覆盖 + 连续双条件**） |
| FR-013 禁止静默回落模拟并照常计费 | **C15**（`source ∈ {real, simulated, fallback}` + `fallback_reason`） | 装配期拒绝为既有行为（`backends.py:235-243`）；`fallback` 为**保留值**，本特性无写入点 |
| FR-014 全部新参数配置化、两形态声明、零形态分支 | **C9**（配置面）+ **C4**（布局）+ **C1**（纯净性断言） | 登记五处清单（缺口 6） |
| FR-015 CLI 与离线演示、退出码语义 | **C16** | `ops/billing.py` 七子命令 + `ops/demo_billing.py` 六步 |

## 规格缺口与处理（如实登记）

| # | 缺口/张力 | 处理 |
| --- | --- | --- |
| 1 | **预算档数字未定**（spec 开放问题 1：需运营侧给定，未给定前"以最小规模档运行并如实标注'未标定'，**不得**发明数字"） | 机制全部落地、取值全部走配置；**码内零默认**（缺项即报错）；交付配置携带**最小规模档**取值 + `note` 标注"未标定"（`BudgetConfig` 缺 `note` 只告警不拦截，data-model 同口径），校准记录与差异报告逐条复述该备注；运营侧给定后只改配置。**已决：交付配置携带最小规模档数值**（门禁必须有值才能拒绝，且该值不进入代码）——**留痕**：spec 开放问题 1 仍如实登记"具体数字待运营给定" |
| 2 | **账单获取方式未定**（spec 开放问题 2：导出 vs API 拉取） | 先做**导出导入**（不引入新凭证面，符合"最小规模起步"）；API 拉取按 C2 的 `source ∈ {export, api}` 同构表达，本特性只实现 export 侧的入库形态、不实现联网拉取（`import-bill` 不联网，C16） |
| 3 | **并发在途语义规格未定义 → 已由契约闭合**：临时说明"同一渠道的并发调用共享同一额度（校验不得只在进程内）"，但规格未定义"已过检、尚未结算"的在途额如何计 | **C11 已落定**：`check` 时同事务 `reserved += estimated_usd`、`settle(actual)` 时 `reserved -= estimated; spent += actual`，余量 = `limit − spent − reserved`（`data-model.md` 的 `BudgetTier.reserved_usd` 同口径）；锁超时 ⇒ `BudgetLedgerError` 拒绝调用；崩溃残留预留如实呈现、不清零。本计划不再把它记为开放项，只保留"预留在并发下的越界残余"这一条如实边界（C10：实测可超预估 ⇒ 余量可为负 + `over_limit` 告警） |
| 4 | **立项书 §3.1 遗留 6 的依赖**：差异报告要逐条指认（SC-009），但"角色 × 档案"分解**只存在于网关内存**（`gateway.py:188-247`），落盘的 `CostRecord` 无角色/档案字段（立项书明写"G3 的账单对账会依赖同一套口径，宜一并定"） | 走立项书**方案 A**：不动数据模型，运行时把 `cost_breakdown()`/`cost_report()` 快照进本特性的差异报告与运行记录（`billing/{channel}/…`，append-only），**且必须与运行同批次落盘**——否则对账缺左侧口径（C13 的输入两侧定义即此）。**局限如实登记**：历史节点不可按角色/档案回溯；不动 `CostRecord`（避免与 016 契约 C6 及原则二字段口径冲突），完整收口留给后续特性 |
| 5 | **厂商缓存命中 token 无承载**：`BackendResult`（`gateway.py:38-52`）与 `HttpBackend.complete`（`http.py:220-225`）只认 prompt/completion tokens | **C7 已落定**：`BackendResult` **末位追加**可选 `cached_prompt_tokens: int | None = None`（既有后端与测试构造零变化），由 `backends/http.py` 从厂商 usage 读取（协议面，**不新增档案配置键**）；缺报 ⇒ 未命中档 + 口径备注；`> prompt_tokens` ⇒ 报错。**不动 `chat` 签名、不新增调用点**（`test_no_vendor_literals.py:103` 钉死 8 处） |
| 6 | **配置登记点共五处（原以为三处）**，且"权重差异循环"不适用：① `test_form_switch.py:259-276` 差异集 ② `test_config_integrity.py:23-37` `CONFIG_CLASSES`（增 `("budget", "core.billing.budget", "BudgetConfig")`）与 `:43-67` `REQUIRED_PATHS`（增 `("budget", ("budget","tiers"))`）③ `test_pilot_contracts.py:412` 段差异集（**该文件内编号 C13**）④ `agents/pilot/pilot.py:101 config_completeness`（预检清单，`:167` 调用）⑤ `tests/conftest.py:3154` 精简 movie 夹具 | 本计划选择"**缺额度不得启动**"（FR-001），故 ④⑤ 必须登记（否则"忘记声明额度"会在模拟路径悄悄跑通、切真实后端时才炸）；①②③ 为既定钉点。**判断调用**：`test_form_switch.py:156` 的差异循环遍历 `evaluator_weights` 的七个 Agent、`budget` 段不属其中，故不并入该循环（等价约束由 019 自己的测试承担），但差异集仍须登记（①③）。**已决：缺额度不得启动；登记点④⑤必须同批落地**（T1903/T1904 已按此写）。**留痕**：若日后改为"仅真实后端强制"，⑤ 可免、④ 仍需 |
| 7 | **"环节"粒度的可得性 → 已由契约落定**：网关边界唯一可得的是 `role`（`core/llm_gateway/routing.py` 四值枚举），`judge` 一个角色覆盖四个环节，`role → tier` 无法表达"按环节分档" | **C9 已落定**：`chat(..., stage=<环节 id>)` 由**调用点**声明环节，`budget.tiers` 键 = 环节 id；静态断言"全部 `.chat(` 调用点声明 `stage=` 且取值 ∈ 两形态 `budget.tiers` 键集"（计数仍为 8），注入了 guard 而缺 `stage` ⇒ 拒绝 `tier_undeclared`。**遗留边界**：这是一处 016 调用点口径的扩展（`chat()` 新增关键字参数，8 处调用点各补一个参数），且厂商若按 token 计费时刻/账单周期摊分峰谷（≠ `call_start`）属**口径校准**内容（C6），本特性不做双口径换算 |

## 复杂度跟踪（新增抽象论证）

| 新增抽象 | 为什么需要 | 为什么不选更简方案 |
| --- | --- | --- |
| 新包 `core/billing/`（5 模块） | 账单导入、差异分类、校准前置、运行记录、跨进程额度都是**渠道无关**机制，G4/G2 接入时复用同一套；门禁又必须能被 `core/llm_gateway` 与其他调用面共用 | *塞进 `core/llm_gateway/`*：网关包只管调用与计费折算，且它是厂商字面量豁免区，会让"账单/对账可写厂商字面量"的错误预期固化；*只写成 `ops/` 脚本*：机制无法被单元测试与报告层复用，G4 一到就复制第二份 |
| 可注入 spend guard（可选参数） | 门禁必须**前置**且唯一入口是网关；同时 `LLMGateway` 必须保持独立可用（**行为**零改动；契约 C10 另要求 `core/`/`agents/`/`ops/` 内每处构造**显式**传 `spend_guard=`，故 **10 处**非测试构造点各补一个关键字参数） | *网关内建强制门禁*：把所有未接门禁的既有调用路径（demo/单测/旧脚本）变成不可运行，等于以"不可用"换纪律；*调用方各自前置校验*：8 个调用点各写一遍 = 门禁可被漏写，SC-004 无保证 |
| 四格 `price_matrix`（可选声明、声明即满格） | 规格 FR-010 要求峰谷 × 命中的**可表达**，且"缺维度即报错"；四格显式枚举使"缺格"可机检、取价可复算（`price_cell` 由折算与估算同取） | *两个独立费率表（峰谷表 + 命中折扣表）*：命中与峰时的组合语义要靠约定（折扣叠乘？取低？），无法取证也无法机检；*继续只用 `price_note` 文字登记*：现状即如此，正是规格判定"无法表达"的根因 |
| 差异分类枚举（六类，代码即契约） | 规格 FR-007 明列六类且"无分类即不可解释"；机检需要稳定词表才能判定"每条都有分类" | *自由文本分类*：不可机检、不可聚合、无法判"完备"；*按金额自动归类*：会把口径差与真差错混为一谈，且掩盖"不知道为什么差" |
| 预留—结算（`reserved_usd`）+ 单调 `revision`（C11） | 规格要求并发共享同一额度，且 SC-004 要求超预算调用恒 0；不记预留时"已过检未结算"的额度会被重复占用 | *锁内只读校验*：并发在途可越过上限（区间大小 = 并发数 × 单次预估），SC-004 不成立；*预扣上限整额*：会把额度按最坏情况锁死，正常调用互相阻塞 |
| `ChannelRunLog` 按日文件 + 覆盖机检 | SC-001 要求"连续运行 ≥7 天"可机检；现有 `pilot/runs/{run_id}.json` 由指纹派生、重复运行**覆盖同一文件**（`pilot/pilot.py:261`），天然无时间序列 | *复用既有 runs 目录*：同一文件会被覆盖，时间证据不可得；*落 PG 表*：需迁移与运维面，而单主机文件已足够（见研究决策 3） |

## 宪章复核（阶段 1 后）

原则三新增条款①由 **C9/C10** 承载（分档额度声明与缺项拒绝启动 + 固定六步序列的前置判定 + 拒绝零入账 +
`stage=` 环节归属 + **调用点零成本分支**：`BudgetRefusedError` 先于通用失败分支捕获，FAILED 记录照写但
**被拒的那一笔不入账**（该笔全零；同轮已发生花费照记）、原因点名拒绝——否则拒绝会被既有的"失败照计预估成本"分支记成花费，SC-004 在调用点侧失守；
普查与断言见 C10 / T1950/T1951）；条款②由 **C2/C3/C13/C14** 承载（导入批次留痕 + 快照与链式摘要的不可改写 +
逐项分类差异 + 告警门禁，含"无账单不得产零差异报告"）；条款③由 **C12** 承载（校准记录 append-only +
六条拒绝条件 + 定点改写与 `calibrated_by` 留痕）。原则一由 **C5/C6/C7/C8** 承载（四格矩阵与 `price_cell`、
峰谷时区/归属随 `budget_tiers` 冻结、旧快照原语义、复算逐字节不变四件套）。原则二不受影响：`CostRecord`
与发现树零改动，账单侧证据按 C3 分轨（一次性快照 `system_digest` / 运行记录 `head_digest` 链 /
账本可变状态 + `alerts.jsonl` 只增事实）、按 C4 落独立目录。原则五由 C1 的四条纯净性断言 + C9 的配置面 +
C4 的布局承载（`core/billing` 零厂商/渠道/环节/格式/形态字面量、零形态分支、依赖单向）。
原则六由 C10/C12/C13/C14/C15 承载（口径备注、实测偏差、未解释项告警、局限如实标注：未标定额度、
多主机未做、厂商双口径摊分未做、历史不可按角色/档案回溯、实测可超预估）。治理规则：六项新增抽象均已
在复杂度跟踪表逐条对照更简方案。**门禁通过**；两项前提——(a) C8 的"历史复算逐字节不变 + 快照不重写"
断言必须随实现落地（否则原则一失守），(b) 缺口 1/6 的两处取舍（交付配置的"未标定"取值、五处登记点中
④⑤的强制性）须在实现时留痕确认。
