# 任务列表：真实渠道与账单对账（LLM 渠道 —— 前置预算门禁 + 厂商账单差异报告 + 两维价目）

**输入**: 来自 `specs/019-real-channel-billing/` 的设计文档（spec.md、plan.md、research.md、data-model.md、contracts/（C1~C16）、quickstart.md）

**前置条件**: 宪章 **v2.0.0**（**原则三新增条款为核心**：① 真实渠道调用必须受预算门禁约束——调用前通过按环节分档额度、超限即拒绝、**禁止先花后报**；② 调用后必须产出与厂商账单的差异报告、网关记账**禁止**作为"成本已核实"的唯一依据；③ 必须先以最小规模验证协议与计费口径，验证通过后方可扩量。原则一：价目随快照冻结、改价不漂移；原则五：形态配置化与零形态分支；原则六：口径可被证伪、局限如实标注）；功能 003（网关）、015（B/C 切换与就绪矩阵）、016（档案/价目/快照与真实 LLM 通道）、009/017（append-only 与不可改写范式：`core/degraded/evidence.py` 的 `system_digest` 机检）已交付；规格含 2026-09-23 澄清会话（本特性只实例化 **LLM 渠道**、媒体渠道整条结转 G4；两维价目**改数据模型**；门禁**按环节分档 + 前置**；新增按时间索引的运行记录；账单走**规范化导入面**；窗口判定 = **覆盖 + 连续双条件**；跨峰谷**按调用开始时刻**归属、取值域单元素 `call_start`）

**测试说明**: 宪章要求 TDD——**测试任务与实现任务分列**（同 001/002/004/009/017 惯例），先写测试并确认失败再实现；全部用例走 **Mock 后端 + 夹具账单**，**零真实昂贵调用**（真实最小规模校准与 ≥7 天连续运行属运营动作，见文末边界）；覆盖率 ≥85%（口径不降，含 web）；本特性的关键是**五处配置登记点**、**门禁两层断言常驻**、**C1 四条纯净性断言**、**旧快照原语义 + 历史复算逐字节不变**、**分类完备性机检**；本地验证命令与 `.github/workflows/ci.yml` 逐字一致。

**组织方式**: 按用户故事分组（US1 预算门禁与最小规模校准 → US2 账单导入与差异报告 → US3 两维价目与连续运行证据）；阶段 1 的两形态 `budget:` 段与五处登记点、阶段 2 的 `core/billing/` 五模块、阶段 3 的 `core/llm_gateway` 侧升级是三条故事线的共同前置。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（两形态 `budget:` 段 + 五处登记点 + 夹具）

**⚠️ 关键**: 五处登记点必须与配置段**同批落地**——① `tests/unit/test_form_switch.py:259-276` 顶层差异键集、② `tests/unit/test_config_integrity.py:23-37` 的 `CONFIG_CLASSES` 与 `:43-67` 的 `REQUIRED_PATHS`、③ `tests/contract/test_pilot_contracts.py:412`（段差异集；**该文件内编号 C13**）为**既定钉点**；④ `agents/pilot/pilot.py:101`（`config_completeness`，`:167` 调用）与 ⑤ `tests/conftest.py:3154`（精简 movie 夹具）是本次勘查发现的**清单缺口**。漏任一处的后果具体：`budget` 段在某一形态缺失或解析器静默取默认时**无任何门禁会发现**，"忘记声明额度"会在模拟路径悄悄跑通、切真实后端时才炸（FR-001；plan.md 缺口 6）。

- [X] T1901 `configs/movie.yaml` 与 `configs/shortdrama.yaml` 新增顶层 `budget:` 段，全键**两形态均须声明**：`channels.<渠道 id>.{adapter, bill.{format, fetch: export|api, columns{entry_id, amount, currency, period, line_kind, amount_sign, 异币种条件必填 fx_rate/fx_source/fx_at}}}`、`tiers.<环节 id>.{limit_usd>0, window.{kind ∈ run|day|period}, on_exhausted: refuse, note}`、`peak_windows.{timezone, attribution: call_start, windows[{start,end}]（支持跨夜；不需峰谷定价的渠道以 `[]` 显式声明全谷时）}`、`calibration.{min_samples, deviation_tolerance, record_ttl_days}`、`reconcile.{amount_tolerance_usd, alert_threshold_usd, unexplained_alert}`、`ledger.{root, lock_timeout_seconds}`、`runs.{min_window_days: 7, gap_tolerance_days: 0}`；两形态取值不同（短剧额度更小、窗口更短）。**额度取值按最小规模档并在 `note` 标注"未标定"**——具体数字由运营给定（spec 开放问题 1：**规格与代码都不得发明数字**，运营给定后只改配置）。（契约 C2/C4/C6/C9/C13/C15；FR-001/006/011/014）

- [X] T1902 [P] 既定钉点三处登记（新顶层段 `budget`）：① `tests/unit/test_form_switch.py:259-276` 顶层差异键集加 `budget`；② `tests/unit/test_config_integrity.py:23-37` 的 `CONFIG_CLASSES` 增 `("budget", "core.billing.budget", "BudgetConfig")`、`:43-67` 的 `REQUIRED_PATHS` 增 `("budget", ("budget", "tiers"))`（缺项即红）；③ `tests/contract/test_pilot_contracts.py:412` 的段差异集（**该文件内编号 C13**）加 `budget`。**判断项**：不并入 `test_form_switch.py:156` 的"权重差异循环"（它遍历 `evaluator_weights` 的七个 Agent），两形态取值差异由 T1923 承担。（FR-014；data-model 登记点①②③）

- [X] T1903 [P] 登记点④：`agents/pilot/pilot.py:101` 的 `config_completeness` 加载器清单增 `budget`（加载器 `BudgetConfig.from_yaml`，属主 `core/billing/budget.py`，见 T1908），缺段/缺档即 `PrecheckError` 拒绝启动；并把 `:182-191` 的 LLM 腿额度占位（`budgets[agent] = 0.0`）改为读取 `budget.tiers` 的**声明值**——`0.0` 占位与 FR-001"额度随配置快照冻结留痕"冲突，属本特性要补的空缺（plan.md 事实 1）。**环节 id 清单 = 各 `.chat(` 调用点声明的 `stage=` 取值**（T1931 落 8 处、T1927 机检），`budget.tiers` 的**键即这些 id**；**判断调用（须实现时留痕）**：agent 名与"环节 id"未必一一对应——若无法对应则如实登记为口径张力（**不发明映射**），占位行与 T1931 的 `stage=` 声明须口径一致；改动须同步既有预检用例。（FR-001；C9）

- [X] T1904 [P] 登记点⑤ + 夹具族，全落 `tests/conftest.py`：① `_MINIMAL_MOVIE_CONFIG`（`:3154` 起）补 `budget:` 段（含最小规模档取值），使 T1903 的预检开启后不红；② billing 夹具：临时 `billing/` 根、夹具账单（含六类差异各若干、口径差、缺失条目、时序错位、折扣与免费额度、异币种缺 `fx`、负数与 bool 金额、未识别格式、可重复导入的同一批次）、差异报告期望值、校准记录（合格 / `passed=false` / 超期 / 样本不足）、运行记录（连续 / 散点 8 天缺 2 天 / 容差放开 / 回落日）、两形态小额度假配置工厂、锁占用与并发进程夹具。**④⑤ 同源**（缺额度不得启动的取舍），须与 T1903 同批落地；不改坏既有夹具。（data-model 登记点⑤）

- [X] T1905 [P] 定点改写路径核查：确认 `core/yaml_edit.py`（`replace_section_entries` / `upsert_section_entries`，注释与其它段逐字节保留、零解析往返）可直接支撑 T1930 的 `raise_tier` 额度改写与 `calibrated_by` 落键；只做核查与必要的最小扩展，**不新增第二个改写器**；如需扩展，同步 `tests/unit/test_yaml_edit.py`。产出核查结论 + 复用口径（镜像 017 采纳改部署指针的先例）。（C12）

**检查点**: ✅ 两形态 `budget:` 段就位；五处登记点全绿（`tests/unit/test_form_switch.py`、`tests/unit/test_config_integrity.py`、`tests/contract/test_pilot_contracts.py`、`agents/pilot/pilot.py`、`tests/conftest.py`）；夹具族可供 US1~US3 复用。**跨阶段前置（如实登记）**：登记点②（`CONFIG_CLASSES` 引用 `core.billing.budget.BudgetConfig`）、③、⑤ 与 T1903 的预检都要求 `core/billing/budget.py` 存在——该模块在阶段 2 的 T1908 才落地，故本检查点的"全绿"**只能在 T1908 之后复核**；阶段 1 的完成口径是"配置段与登记点改动就位"

## 阶段 2：基础（`core/billing/` 五模块 + 纯净性 + 布局）

**⚠️ 关键**: 此阶段完成前不能开始任何用户故事的**装配级**工作（门禁注入、报告落盘、CLI）。本阶段先落两个边界测试（T1906/T1907，先写、确认失败）再落五模块；五模块的行为面（门禁拒绝语义、六类分类、校准先决、窗口机检）由 US1~US3 的测试任务覆盖，故 US 阶段的测试**先于 US 阶段的实现任务**（装配/接线/产物/CLI），与 017 的"通用件抽取 + 故事装配"结构同源。

- [X] T1906 [P] `tests/unit/test_billing_core_purity.py`（先写，镜像 `tests/unit/test_dev_core_degraded_purity.py` 的双层扫描）：C1 四条断言——① 零形态字面量/分支（同 `tests/unit/test_form_switch.py:293-294` 口径）；② 零厂商词与配置声明的档案 id/端点（同 `tests/unit/test_no_vendor_literals.py:39-52` 的反向扫描法）；③ 零**配置声明的渠道 id、环节 id 与账单格式 id**（反向扫描 `budget.channels` 键、`budget.tiers` 键、`budget.*.bill.format`），白名单**仅** `csv_lines`/`json_lines` 且**白名单本身常驻断言**（新增一条即红）；④ 零 `from agents.` / `import agents`（AST + 文本）。（C1）

- [X] T1907 [P] `tests/unit/test_billing_paths.py`（先写）：C4 路径口径——`billing/{channel}/{bills,reports,calibrations,runs}/…` + `ledger.json` + `alerts.jsonl`；`{bill_id}`/`{period}`/`{calibration_id}` 取自声明值、`{date}` = `peak_windows.timezone` 本地日期；根可配（`budget.ledger.root`，默认仓库根 `billing/`）；同键重产被拒；两渠道同周期互不覆盖；**产物不落在** `pilot/`、`deployment/`、`calibration/` 等既有目录。（C4）

- [X] T1908 `core/billing/__init__.py` + `core/billing/budget.py`：`BudgetConfig.from_yaml/from_dict`（`budget:` 段全量校验：`limit_usd` > 0 且非 bool、`window.kind ∈ {run,day,period}`、`on_exhausted` **取值域单元素 `refuse`**、`note` 缺省通过但记 notes 告警、`peak_windows` 三键 + `attribution` 单元素 `call_start`、`reconcile`/`calibration`/`ledger`/`runs` 缺项即 `BudgetConfigError`）+ `BudgetTier`（余量 = `limit − spent − reserved`）+ `FileLedger`（`fcntl.flock(LOCK_EX)` → 读 → 改 → `os.replace` 原子替换、每写 `revision += 1` 单调、窗口滚动 `window_key = (kind, 窗口实例)`、锁超时 ⇒ `BudgetLedgerError` 拒绝、崩溃残留预留**如实呈现不清零**）+ `SpendGuard.check(request) -> Reservation` / `Reservation.settle(actual_usd)`（预留—结算两段）+ `alerts.jsonl` 只增写手（`kind` 六值取值域）+ `peak_windows` 判定 `is_peak(moment, cfg)`（**闭开区间 `[start, end)`**、支持跨夜、一次调用只取一个格位）。**判断调用**：一次性快照的 `system_digest` 机检 helper 落点 plan 未指定，按 C1"不新增第六/第七模块"置于 `core/billing/bill.py` 供 `reconcile.py`/`calibration.py` 复用（见 T1909）。（C1/C3/C9/C11；FR-001/002/011）

- [X] T1909 [P] `core/billing/bill.py`（解析面）：`normalize_bill(raw, *, channel_id, bill_id, period, currency, source, fmt, columns, importer=None) -> VendorBill`；导入器注册表 `register_importer(format_id, parser)` / `importer_for(format_id)`（内置通用键仅 `csv_lines` / `json_lines`）；未注册格式 ⇒ `UnknownBillFormatError`；**列映射必填** `entry_id`/`amount`/`currency`/`period`/`line_kind`/`amount_sign`，异币种条件必填 `fx_rate`/`fx_source`/`fx_at`，任一缺失即导入报错（**禁止**改用金额阈值或符号猜测推断分类）；条目校验（`entry_id` 账单内唯一、`amount` 非负且非 bool、`model_ref` 可空）；**全成或全败**（任一行非法 ⇒ 整体拒绝、零落盘）；批次身份 `(channel_id, bill_id)`；一次性快照 helper（`system_digest = BLAKE3(canonical JSON of 系统字段)`、同键拒重产、`overrides` 只追加）。（C2/C3；FR-006/009）

- [X] T1910 [P] `core/billing/reconcile.py`（判定面）：`reconcile(period, *, channel_id, gateway_ledger, bill, cfg, ...) -> ReconciliationReport`；按 `(档案 id, 周期)` 聚合逐项比对、金额以账单币种为准（异币种先按 `fx` 折算）；**固定六类不增不减**（计费口径 / 未入账 / 时序错位 / 免费额度与折扣 / 币种汇率 / 未结账），分类输入面 = 账单声明的 `line_kind`/`amount_sign`（`line_kind → 六类` 的映射由配置声明）；缺失或取值域外 ⇒ `unclassified` ⇒ `unexplained=true`；`|delta| ≤ reconcile.amount_tolerance_usd` 视为零差异但**仍须分类与备注**；阈值与告警判定（超 `alert_threshold_usd` ⇒ `delta_over_threshold`；`unexplained` 非空 ⇒ `unexplained_delta`；`unexplained_alert` 为真时不得关闭）；时序错位/未结账**不得**据此判定"网关记账有误"（留待下期）。（C13；FR-007/008）

- [X] T1911 [P] `core/billing/calibration.py`（记录面）：`record_calibration(...) -> CalibrationRecord`（渠道 / 环节档 / 当时配置价目快照（含 `price_matrix` 与 `peak_windows_snapshot`）/ 实测花费与样本量 / `deviation = (实测 − 按价目折算)/按价目折算` / `passed`（阈值 `deviation_tolerance` 与 `min_samples`）/ `reasons` / 口径备注 / `at`）、`load_calibration`、`require_calibration`；**append-only**（同 `(channel_id, calibration_id)` 重产拒绝）；渠道校准状态由最新记录派生 `untested|pass|fail|stale`（超期 ⇒ `stale`，按 `untested` 处理）。（C12；FR-004）

- [X] T1912 [P] `core/billing/runlog.py`（记录面）：`append_run(channel_id, *, moment, stage, source ∈ real|simulated|fallback, adapter_ref, profile_id, result, cost_source, fallback_reason)` / `load_run(date)`；按日分片落 `runs/{date}.json`；`entries` 追加只增 + `head_digest` **链式摘要**（改写/删除任一条即断链 ⇒ 读取报错，不静默取）；当日 `sealed` 后追加拒绝；日期归属 = `peak_windows.timezone` 的本地日期。（C3/C15；FR-009/013）

**检查点**: ✅ `pytest tests/unit -k billing` 中 C1 四条纯净性断言与 C4 路径断言全绿；五模块就位且零字面量/零反向依赖；产物只落 `billing/{channel}/`

## 阶段 3：基础（`core/llm_gateway` 侧：两维价目 + 命中维度 + 门禁注入点）

**⚠️ 关键**: 此阶段完成前 US1~US3 的装配级实现无法开始。约束：`core/llm_gateway` 对 `core/billing` **零 import**（`spend_guard=None` 时网关行为逐字节不变）；**不得新增 `.chat(` 调用点**（`tests/unit/test_no_vendor_literals.py:103` 钉死 8 处）；预估口径收敛后**唯一属主**在 `core/llm_gateway`，不得留第二份。

- [X] T1913 [P] `tests/unit/test_billing_price_matrix.py`（先写；C5/C8 四件套）：四格（峰/谷 × 命中/未命中）取价正确且 `cost_usd` 等于格位折算值；声明即四格齐备（缺一格或某格缺 `completion_per_1k` ⇒ `ProfileConfigError`）；未声明 ⇒ 四格皆取 `prices` 且既有档案取价与改造前**逐字节一致**；单格为 0 合法、四格全 0 仍须 `zero_marginal: true`；本地网关缓存命中**不进任何格位**（与厂商 prompt 缓存正交）；四件套 = ① 改配置后历史复算逐字节不变 ② 新旧形状读取等价 ③ 缺格装配报错 ④ 快照含 matrix 且无密钥、指纹随 matrix 变化。（C5/C7/C8；SC-003）

- [X] T1914 [P] `tests/unit/test_billing_peak_windows.py`（先写；C6）：跨夜窗口（`08:30–00:30`）在 `23:00` 判峰、`01:00` 判谷；边界时刻按**闭开** `[start, end)` 裁定；跨峰谷切换时刻的调用按其**开始时刻**取档且**不拆分**；缺 `timezone`/缺 `attribution`/`attribution` 取值非法 ⇒ 装配报错；归属口径**三处可见**（报告与运行记录口径备注、`config_snapshot["budget_tiers"]` 的 `peak_windows_snapshot`、校准记录 `note`）；日历单点（额度 `day` 窗口与运行记录日期同用 `peak_windows.timezone`）。（C6；FR-011）

- [X] T1915 [P] `tests/unit/test_billing_cached_tokens.py`（先写；C7）：后端报命中 token ⇒ 取 `*_hit` 格并标注；未报/`None` ⇒ 取 `*_miss` 格 + 口径备注「厂商未报告命中 token（按未命中计）」（**不按 0 计**）；`cached_prompt_tokens > prompt_tokens` ⇒ 报错（不静默钳制）；旧后端实现（不设该字段）与既有单测**逐条不变**；本地缓存命中零成本、零后端调用、不占额。（C5/C7；FR-010）

- [X] T1916 扩展 `tests/unit/test_billing_core_purity.py`（先写、确认失败；承接 T1906）：C10 **两层断言 + 一条条件义务，缺一不可且按规则判定（不用文件白名单）**——① **显式性**：`core/`、`agents/`、`ops/` 内任何 `LLMGateway(...)` 构造**必须显式传** `spend_guard=`（含显式 `None`），同函数内构造 `MockBackend`（或测试桩）的离线装配 ⇒ **要么**显式 `None`（清单 `OFFLINE_ASSEMBLIES` 常驻）**要么**登记进"离线但注入真守卫"清单（`OFFLINE_GUARDED_ASSEMBLIES`：019 的离线演示端到端走真门禁）——两类**互斥**、**新增一处即红**；② **保证性**：引用 `HttpBackend` / `_llm_backend(kind="http")` 的**真实渠道装配点**（**两处**：`agents/pilot/backends.py:210-214`、`ops/smoke_llm.py:193-198`）必须传**非 `None`** 的 guard，清单常驻，且 `core/` 与 `agents/` 下不得出现 `spend_guard=None`；③ **条件义务（堵洞）**：`ops/screenplay.py:167` 与 `ops/dev.py:168` 的注入后端路径**当前不可用**（`--backend http` → 裸构造 `HttpBackend()` 恒抛 `GatewayError`，构造器要求显式注入 `base_url`/`api_key`），故按①显式 `None` 登记；但断言须**按规则**判定（引用 `HttpBackend` / `_llm_backend(kind="http")` 的构造点**必须在②清单内**，不用文件白名单）——一旦它们被改成可用装配路径即红，必须连同 guard 一起改。（C10；US1 场景 5）

- [X] T1917 `core/llm_gateway/profiles.py`：`price_matrix` 解析/校验（格位键 = `<峰谷>_<缓存>`，**声明即四格齐备**、不回落基础价，取值校验沿用 `_parse_prices`）+ `price_cell(profile, *, moment, cache_hit) -> (cell_key, prices)`（由折算与估算**同取**）+ `declared_dimensions`；`to_snapshot()`（`:98-112`）新增**可选**键 `price_matrix`/`declared_dimensions`，`prices` 与既有键逐字保留、**仍不含密钥**，指纹（`:131-138`）自然覆盖新键；读取端**按 `price_matrix` 是否在键集中分派**（不按版本号猜），**旧快照永不重写**。**并把 `ModelProfile.cost_usd`（`:92-96`）改为委派 `price_cell`**——它是**第二条折算路径**（硬编码线性式，与网关折算两套口径），两维价目一改就会与 `price_cell` 脱钩；签名与返回语义不变、调用方（`tests/unit/test_llm_profiles.py:58`/`:104`）零改动，使全仓**只有一份**折算口径。（C5/C8；FR-010）

- [X] T1918 `core/llm_gateway/gateway.py`：`BackendResult`（`:38-52`）**末位追加**可选 `cached_prompt_tokens: int | None = None`（既有后端与测试构造零变化）；`LLMResult` 如实透传命中 token 数与所用格位键；折算（`:311-314`）改取 `price_cell`；口径备注分三种（未声明 matrix ⇒「未区分峰谷/缓存」；厂商未报命中 ⇒「按未命中计」）；本地缓存命中（`:283-294`）不进任何格位、不占额。**折算口径唯一性的延伸**：`ModelProfile.cost_usd`（`profiles.py:92-96`）这条**第二折算路径**改为委派 `price_cell` 的动作在 T1917 落地（本任务只需保证网关侧引用同一函数）。（C5/C7）

- [X] T1919 `core/llm_gateway/gateway.py`（承接 T1918，同文件故不并行）：`LLMGateway(..., spend_guard=None)` **可选**注入（`None` 时行为与现状逐字节一致、独立可用、既有用例零改动）；**固定六步序列**（① 角色路由与取价 → ② 本地缓存判定（命中即返、零成本、不过门禁）→ ③ 门禁 `check` → ④ 后端调用 → ⑤ 入账 → ⑥ `reservation.settle(actual)`）；`SpendRequest`（含 `channel_id`/`stage`/`estimated_usd`，**预估由网关填，guard 不自行估算**）；拒绝 = `BudgetRefusedError`（`GatewayError` 子类）：`call_count`/`total_cost_usd`/`_breakdown` **均不变**、缓存不写、异常上抛；分型互斥（`budget_refused` ≠ 401 `PermanentBackendError` ≠ 429 `TransientBackendError`，各有独立 `kind`）；实测超预估 ⇒ 如实入账 + `over_limit` 告警 + 后续拒绝（不回滚）；缺 `stage=` ⇒ 拒绝 `tier_undeclared`。**本任务只做网关侧**：调用点侧的**零成本分支**由 T1950/T1951 落地——本任务交付的"网关侧三量不变"**不等于**"拒绝即零入账"，调用点仍须自己走分支。（C9/C10；FR-002/003）

- [X] T1920 [P] `core/llm_gateway/backends/http.py`：从厂商响应 usage 读取命中 prompt token 并填 `cached_prompt_tokens`（协议字段，**不新增档案配置键**）；缺失/读取失败/非法 ⇒ `None`（由 T1918 记口径备注）；命中数 > prompt token 数由网关报错。（C7）

- [X] T1921 [P] 预估口径收敛（**属主 = `core/llm_gateway`**）：把既有成本上界估算（prompt `max(1, len(prompt)//2)` token + completion `max_tokens` 满额，与 `price_cell` 同源）落为 `core/llm_gateway` 内的**唯一实现**，并把两处第二份收敛为薄调用：`agents/screenplay/loop.py:281-289`（口径定义，调用点 `:672`/`:691`）与 `agents/dev/loop.py:160-171`（同一公式的第二份）。**排除** `core/llm_gateway/backends/mock.py:49`（`prompt_tokens=max(1, len(prompt)//2)` 是**模拟后端的 usage 生成**，用途不同，并入即语义错位）。估算额 / 网关记账 / 厂商账单**三数分离**并写进报告与校准记录。（C10）

- [X] T1922 [P] `ops/` 内 8 处离线/桩装配点补显式 `spend_guard=None`（行为零变化、机械改动）：`ops/demo_editing_loop.py:219`、`ops/demo_storyboard_loop.py:205`、`ops/screenplay.py:167`、`ops/demo_dev_loop.py:793`、`ops/demo_visual_loop.py:79`、`ops/demo_screenplay_loop.py:271`、`ops/dev.py:168`、`ops/demo_promo_loop.py:96`。**这 8 处不是同一类**（plan 原记的构造点数现为 **13 处** = 真实 2 处 + 离线显式 `None` 8 处（本任务）+ 离线但注入真守卫 3 处（`ops/demo_billing.py`，019 阶段 6 的离线演示；清单 `OFFLINE_GUARDED_ASSEMBLIES`），已按实测更正）：其中 **6 处**是同函数内构造 `MockBackend`（或测试桩）的离线演示装配（`demo_editing_loop.py:219`、`demo_storyboard_loop.py:205`、`demo_dev_loop.py:793`、`demo_visual_loop.py:79`、`demo_screenplay_loop.py:271`、`demo_promo_loop.py:96`）；另外 **2 处**（`ops/screenplay.py:167`、`ops/dev.py:168`）是**当前不可用的真实装配路径**——`--backend http` 走裸构造 `HttpBackend()`，而裸构造恒抛 `GatewayError`，故今天到不了真实调用面。按 T1916③ 的**条件义务**：一旦它们被改成可用装配路径，断言即红，**必须连同非 `None` 的 guard 一起改**（不得只补 `None` 了事）。真实两处（`agents/pilot/backends.py:210-214`、`ops/smoke_llm.py:193-198`）在 T1928 注入非 `None`。（C10）

**检查点**: ✅ 四格取价与 legacy 原语义断言全绿；`spend_guard=None` 时既有网关用例逐条不变；两层注入断言（T1916）就位（此时因真实两处尚未注入而非 `None` 而**应红**，由 T1928 转绿）；`agents/screenplay/loop.py` 与 `agents/dev/loop.py` 无第二份估算公式

## 阶段 4：用户故事 1 - 最小规模校准与前置预算门禁（优先级：P1）🎯 MVP

**目标**: 额度按环节分档声明（缺项即拒绝启动）、调用**前**校验（超限即拒绝、不先花后报）、被拒调用 0 调用 0 入账、最小规模校准记录 append-only、校准未过扩量被拒并留痕。

**独立测试**: Mock 后端 + 夹具账单可完整测试：额度缺项即拒启动；超限调用后端 0 次且不入账；最小规模调用后产出校准记录（含实测偏差与口径备注）；校准未过时扩量请求被拒且留痕、配置未被改写。

### 用户故事 1 的测试（先写，确认失败后再实现）

- [X] T1923 [P] [US1] `tests/unit/test_billing_config.py`：C9 校验矩阵（缺 `budget` 段 / 缺某环节档 / 缺 `peak_windows.timezone` ⇒ 装配报错，不取默认；`on_exhausted: queue`、`limit_usd: 0` 或 bool ⇒ `BudgetConfigError`；`note` 缺失 ⇒ notes 告警但可启动）+ 两形态取值差异**由配置承载**（额度/窗口取值不同且可指认）+ 零形态分支（无 `form ==` 判断）。（C9；FR-001/014）

- [X] T1924 [P] [US1] `tests/unit/test_billing_gate.py`：C10 固定六步序列次序即语义；预估额 > 余量 ⇒ `BudgetRefusedError` + 后端 **0 次调用** + `call_count`/`total_cost_usd`/`_breakdown` 均不变 + 缓存不写 + 原因与预估价落 `alerts.jsonl`（`kind=budget_refused`）+ 账本 `refusals` 计数；本地缓存命中不过门禁（零成本、零后端调用、不占额）；`spend_guard=None` 时既有装配行为逐字节不变；缺 `stage=` ⇒ `tier_undeclared`；实测超预估 ⇒ 余量可为负 + `over_limit` 告警 + 后续调用拒绝、已发生花费如实入账不回滚；三类错误分型互斥且 `kind` 可辨（`budget_refused` / 认证失败 401 / 限流 429）。（C10；FR-002/003；SC-004）

- [X] T1925 [P] [US1] `tests/unit/test_billing_ledger_concurrency.py`：C11 两进程并发抢同一档 ⇒ 总入账 ≤ `limit_usd`、`revision` 单调、无丢失更新（预留使并发不超额）；`reserved += estimated` → `settle` 后 `reserved -= estimated; spent += actual`、余量 = `limit − spent − reserved`；锁被占满超时 ⇒ `BudgetLedgerError` 拒绝调用（不无锁写、不静默放行）；崩溃残留预留 ⇒ 如实列出（`reserved_usd > 0` 且有未结算标记）、不自动清零；窗口滚动（`run`/`day`/`period` 键变更即归零，旧窗口记录保留）。**登记边界**：单主机多进程安全，多主机需换 PG（**未做**）。（C11；US1 场景 1）

- [X] T1926 [P] [US1] `tests/unit/test_billing_calibration.py`：C12 校准记录字段齐备（渠道 / 环节档 / **当时价目快照含 `price_matrix` 与 `peak_windows_snapshot`** / 实测花费与样本量 / `deviation` / `passed` / `reasons` / 口径备注 / `at`）+ append-only（同键重产拒绝）+ **`raise_tier` 六条拒绝条件各一例**（① 无 `calibration_id`；② 记录不存在或不同渠道；③ `passed != true`；④ 样本量 < `min_samples`；⑤ 超期 `record_ttl_days`；⑥ `deviation` 超容差）+ 拒绝时**配置未被改写**且留痕 `kind=uncalibrated_raise`；合格时定点改写额度 + `calibrated_by` 落键 + `kind=tier_raised`，其余段与注释**逐字节不变**；渠道状态派生 `untested|pass|fail|stale`。（C12；FR-004/005；SC-005）

- [X] T1927 [P] [US1] 扩展 `tests/unit/test_no_vendor_literals.py:86-138`（先写）：**每个 `.chat(` 调用点声明 `stage=`**（环节 id）且取值 ∈ **两形态** `budget.tiers` 键集；调用点计数仍为 **8**（不得新增）。**这 8 处 = 该测试扫描域内的 8 处**（`SCAN_ROOTS = core, agents, dreaming`，`core/llm_gateway/` 除外），故**含 `dreaming/candidates.py:79`**、**不含 `ops/smoke_llm.py:215`**（后者在 `ops/` 下，属 T1916② 的另一条装配面断言，不并入本条计数）。注入了 guard 而请求缺 `stage` ⇒ 运行期拒绝（与 T1924 的 `tier_undeclared` 同源）。（C9/C10；FR-003）

- [X] T1950 [P] [US1] `tests/unit/test_billing_refusal_branch.py`（先写，确认失败）：C10 **调用点分支规则**——普查到的**每一处**处理点各一例，断言 `BudgetRefusedError` 走**零成本分支**：**被拒的那一笔不入账**（该笔 `llm_calls=0`、`llm_tokens=0`、`generation_api_cost_usd=0.0`；同轮**已发生**花费照记）、后端 **0 次调用**、`call_count`/`total_cost_usd` 不变、失败原因**点名"预算拒绝"**且含剩余/所需额度；同一轮里**已发生**的花费照记、**被拒的那一笔不入账**（"花了的钱"与"没花的钱"可分辨）。**调用点普查（11 处调用面 = 10 处处理器 + 1 处裸上抛，逐处一例）**：① **计费分支**（拒绝会被记成预估花费，**必修**）：`agents/screenplay/loop.py:698`（`except GatewayError` → `_fail_stage(actual=estimated, cost=CostRecord(llm_calls=1, generation_api_cost_usd=estimated))`）与 `agents/dev/loop.py:645`（→ `_fail_round(actual=estimated, cost=CostRecord(llm_calls=len(calls) + 1, generation_api_cost_usd=estimated))`）；② **归因分支**（拒绝须可辨，不得误报为厂商失败/后端不可用）：`ops/smoke_llm.py:514`（`except (SmokeError, GatewayError, ProfileConfigError)` → 现统一 `smoke_failed`）、`ops/screenplay.py:142` 与 `ops/dev.py:147`（`except GatewayError` 包住 `_backend(args)`，现文案"网关后端不可用"；装配期不产生拒绝，但分支仍须先写）；③ **兜底吞没点**（判官的 `.chat` 无处理器，拒绝上抛到"崩溃隔离"的 `except Exception`，失败原因会写成"评估器崩溃/生成失败"）：`agents/screenplay/loop.py:786`（判官 `agents/screenplay/evaluators/dramatic_tension.py:117`）、`agents/visual/loop.py:495`（`agents/visual/evaluators/cinematic.py:77`）、`agents/storyboard/loop.py:574`（`agents/storyboard/evaluators/script_fit.py:90`）、`agents/editing/loop.py:611`（`agents/editing/evaluators/narrative.py:90`）、`agents/promo/loop.py:313`（`agents/promo/material.py:30`；该处 `CostRecord()` 已全零，只须原因点名）；④ **裸上抛**（**无处理器**，只断言"不上错账"）：`dreaming/candidates.py:79`（经 `dreaming/pipeline.py:160` 上抛——不入账，也不得被泛化成厂商失败归因）。断言方式：①②④ 用夹具 guard 走真实现路径，③ 用最小桩评估器让拒绝上抛；**不得**用文件白名单跳过任一处。（C10；FR-002/003；SC-004）

### 用户故事 1 的实现

- [X] T1928 [P] [US1] 两处**真实渠道装配点**注入非 `None` 的 `SpendGuard`（**链内唯一装配点 + 校准入口，共两处**接线，与 015 的"A/B 一行切换"同源）：`agents/pilot/backends.py:210-214`（整条链唯一装配点，函数入口 `:184 build_backends`）与 `ops/smoke_llm.py:193-198`（最小规模真实调用/校准的装配点）；装配 `FileLedger`（`budget.ledger.root` + `lock_timeout_seconds`）+ 档位配置 + 渠道 id + `alerts.jsonl` 写手；`core/llm_gateway` 对 `core/billing` 仍**零 import**。此任务使 T1916 的保证性断言转绿。（C10；US1 场景 5；SC-004）

- [X] T1929 [US1] 额度与峰谷快照冻结（承接 T1903，同文件故不并行）：装配时把生效档位定义 + `peak_windows_snapshot{timezone, attribution, windows}` + `channel_id` + 账本 `revision` 并入 Agent 的 `config_snapshot["budget_tiers"]`（**未接入时不落键**，镜像 `profiles.py:527-535` 的 `with_llm_profiles` 口径）——改额度只影响此后新装配，历史节点不变。（C9/C6；FR-001）

- [X] T1930 [US1] `core/billing/calibration.py` 扩量面（承接 T1911，同文件故不并行）：`raise_tier(channel, tier, limit_usd, *, calibration_id, by, reason)` 六条拒绝条件实现 + 落地动作 = 经 `core/yaml_edit.py`（T1905 核查结论）**定点改写**配置额度 + 写 `calibrated_by` + 向 `alerts.jsonl` 追加 `tier_raised` / `uncalibrated_raise` 留痕；拒绝时不触配置。（C12；FR-005；SC-005）

- [X] T1931 [US1] 8 处 `.chat(` 调用点补 `stage=<环节 id>`（**计数仍为 8**，且**就是 T1927 扫描域内的 8 处**）：`agents/screenplay/loop.py:691`、`agents/dev/loop.py:626`、`agents/promo/material.py:30`、`agents/screenplay/evaluators/dramatic_tension.py:117`、`agents/visual/evaluators/cinematic.py:77`、`agents/storyboard/evaluators/script_fit.py:90`、`agents/editing/evaluators/narrative.py:90`、**`dreaming/candidates.py:79`**；取值须 ∈ 两形态 `budget.tiers` 键集（T1927 机检）。**另有一处 `ops/smoke_llm.py:215`**（在 `ops/` 下、**不在**该扫描域内）：同样补 `stage=` 以与 T1928 的校准装配面口径一致，但它**不计入** T1927 的"8 处"计数。**与 T1928 共用 `ops/smoke_llm.py`，串行执行**。（C9；FR-003）

- [X] T1951 [US1] 调用点零成本分支落地（逐处改，与 T1950 的普查一一对应）：把 `BudgetRefusedError` 的捕获**置于** `except GatewayError` / `except Exception` **之前**，走零成本分支——FAILED 节点/标记照写、**被拒的那一笔不入账**（该笔 `llm_calls=0`、`llm_tokens=0`、`generation_api_cost_usd=0.0`）、原因点名"预算拒绝 + 剩余/所需额度"、留痕 `kind=budget_refused`；`ops/smoke_llm.py` 的拒绝单列 `reason=budget_refused`（**不与 `smoke_failed` 混**）。**已发生**的花费（同轮前序生成）照记、**被拒的那一笔不入账**；**不改**"失败照计预估成本（原则二）"对**已发生**花费的既有语义——只把拒绝从该分支里摘出来。**禁止**把拒绝并入"失败照计预估"（C10 明文禁止）。**普查与断言**见 T1950。（C10；FR-002；SC-004）

**检查点**: ✅ `pytest tests/unit -k billing` 绿（门禁/账本/校准）+ `test_no_vendor_literals` 扩展断言绿；超限调用后端 0 次、`call_count`/`total_cost_usd` 恒 0；`raise_tier` 六条拒绝各有用例；两层注入断言转绿

## 阶段 5：用户故事 2 - 厂商账单导入与差异报告（优先级：P2）

**目标**: 规范化导入面（来源/批次/币种/周期/原始行摘要/口径备注，全成或全败、批次幂等）+ 逐项差异报告（每条差异必带分类，无分类即不可解释 ⇒ 告警）+ append-only 不可改写 + 无账单不得产"零差异"报告。

**独立测试**: 夹具账单（含刻意构造的口径差、缺失条目、时序错位、折扣、异币种）→ 逐项分类正确、不可解释项 100% 告警；篡改报告或改写系统字段被拒；重复导入同一批次被拒；未识别格式报错且零落盘。

### 用户故事 2 的测试（先写，确认失败后再实现）

- [X] T1932 [P] [US2] `tests/unit/test_billing_bill_import.py`：C2 夹具账单字段齐备 + 来源/批次/币种/周期/`raw_ref`/`fetched_at` 留痕齐全；内置 `csv_lines`/`json_lines` + 注册表（`register_importer`/`importer_for`）；未注册格式 ⇒ `UnknownBillFormatError` 且**零落盘**（无部分导入）；重复批次拒绝；列映射缺声明即报错（`entry_id`/`amount`/`currency`/`period`/`line_kind`/`amount_sign`）；异币种缺 `fx{rate, source, at}` 报错；`amount` 为负或 bool ⇒ 拒绝；`source ∈ {export, api}` 同构。C3 快照面：`system_digest` 机检、同键重产拒绝、`overrides` 只追加、改写系统字段或省略条目 ⇒ 拒绝。**对抗面（本特性的，不是"沿用既有"）**：伪造账单条目（假 `raw_ref`、自造金额）、改写已落盘账单/报告后再导入、以及**门禁绕过**尝试（不传 `guard` 的装配、伪造 `--tier`）——这些是本特性必须自证的对抗用例。（C2/C3；FR-006/009；SC-006/007）

- [X] T1933 [P] [US2] `tests/unit/test_billing_reconcile.py`：C13 六类差异逐项分类正确、`delta_usd` 实测偏差与 `note` 口径备注齐备；**每条差异必须带分类**，缺失或取值域外 ⇒ `unclassified` ⇒ `unexplained=true` ⇒ 告警（100%）；`|delta| ≤ amount_tolerance_usd` 仍须分类与备注；超 `alert_threshold_usd` ⇒ `delta_over_threshold`；**报告必须含 `bill_refs[]`（缺即 `ReconciliationError` 拒绝产出）**；**无账单批次 ⇒ 拒绝产出**（不产"零差异"报告）；时序错位/未结账按"留待下期"分类且**不得**判定网关记账有误；免费额度与折扣条目不静默按 0 记账。（C13/C14；FR-007/008；SC-002/009）

- [X] T1934 [P] [US2] `tests/unit/test_billing_alerts.py`：C14 告警件结构（`billing/{channel}/alerts.jsonl` 只追加；字段 `kind`/`at`/`channel_id`/`period`/`detail`/`ref`；`kind` 取值域 = `budget_refused`/`over_limit`/`unexplained_delta`/`delta_over_threshold`/`tier_raised`/`uncalibrated_raise`）；报告含未解释项或超阈值 ⇒ 判定返回"有告警"（core 层信号，供 CLI 退出码 1 使用）；无告警 ⇒ 信号为空。（C14；SC-002）

### 用户故事 2 的实现

- [X] T1935 [P] [US2] `core/billing/bill.py` 落盘面（承接 T1909，同文件故与 US2 测试任务并行）：账单导入记录落 `billing/{channel}/bills/{bill_id}.json`（`system_digest` + 同键重产拒绝 + `overrides` 只追加 + 人工批注不触系统字段）；批次幂等 IO（`load_bill` / 已存在即拒绝）；**全成或全败**的落盘语义（任一行非法 ⇒ 零文件产生）。**本特性的对抗面**：**伪造与篡改**——自造 `raw_ref`、改写已落盘账单的系统字段、删改条目后再导入；这些必须被 `system_digest` 机检挡住，本特性**自行举证**（不是"沿用既有对抗面"）。（C2/C3/C4；FR-006/009）

- [X] T1936 [P] [US2] `core/billing/reconcile.py` 产物与告警面（承接 T1910）：报告落 `billing/{channel}/reports/{period}.json`（`system_digest` 机检、同键重产拒绝、`overrides` 只追加、`thresholds_snapshot`/`bill_refs`/`unexplained`/`alerts` 齐备）；`bill_refs` 缺即拒绝产出；无账单 ⇒ `ReconciliationError`；`alerts.jsonl` 落 `unexplained_delta`/`delta_over_threshold`；报告文本与字段写明"时序错位/未结账不得据此判定网关记账有误"。（C13/C14；FR-008；SC-002/006/009）

**检查点**: ✅ 六类分类逐项正确、不可解释项 100% 告警；重复批次与未识别格式 100% 被拒且零落盘；报告缺 `bill_refs` 或无账单时拒绝产出；`alerts.jsonl` 只增可追溯

## 阶段 6：用户故事 3 - 两维价目与连续运行证据（优先级：P3）

**目标**: 峰谷 × 缓存命中四格取价随快照冻结、改价后历史复算逐字节不变；按时间索引的运行记录让"连续运行 ≥7 天"可机检（覆盖 + 连续双条件，断档如实报出）；CLI 与离线演示闭合全部机制。

**独立测试**: 同一调用在四种组合下取价正确；改价后历史节点成本逐字节不变；价目缺维度即报错；运行记录机检同时要求覆盖 ≥7 天与连续；仅累计够天数但有断档**不通过**（缺口如实报出，不插值）。

### 用户故事 3 的测试（先写，确认失败后再实现）

- [X] T1937 [P] [US3] `tests/unit/test_billing_runlog.py`：C15 连续夹具（窗口内逐日 `source=real`）⇒ `meets=true`、`gaps=[]`、`max_gap_days=0`；**散点夹具**（8 天真实运行但中间缺 2 天、容差 0）⇒ `covered_days=8 ≥ min_window_days` 仍 `meets=false`（`max_gap_days=2 > 0`），`gaps` 如实呈现且**文件内无插值条目**；容差放开（`gap_tolerance_days=2`）⇒ 同一夹具 `meets=true` 而 `gaps`/`max_gap_days` **照旧可见**；仅 5 天 ⇒ `meets=false` 且给出覆盖差值；回落日（`source=fallback`）**不计入** `covered_days` 且标原因；`head_digest` 链被篡改/删条目 ⇒ 读取报错；当日 `sealed` 后追加拒绝；日期口径 = `peak_windows.timezone`。（C15；FR-012/013；SC-001）

- [X] T1938 [P] [US3] `tests/unit/test_billing_gateway_cells.py`：经 `gateway.chat` 的**四格端到端取价**（峰/谷 × 命中/未命中 ⇒ `cost_usd` 分别等于格位折算值、`LLMResult` 标注格位键）；本地缓存命中与厂商缓存**不混算**（本地命中零成本、不进格位、不占额）；改价后按历史节点 `config_snapshot` 复算**逐字节不变**、新节点用新价目；报告/账目口径备注含 `attribution=call_start` 与区间口径 `[start, end)`。（C5/C6/C7/C8；SC-003）

- [X] T1939 [P] [US3] `tests/unit/test_billing_cli.py`：C16 七子命令的参数解析与退出码语义（`0` 成功 / 无告警；`1` 执行失败或拒绝或有告警；`2` 用法或配置错误）+ JSON 输出 + 凭证只报"是否设置 + 长度"、**绝不回显值** + `import-bill` 不联网 + `reconcile` 有告警/无告警两态 + `raise-tier` 走定点改写（配置其余段逐字节不变）。（C16；FR-015）

- [X] T1940 [P] [US3] `tests/contract/test_billing_contracts.py`：**C1~C16 聚合断言**（含 C14 的两条腿契约用例——报告含未解释项却返回 0 / 省略未解释项 / 缺 `alerts` 落盘 ⇒ 红；C9 的 `stage=` 静态断言；C10 的超限调用后端计数 0 与 `call_count`/`total_cost_usd` 不变、**调用点零成本分支（被拒的那一笔不入账——该笔全零；同轮已发生花费照记；原因点名拒绝）**；C16 的 CLI 退出码）。**本特性的对抗/篡改面在此聚合举证**（不是"沿用既有对抗面"）：伪造 `covered_days`（自造运行记录条目）、改写账单/报告后再对账、**门禁绕过**（不传 guard 的装配、绕过 `stage=`）——各 100% 被拒或报警。（SC-004/005/006/007）

### 用户故事 3 的实现

- [X] T1941 [P] [US3] `core/billing/runlog.py` 窗口机检面（承接 T1912）：`window_coverage(channel_id, *, end, min_window_days, gap_tolerance_days) -> {covered_days, gaps[], max_gap_days, continuous, meets}`；`covered_days` **只计 `source=real` 的日期**；`meets = covered_days ≥ min_window_days` **∧** `max_gap_days ≤ gap_tolerance_days`（覆盖 + 连续双条件，两项均由配置声明）；`continuous = not gaps`（与容差无关，与 `meets` 并列呈现，通过不谎报为"连续"）；未达标时给出**归因**（覆盖差值与最长断档差值与逐段 `gaps`）；**如实列出、禁止插值补齐**。（C15；FR-012；SC-001）

- [X] T1942 [P] [US3] 运行记录写入接线（与网关调用同源；承接 T1928 的同一装配面）：一次真实调用 = 一条 entry，落 `source=real|simulated|fallback` + `fallback_reason` + `cost_source`（网关记账值 / 实测回填），使花费可回溯（不凭报告自证）；真实渠道失败**禁止**静默回落模拟并照常计费（回落必须显式声明 + 原因 + 来源标注）；落点 `agents/pilot/backends.py` 与 `ops/smoke_llm.py`（不新增 `.chat(` 调用点，计数仍 8）。**`source=fallback` 为保留值（本特性无写入点）**——装配期即拒、网关无回落路径（`backends.py:235-243`），本任务只实现"读取时不计入 `covered_days` 且必须带原因"，**不实现**回落写入。**判断调用（须留痕）**：写入点 plan 未逐行指定，按"与**链内唯一装配点 + 校准入口，共两处**同一处"落地。（C15；FR-013）

- [X] T1943 [P] [US3] `ops/billing.py` 七子命令（薄转发，判定全在 core；命名与契约逐字一致）：`tiers`（各档余量/拒绝计数/**未结算预留**）、`calibrate`（**只读**既有真实调用记录——`ops/smoke_llm.py --round` 的运行记录 + 网关账目/账本记录——算出偏差后落校准记录；**本身不联网、不构造后端、不新测花费**，故**不产生第三个真实装配点**，"实测花费"是既有记录的复述，C12）、`import-bill`（不联网）、`reconcile`、`alert-check`（只读既有产物：报告未解释项 + `alerts.jsonl` 增量）、`runs`（`--window-days`，未达标如实报缺口与差值）、`raise-tier`；退出码 `0/1/2`，JSON 输出；凭证只报"是否设置 + 长度"。（C12/C16；FR-004/015）

- [X] T1944 [US3] `ops/demo_billing.py` 离线六步（Mock 后端 + 夹具账单，**零真实调用、零外部网络**）：① 额度声明与缺项拒绝 → ② 最小规模校准记录 → ③ 未校准扩量拒绝留痕 → ④ 夹具账单导入 + 对账分类（含未解释项告警、重复批次拒绝、未识别格式报错）→ ⑤ 跨进程账本并发共享额度 → ⑥ 两维取价四格 + 改价后历史复算逐字节不变 + ≥7 天窗口机检（断档如实报缺口）；退出码 0 = 六步全 ok。依赖 T1943 的 CLI。（C16；SC-001/003）

**检查点**: ✅ 四格取价与"改价不漂移"全绿；散点夹具不判通过、断档如实报出；七子命令退出码语义正确；demo 退出码 0 且六步全 ok；契约聚合 C1~C16 绿

## 阶段 7：打磨与横切关注点

- [X] T1945 [P] `README.md` 新增"真实渠道与账单对账（功能 019）"章节（七子命令与用法、`billing/{channel}/` 目录、新增门禁 `alert-check`、诚实边界：只做 LLM 渠道 / 单主机账本 / 未标定额度）

- [X] T1946 [P] `specs/019-real-channel-billing/quickstart.md` 验证记录逐条回填（各套件通过数、CLI 各命令退出码与关键输出、demo 退出码与耗时、覆盖率、ruff 双绿、五处登记点同步复核、`test_no_vendor_literals` 调用点断言扩展、**真实最小规模校准与 ≥7 天窗口留"待运营"**）

- [X] T1947 `.github/workflows/billing_alerts.yml`（C14 的新增门禁本体）：cron **错峰** + `workflow_dispatch`（对齐 `.github/workflows/cost_regression.yml:8-10`），跑 `uv run python ops/billing.py alert-check --channel <id>`，**非零退出即告警**；只读既有产物、**零真实调用、零凭证**。依赖 T1943 的 CLI。

- [X] T1948 门禁复核（命令与 `.github/workflows/ci.yml` 逐字一致）：`uv run ruff check .` + `uv run ruff format --check .`；`uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`；`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`；`uv run pytest tests/integration -m integration`（本特性**无 DB 迁移**，既有回归不降）；`uv run pytest tests/adversarial -m adversarial`（既有对抗套件**照跑不放松**；**本特性的对抗面**——伪造 `covered_days`/运行记录、改写账单或报告后再对账、**门禁绕过**（不传 guard / 伪造 `stage=`）——由 T1932/T1935/T1940 的新用例举证，**不并入**既有套件）；`uv run pytest tests/unbiasedness -m unbiasedness`（不放松）；`uv run python ops/demo_billing.py` 退出码 0。

- [X] T1949 `docs/三期立项书.md` §3.1 的 G3 行（`:53`）标注交付状态，并把"预算与账单差异告警"新增门禁（`:129`）的落点（`alerts.jsonl` + `alert-check`/`reconcile` 退出码 + 契约用例 + `billing_alerts.yml`）与**结转项**（媒体渠道与投放侧协议校准 → G4、生成侧厂商对接 → G2）记入交付说明。

## 依赖关系与执行顺序

### 阶段依赖

- **阶段 1（搭建）**: 无依赖，可立即开始；T1904 与 T1903 **须同批落地**（缺额度不得启动的取舍）
- **跨阶段前置（F8，如实登记）**: 阶段 1 的检查点**不可能在阶段 2 之前变绿**——登记点②（`CONFIG_CLASSES` 引用 `core.billing.budget.BudgetConfig`）、③、⑤ 与 T1903 的预检都要求 `core/billing/budget.py` 存在，而它在阶段 2 的 T1908 才落地。故阶段 1 的完成口径 = "配置段与登记点改动就位"，其检查点的全绿在 T1908 之后复核。**不把 `budget.py` 骨架提前**：提前会把"五模块一次落地"的阶段 2 拆成两半，且 `BudgetConfig` 与 `FileLedger`/`SpendGuard` 同文件（C1 明文不许新增第六/第七模块），拆开的收益不抵成本
- **阶段 2（`core/billing` 五模块）**: 依赖阶段 1（`budget:` 段与夹具）；**阻塞全部用户故事的装配级工作**
- **阶段 3（`core/llm_gateway` 侧）**: 依赖阶段 2（`SpendRequest`/`BudgetRefusedError` 类型来自 `core/billing`，依赖单向）；T1916 的两层断言在此写就并**预期红**，由 T1928 转绿
- **阶段 4（US1）**: 依赖阶段 3；T1929 承接 T1903（同文件），T1930 承接 T1911（同文件），T1931 承接 T1927
- **阶段 5（US2）**: 依赖阶段 2（`bill.py`/`reconcile.py` 判定面）；T1935/T1936 分别承接 T1909/T1910（同文件、跨阶段串行）
- **阶段 6（US3）**: 依赖阶段 3（`price_cell` 与命中字段）与阶段 5（demo 步④需对账面）；T1941 承接 T1912，T1942 承接 T1928 的装配面；T1944 依赖 T1943
- **阶段 7（打磨）**: 依赖全部用户故事完成；T1947 依赖 T1943

### 并行机会

- T1902 / T1903 / T1904 / T1905 文件互不重叠，可并行（T1903 与 T1904 的**结论**须一致）
- T1906 / T1907（测试）与 T1908~T1912（五模块）文件互不重叠，可并行；**测试先于实现**；T1911 的 `raise_tier` 语义依赖 T1908 的 `FileLedger`
- T1913 / T1914 / T1915（三份测试文件）+ T1920 / T1921 / T1922（`backends/http.py` / 估算收敛 / `ops/` 8 处）互相独立；T1917 → T1918 → T1919 同属 `gateway.py` 与 `profiles.py` 链，**串行**
- US1 的 T1923~T1927（五份测试文件）与 T1928 / T1929 / T1930 可并行；T1931 与 T1928 共用 `ops/smoke_llm.py`，**串行**
- US2 的 T1932~T1934（三份测试）与 T1935 / T1936（两个模块，其余任务不触同文件）可并行
- US3 的 T1937~T1940（四份测试）与 T1941 / T1942 / T1943 可并行；T1944 需 T1943 之后

## 实现策略

### MVP 优先（用户故事 1）

1. 阶段 1 → 阶段 2 → 阶段 3（**不可跳过**：门禁注入点与类型必须先在位）
2. 阶段 4（US1）→ 独立验证：`pytest tests/unit -k billing` + `test_no_vendor_literals` + demo 步①②③（步④之后随 US2/US3 补齐）
3. 此时即交付"花得起、拒得掉"的价值：超限调用恒 0、校准记录留痕、扩量有先决

### 增量交付

1. US1 → 预算门禁 + 最小规模校准（MVP）
2. US2 → 账单导入 + 差异报告 + 告警门禁（"成本已核实"不再自证）
3. US3 → 两维价目 + 连续运行证据 + CLI/演示（SC-001/003 可机检）
4. 阶段 7 → 门禁工作流 / 验证记录 / 文档与立项书状态

---

## 备注

- **原则三落点（v2.0.0 三句式）**: ① 预算门禁 = T1923/T1924/T1925（分档声明 + 前置判定 + 拒绝零入账）+ T1928（**链内唯一装配点 + 校准入口，共两处**，两层断言由 T1916 常驻）+ T1931（`stage=` 环节归属）+ **T1950/T1951（调用点零成本分支——拒绝必须记成"没花的钱"）**；② 账单对账 = T1932/T1933/T1936/T1940（导入批次留痕 + 逐项分类差异 + 缺 `bill_refs` 拒绝产出 + 告警门禁，含"无账单不得产零差异报告"）；③ 最小规模校准 = T1926/T1930（校准记录 append-only + 扩量六条拒绝 + 定点改写与 `calibrated_by` 留痕）
- **原则一落点**: T1913/T1917/T1938 的"改价不漂移四件套"——旧快照**永不重写**、按 `price_matrix` 是否在键集中分派（不按版本号猜）、历史节点复算逐字节不变；峰谷时区/归属随 `config_snapshot["budget_tiers"]` 冻结（T1929），**不重复进档案快照**
- **有意改造（相对 017 模板）**: 五模块（即 US1/US2/US3 的机制本体）落在**基础阶段**，故 US 阶段的实现任务为**装配/接线/产物/CLI**面（跨阶段续改同一文件的任务已在描述中标"承接"并在依赖关系中标注串行）；US 阶段的测试任务仍先于该 US 的实现任务。C10 的两层断言按 plan 的钉名文件放在 `tests/unit/test_billing_core_purity.py`（T1916 扩展 T1906 的文件，顺序执行）
- **不做**（规格与 plan 已明确，防回潮）: 媒体渠道与投放侧协议校准（**结转 G4**）；生成侧真实厂商对接（**G2**）；`Decimal` 金额重构（沿用 float + 容差二分）；**多主机共享额度**（单主机文件账本边界）；自动比价路由与汇率引擎；适配器协议重写（`core/platform_http.py`、`agents/*/platform/http_real.py` 原样复用）；`CostRecord` 增列角色/档案（走运行报告层，历史节点不可按角色/档案回溯如实登记）；web 侧写入与 billing 看板（前端只读）
- **"阶段级额度归属"不在不做清单**（**判断调用，如实登记**）: 用户侧给出的清单含此项，但 research 决策 13 明确"**不属"不做"**——已由 C9 落定"（调用点声明 `chat(..., stage=<环节 id>)` + 静态断言取值 ∈ `budget.tiers` 键集，T1927/T1931 落实）；本特性**不做**的是**厂商按 token 计费时刻或账单周期摊分峰谷的双口径换算**（C6 登记为口径校准内容），已列入上一条
- **边界一：预算档数字由运营给定、代码零默认**——机制全部落地、取值全走配置；T1901 交付配置携带**最小规模档**并 `note` 标注"未标定"，`BudgetConfig` 缺 `note` 只告警不拦截；校准记录与差异报告逐条复述该备注；运营给定后**只改配置**（FR-001；spec 开放问题 1）
- **边界二：SC-001 需真实时间积累**——`covered_days` 只计 `source=real`、断档不插值；机制面（覆盖 + 连续双条件、散点不判通过、缺口如实报出）由 `pytest tests/unit -k billing` 与 **demo 步⑥** 机检；真实通道的 ≥7 天窗口与最小规模校准属运营动作，T1946 如实记"待运营"（FR-012；SC-001）
- **其他如实登记**: (a) 构造点普查 = **13 处**（真实 2 + `ops/` 离线显式 `None` 8 + `ops/` 离线但注入真守卫 3 = `ops/demo_billing.py`），plan 原记"11 处"先按实测更正为 10（见 T1922），019 阶段 6 加入离线演示后按实测更正为 **13**（阶段 7 复核：`tests/unit/test_billing_core_purity.py` 的 `test_构造点普查面有效` 断言 13）；(b) 一次性快照 helper 的落点 plan 未指定，按 C1"五模块"置于 `bill.py`（见 T1908）；(c) 运行记录写入点 plan 未逐行指定，按"与**链内唯一装配点 + 校准入口，共两处**同一处"落地（见 T1942）；(d) 缺口 1/6 的两处取舍**已决**：交付配置携带最小规模档取值 + 登记点④⑤必须同批落地（缺额度不得启动），留痕见 T1901/T1903/T1904；(e) **D1 调用点分支**：拒绝走**零成本分支**的普查与断言见 T1950/T1951（11 处调用面 = 10 处处理器 + 1 处裸上抛）
- **SC 映射**: SC-001→T1937/T1941/T1944（步⑥）/T1946；SC-002→T1933/T1936/T1940；SC-003→T1913/T1917/T1918/T1938/T1944（步⑥）；SC-004→T1924/T1928/T1916/**T1950/T1951**；SC-005→T1926/T1930；SC-006→T1932/T1935/T1936/T1940；SC-007→T1932/T1935；SC-008→T1948；SC-009→T1919/T1921/T1933/T1936
- 本地验证纪律：命令与 `.github/workflows/ci.yml` 逐字一致（含 `ruff format --check .`；覆盖率口径含 web；对抗/无偏性门禁不放松）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
