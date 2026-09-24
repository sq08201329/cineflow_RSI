# 任务列表：电影长片全链路编排（七 Agent 串链 + 选题产出 → 剧本输入交接 + 真实渠道分层）

**输入**: 来自 `specs/018-feature-film-pipeline/` 的设计文档（spec.md、plan.md、research.md、data-model.md、contracts/（C1~C13：`pipeline-chain.md` C1~C4、`dev-script-handoff.md` C5~C7、`render-index-scale.md` C8~C10、`package-evidence.md` C11~C13）、quickstart.md）

**前置条件**: 宪章 **v2.0.0**（**原则五为核心**：形态差异必须经 `configs/*.yaml` 表达、切换形态零代码改动、编排必须用自研轻量 DAG；**原则一**：同 `evaluator_id@version` 不得改行为、索引网格参数变更即升版本；**原则二**：节点不可变、`CostRecord` 对每个节点必须入账（含 `FAILED`）；**原则三**：真实渠道调用必须受预算门禁与账单对账双重约束、网关记账仅为内部口径；**原则四**：`dev` 链首装载人工策略适用例外条款，**三项替代约束**（静态检查在装载时生效 / 策略执行超时 / 策略零环境对象·不触网关）必须落机检；**原则六**：指标口径必须可被证伪、模拟与真实不得混淆、局限如实标注）；功能 015（六环节试水链 + 样片包五件套 + 可复现口径）、017（开发 Agent 降级件：轮次入口 `agents/dev/loop.py:490 run_dev_round`、导出面 `agents/dev/export_slate.py:37`、人工策略版本化与静态检查、契约 C14 的例外义务）、019（LLM 腿前置预算门禁 + 账单对账 + 运行记录 `core/billing/`；`LLMGateway(` 构造点普查 13 处）、001/002/004/009/016 已交付；规格含 2026-09-24 澄清会话**十二条**决议（含本轮新裁的 **"A + 排练档"**：`configs/movie.yaml` 的两处时长口径矛盾**按长片语义修 bug**——`editing.target_duration_s` 必须等于 `screenplay.target_duration_min × 60`（movie ⇒ **5400 秒**）、不一致即**拒绝启动并点名两处实测值**；体量常量（场景数/每场景行数）入形态配置；缩档**只经形态配置**；`ops/demo_pilot.py` 的脚本内改配置与恒假字面量断言一并修正；**索引码泛化为 R×C 块网格**、容量由该形态**派生镜头数**决定：`T1801` 的容量下界按 movie 原值 2700 镜复算），并新增 **SC-012**（口径一致性机检）

**测试说明**: 宪章要求 TDD——**测试任务与实现任务分列**（同 015/017/019 惯例），先写测试并确认失败再实现；全部用例走 **全模拟后端 + 夹具 + 确定性时钟**，**零真实花费、零外部网络**（真实运行与真实渠道凭证属运营动作，见文末边界）；覆盖率 ≥85%（口径不降，含 web）；本特性的关键是**七处集中声明点的清单同步不变量**、**五处既有登记点**、**索引网格容量上下界与升版义务**、**交接读取集的源码扫描式锁定与两层断言**、**证据面的缺项拒绝与逐字节可复现**；本地验证命令与 `.github/workflows/ci.yml` 逐字一致。

**组织方式**: 按用户故事分组（US1 七环节全链路与排练档 → US2 `dev → script` 交接契约 → US3 性能/预算门禁与证据面）；阶段 1 的两形态新键与五处登记点、阶段 2 的 `dev` 链首插入（清单同步 + 入口与产物 kind + 档位预检 + 策略装载下沉）、阶段 3 的索引网格与版本规则是三条故事线的共同前置。**契约编号口径**：本文件引用的 **C1~C13 是 018 自己的契约编号**（`specs/018-feature-film-pipeline/contracts/`），与 `tests/contract/test_pilot_contracts.py` 文件内部的 015 编号（该文件内亦称 C13）**同名不同物**（F-06）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（两形态新键 + 五处既有清单登记 + `ops/demo_pilot.py` 口径修复）

**⚠️ 关键**: 五处登记点必须与配置段**同批落地**——① `agents/pilot/pilot.py` 的 `config_completeness` 加载器元组（当前 `:117-131`）、② `tests/unit/test_config_integrity.py:23-37` 的 `CONFIG_CLASSES` 与 `:43-67` 的 `REQUIRED_PATHS`、③ `tests/unit/test_form_switch.py:260-281` 顶层差异键集、④ `tests/contract/test_pilot_contracts.py:423-440`（段差异集；**该文件内编号 C13**，与 018 的 C13 同名不同物——018 契约编号见 plan/quickstart 的口径注）、⑤ `tests/conftest.py:3159-3163`（`_MINIMAL_MOVIE_CONFIG` 与"不声明 `dev` 段……那属 G2/018"的**书面备忘**）。漏任一处的后果具体：`pilot` 段新键在某一形态缺失或解析器静默取默认时**无任何门禁会发现**，"缩档只改配置"（FR-013/FR-014）会在唯一的机检面上失效。另：**T1802 与 T1804 须同批落地**（新加载器一开启，精简夹具缺键即红）。

- [ ] T1801 `configs/movie.yaml` 与 `configs/shortdrama.yaml` 新增 `pilot` 段新键与 `storyboard.render.index_grid`，全键**两形态均须声明**：`pilot.scene_count`（场景数，唯一来源）、`pilot.lines_per_scene`（每场景行数）、`pilot.rehearsal.{status ∈ declared|unstandardized, work_kind ∈ rehearsal|real_work, scale{target_duration_s, script_target_minutes, script_tolerance_minutes, clip_duration_seconds}}`（**时长粒度**：`target_duration_s` 秒级浮点，可表达 **30 秒演示档**；分钟键为浮点分钟、`0.5` 合法；不变量 `target_duration_s == script_target_minutes × 60`，容差 `1e-6`）、`pilot.performance.{status, stage_seconds.<七环节 id>}`、`storyboard.render.index_grid.{rows, cols}`（**容量下界** `2**(R·C) ≥ 该形态派生镜头数`：movie 原值 2700 镜 ⇒ 容量 ≥ 12 位（如 `rows: 2, cols: 8`）、shortdrama 16 镜 ⇒ ≥ 4 位（如 `rows: 1, cols: 4`）；**量子上界** `2**C ≤ render.width`（movie 320 ⇒ `cols ≤ 8`、shortdrama 144 ⇒ `cols ≤ 7`）且 `1 ≤ rows ≤ height`）。两形态取值不同。**取值按排练档并在注释标注"未标定"**——具体数字属运营侧输入（spec 开放问题 2：**规格与代码都不得发明数字**），运营给定后只改配置（零代码改动）。**movie 的 `editing.target_duration_s` 同步修正为 5400**（= 90 分钟 × 60，与 `screenplay.target_duration_min` 一致；**这是修 bug**，不是发明运营数字）。（C8/C10/C13；FR-013/014/015；SC-010/011/012）

- [ ] T1802 [P] 登记点①②：`agents/pilot/pilot.py` 新增 `pilot` 段加载器（`PilotConfig.from_yaml`：`scene_count`/`lines_per_scene`/`rehearsal`/`performance` 全量校验，**缺段/缺键即 `PrecheckError`，不取码内默认**；**该加载器是这两个体量键的唯一解析者**，C-02）并登记进 `config_completeness` 的加载器元组（当前 `:117-131`）；`tests/unit/test_config_integrity.py:23-37` 的 `CONFIG_CLASSES` 增 `("pilot", "agents.pilot.pilot", "PilotConfig")`、`:43-67` 的 `REQUIRED_PATHS` 增 `("pilot", ("pilot", "scene_count"))` 与 `("pilot", ("pilot", "rehearsal"))`（缺项即红）。**判断项（须实现时留痕）**：`pilot` 段现有的 `backend`/`llm_backend`/`overrides` 由 `BackendSelection.from_yaml` 解析且**不拒绝段内未知键**（`agents/pilot/backends.py:91-130`），故新键不撞既有解析；新加载器**不得**复用 `BackendSelection`（键集与语义不同），须独立声明。**与 T1804 同批落地**。（data-model 登记点①②；FR-014）

- [ ] T1803 [P] 登记点③④（形态差异集两处，`pilot` 段取值按形态声明 ⇒ 必入差异集）：`tests/unit/test_form_switch.py:260-281` 的顶层差异键集加 `pilot`；`tests/contract/test_pilot_contracts.py:423-440` 的段差异集（该文件内编号 C13）加 `pilot`，并把注释里的段数口径（"14 个段"）随之更正。**判断项**：不并入 `tests/unit/test_form_switch.py:151-159` 的权重差异循环（它遍历 `evaluator_weights` 的七个 Agent，**017 起已含 `dev`**）；该循环本次**只复核、不改**（见 T1812）。（data-model 登记点条件项；FR-014）

- [ ] T1804 [P] 登记点⑤ + 夹具族（全落 `tests/conftest.py`）：① `:3159-3163` 的 `_MINIMAL_MOVIE_CONFIG` 补 `pilot` 段新键（`scene_count`/`lines_per_scene`/`rehearsal`/`performance`，含最小规模档取值）与 `storyboard.render.index_grid`，使 T1802 的加载器开启后不红；② 同处的**书面备忘**（"不声明 `dev` 段……017 不把开发 Agent 插入七阶段链，那属 G2/018"）改写为"`dev` 段由 018 插入，见阶段 2"；③ `:3065-3077` 演示档注释的"16 镜上限"随索引网格机制更正（改为"容量 = `2**(R·C)`"口径）。**不改坏既有夹具**；`dev` 段的实际补充见 T1812（同文件，串行）。**与 T1802 同批落地**。（C1/C8；FR-014）

- [ ] T1805 `ops/demo_pilot.py` 的口径修复与派生断言：① **移除脚本内改配置缩档**（`_derive_pilot_scale`，当前 `:51-61` 现改写临时副本的 `editing.target_duration_s` / `screenplay.target_duration_min` / `page_tolerance`）——改为**只经形态配置声明的排练档**（演示档取值以 `pilot.rehearsal` 声明落在配置副本里，脚本不再改键），与"缩档只改配置"（FR-013/014、C10）对齐；② 把配置加载器计数的**写死字面量断言改为派生断言**（当前 `:83-85`）——与 `agents/pilot/pilot.py` 的 `config_completeness`（当前 `:101`）实测返回值一致、与预检报告 `loaders` 键一致；**不得**换另一个字面量（计数随实现派生：13 类配置 + 各 Agent 权重 + `pilot` 段加载器，**规格与计划都不写数字**；写死即恒假，属既有缺陷）；③ 模块 docstring 的"六步 / 试水档等值派生 / 短剧 16 镜上限"口径同步更正。（C10；FR-013/014；SC-012；plan 缺口 6）

**检查点**: ✅ **改动就位口径（与"依赖关系"节一致，如实登记）**：两形态 `pilot` 段新键与 `storyboard.render.index_grid` 就位、五处登记点改动就位、`ops/demo_pilot.py` 的缩档改走配置且计数断言改为派生量（`grep -c "_derive_pilot_scale" ops/demo_pilot.py` == 0）。**五处登记点的"全绿"不能在阶段 1 判定**：登记点①②要求 `pilot` 段加载器（T1802）与 `T1809/T1810` 的装配/预检就位，登记点⑤的 `dev` 段要求阶段 2 的 T1812——故本检查点的绿在**阶段 2 结束后复核**。T1805② 的断言也只有在 T1802（加载器）落地后才可能转绿；其"七环节演示口径"部分要到 T1818/T1820 之后才可验证。

## 阶段 2：基础（`dev` 链首插入：清单同步不变量 + 入口与产物 kind + 档位预检 + 策略装载下沉）

**⚠️ 关键**: 此阶段完成前不能开始任何用户故事的**装配级**工作（排练档生效接线、交接接线、证据面）。七处集中声明点（阶段元组 / 阶段表 / `AgentConfigs` / 运行时装配含建表 / 预检四处清单 / 产物 kind 登记 / 两形态声明）必须**同批**落地，漏一处即**多条一致性断言同时红**；`dev` 阶段入口**只调** 017 既有轮次入口，**零新增落树路径**；策略装载**必须**下沉到 `agents/dev/`（常驻静态断言禁止 `agents/` import `ops/`，`tests/unit/test_pilot_stages.py` 当前 `:140-162`），且**原则四例外的三项替代约束**（静态检查在装载时生效 / 策略执行超时 / 策略零环境对象·不触网关）必须在该下沉路径上落机检（E-04；口径镜像 017 契约 C14）。本阶段先落两份边界测试（T1806/T1807，先写、确认失败）再落实现。

- [ ] T1806 [P] `tests/unit/test_pilot_chain_seven.py`（新，先写，确认失败）：**C1 清单同步不变量**——① `tuple(spec.stage_id for spec in build_stage_specs(runtime)) == PILOT_STAGE_IDS`（恰为 `dev→script→storyboard→visual→sound→editing→promo`）；② `STAGE_CONFIG_SECTION` 的键域 == `PILOT_STAGE_IDS` 且值域 == `AgentConfigs` 字段名集、`STAGE_TREE_PREFIX` 不得以 `f"{stage_id}-round-"` 直推（`script` 实为 `screenplay-round-`）；③ 阶段表 `output_kind` 的全部取值 ∈ `package._KIND_CONTENT_TYPE`；④ 七环节的权重循环名单/加载器名单/`budget.tiers` 档位声明齐备（含 `dev` 档，缺档即红）；⑤ **模拟漏同步**（从阶段表删 `dev`）⇒ 一致性断言红（不是"少一环也能跑"）；⑥ `dev` 拒绝路径（`run_dev_round` 返回 `artifact_hash is None`）⇒ 阶段 `failed`、其后环节全 `skipped`、下游零调用。**四条常驻静态断言不放松**：编排层不新增落树路径、零形态分支（`form ==` 类）、`agents/` 不 import `ops/`（AST + 文本）、`core/` 零形态字面量。（C1/C2/C4；FR-001/002；SC-002/009）

- [ ] T1807 [P] `tests/unit/test_dev_policy_loader.py`（新，先写，确认失败）：**C2 策略装载下沉 + 原则四例外的三项替代约束（E-04，逐项一例）**——① **静态检查前置**：未过 `policies/static_check.check_policy_source` ⇒ 拒绝装载，**0 网关 0 落树**、不入策略历史；② **执行超时**：注入**死循环策略** ⇒ 超时判失败（`PolicyLoadError` 语义，镜像 `core/degraded/compare.py` 的 `CompareError` 口径）而**不挂死**；③ **策略零环境对象 / 不触网关**：断言策略只被喂 `plan(inputs, config)`——**不交付** `observed()`/`probe()`/对象存储/账本/网关句柄（真值探测由宿主代执行），且该路径**不持有对象存储凭证、不经网关、不触生成**（网关调用由宿主 `run_dev_round` 发出并受 019 门禁约束——本断言针对**策略侧**，不是"链上不调网关"）；④ 版本取形态配置部署指针 `deployment.dev.current_policy_version`（`configs/movie.yaml` 当前 `:625`、`configs/shortdrama.yaml` 当前 `:624`），**缺指针/源码不存在 ⇒ 启动前拒绝**，不回落"最新/第一条"策略；⑤ `ops/dev.py` 的 `_load_policy`（当前 `:81-123`）与 `agents/dev/policy_loader.py` 为**同一实现**（`ops/` 侧为薄调用，不得留第二份）；⑥ `agents/` 侧零 `import ops`（AST + 文本），既有 `agents/dev/policy_versions.py:68 load_policy_source` 的源码文本通道复用不改协议。（C2；FR-002；SC-009；E-04）

- [ ] T1808 `agents/dev/policy_loader.py`（新）+ `ops/dev.py` 的 `_load_policy`（当前 `:81-123`）收敛：把装载三段（静态检查前置 → 版本核验 → 实例化）下沉为 `agents/dev/` 侧**单一实现**（失败 ⇒ 阶段 `failed` 且原因点名，不静默取"最新/第一条"）；**并落实例外条款的另外两项**——**策略执行超时上限**（静态检查不禁循环）与**零环境对象守护**（只传 `plan(inputs, config)`，不交付任何句柄）；`ops/dev.py:138` 的调用点改为薄调用（`--policy` 语义与退出码不变）。**判断项（须实现时留痕）**：装载函数的对外形状（入参 = 策略源码文本 + 版本 + 实例化上下文 + 超时上限）由本任务定，`agents/pilot/stages.py` 的 `_dev_entry`（T1809）只调用它。（C2；FR-002；SC-009；E-04）

- [ ] T1809 `agents/pilot/stages.py` + `agents/pilot/scale.py`（承接 T1806 的断言，同文件故不并行）：① `PILOT_STAGE_IDS`（当前 `:94`）链首插入 `dev`；② `build_stage_specs`（当前 `:1018-1069`）首项 `dev`（`depends_on=()`、`entrypoint=_dev_entry`、`handoff=_handoff_dev`、`output_kind="slate"`）且 `script.depends_on` 由 `()` 改为 `("dev",)`——`_handoff_dev`/`_handoff_script` 的**声明形状与拒绝逻辑**归 US2 的 T1824，本任务只落阶段表引用与最小可运行形态（`export_slate` 导出面的**反解引用**亦在 T1824）；③ `AgentConfigs`（当前 `:102-111`）加 `dev: DevConfig`；④ `build_runtime`（当前 `:138-198`）装配含建表段（当前 `:173-180` 新增 `agents/dev/db.py:60 create_jobs_schema`）；⑤ `_dev_entry` **只调** `agents/dev/loop.py:490 run_dev_round`（零新增落树路径；`artifact_hash` 为空 ⇒ 判**阶段失败**，不带着空工件往下走；`reason` 点名）+ 产出 `ProductRef(kind="slate", content_hash=slate_hash())` 与 `detail` 键集（`artifact_hash`/`policy_version`/`entry_count`/`production_marks`/`cost_reconciliation`/`spent_usd`）；⑥ 新增 `STAGE_CONFIG_SECTION` 与 `STAGE_TREE_PREFIX` **单一映射声明**（stage_id → 配置段名 / 轮次树前缀）；⑦ **新增 `agents/pilot/scale.py`（新；叶子模块，不 import `agents/storyboard/*`）**：`derived_shot_count = max(scene_count, ceil(target_duration_s / clip_duration_seconds))` **唯一持有者**（C8 的容量下界、C10 的档位一致性机检都只读它——**不得**在两处各写一遍公式，F-04）；`build_shot_plan`（当前 `:230-248`）与 `build_screenplay_plan`（当前 `:902-960`）改**经参数注入**取 `scene_count`/`lines_per_scene`（**唯一解析者是 `PilotConfig`**，C-02；`agents/*/config.py` 不得读 `pilot` 段）——`_DEFAULT_SCENE_COUNT`（当前 `:97`）、`range(4)`（当前 `:918`）、`range(12)`（当前 `:930`）**退役**。（C1/C2/C8/C10；FR-001/002/013/014/015；SC-002/011）

- [ ] T1810 `agents/pilot/pilot.py`（承接 T1802，同文件故不并行）：① 预检**四处清单**加 `dev`（加载器元组当前 `:117-131`、权重循环当前 `:135-140`、`AgentConfigs` 构造当前 `:198-205`、预检预算循环当前 `:208-224`）；② `PilotInputs`（当前 `:40-58`）扩展 `genre_bounds` / `audience`（`run_dev_round` 的 `_validate_inputs` 要求，`agents/dev/loop.py:176-191`），**缺项即预检拒绝**，`to_dict()`/`fingerprint()` 同步 ⇒ **输入指纹口径变更如实留痕**（plan 缺口 2）；且 `target_duration_min` 改为**浮点分钟**（C-01：0.5 = 30 秒，指纹格式化须确定性）；`dev` 输入必须由**显式声明的运行级映射**产生（逐字段可追溯，**禁止未声明直通**）；③ `dev` 的预算走**LLM 腿档位**分支（无 `exploration_per_round_usd`，`agents/dev/config.py:150-164`）——登记 `budget.tiers.dev` 的**声明值**，**不得**新增 `0.0` 占位、也**不得**为过预检发明单轮预算；`budget.tiers.dev` 两形态缺档 ⇒ **拒绝启动**（019 口径不放宽）；④ **新增前置判定**：全部 `.chat(` 调用点声明的 `stage=` 取值必须 ∈ **两形态** `budget.tiers` 键集（缺档即拒绝启动，**不发明 agent↔环节映射**；`sound` 环节无 LLM 调用如实声明，`agents/pilot/stages.py` 当前 `:594` 一线，按结构名定位），并复核 `tests/unit/test_no_vendor_literals.py:86-138` 的调用点断言（**计数仍为 8**，`agents/dev/loop.py:632` 的 `stage="dev"` 已在位）；⑤ **新增两处时长一致性预检（SC-012①）**：`screenplay.target_duration_min × 60` 必须等于生效 `editing.target_duration_s`（容差 `1e-6`；取**排练档覆盖后**的生效值），**运行级 `target_duration_min × 60` 亦必须等于生效成片时长**——任一不一致 ⇒ **拒绝启动并点名两处实测值**（不静默择一、不按其一取值）。（C1/C3/C10；FR-007/013/014；SC-006/012；plan 缺口 2/5/9；C-01）

- [ ] T1811 [P] `agents/pilot/package.py:173-181` 的 `_KIND_CONTENT_TYPE` 新增 `"slate": "json"`（`TopicSlate` canonical JSON），与测试侧对照表**双向一致**：`tests/unit/test_pilot_product_labels.py:36-44` 的 `_EXPECTED_CONTENT` 同步新增 `"slate": "json"`、`:127` 的"六阶段全部有产物"计数改七环节；未登记即 `check_product_kinds`（`:197-217`）拒绝装配（既有行为，不放宽）。同处 `build_manifest`（`:220`）/`build_state`（`:272`）的"六阶段"文案同步更正（另含 `agents/pilot/__init__.py` 的包注释"六阶段 StageSpec 定义"）。（C4；FR-003；SC-007）

- [ ] T1812 [P] 既有测试与夹具字面量同步（**不得为过测试而放宽断言**）：`tests/unit/test_pilot_stages.py` 的 `Test阶段定义`（阶段顺序与配置装配，当前 `:37-56`）、`tests/contract/test_pilot_contracts.py:368-375`（`completed_stages` 六元组 → 七元组；与 T1803 同文件，跨阶段串行）、`tests/unit/test_pilot_package.py:59-66` 与 `:72-79`（清单/产物阶段列表）、`tests/unit/test_pilot_run.py:90-97`（同）、`tests/conftest.py:3159-3163` 的 `dev` 段与书面备忘（承接 T1804，同文件串行）。**只需复核、不需改**：`tests/unit/test_form_switch.py:151-159` 的权重差异循环——**017 起已含 `dev`**（F-09），本次插链不改变它。（C1；FR-001；SC-002；plan 缺口 6）

**检查点**: ✅ `pytest tests/unit -k "pilot or dev"` 与 `pytest tests/contract -k pilot` 在阶段 2 范围内绿；拓扑序恰为七元组、删 `dev` 即红；`dev` 档缺声明 ⇒ 启动拒绝；两处时长不一致 ⇒ 启动拒绝并点名；`agents/` 无 `import ops`；调用点计数仍 8。

## 阶段 3：基础（索引网格容量 + 容量校验 + 原则一版本规则）

**⚠️ 关键**: 网格参数**确实**进入评估器实现哈希（`frame_function_hash()` = `agents/storyboard/board_render.py` 的文件字节摘要，当前 `:233-239`，被 `agents/storyboard/evaluators/alignment.py` 的 `EvaluatorSpec.version` 构造拼进 `proxy.emotion_alignment` 版本，当前 `:88-97`）——故"升版"在本仓是**必定义务而非条件句**；且**仅改配置网格取值（如 `cols: 4 → 8`）也必须升版本**，否则同 `evaluator_id@version` 的行为会随配置漂移（直接违反原则一）。既有已落盘工件为**内容寻址**、节点为一次性 INSERT ⇒ **禁止**任何改写历史节点/工件的路径（无迁移、无回填）。索引网格落 `storyboard.render` 段内 ⇒ **无新增登记点**（该段已在形态差异集内）；但平台侧真实渲染请求**全量透传** `cfg.render`（`agents/storyboard/platform/http_real.py:105-122`），本地 stub 契约须同步（plan 缺口 8）。

- [ ] T1813 [P] `tests/unit/test_storyboard_index_scale.py`（新，先写，确认失败；C8/C9）：① **容量下界 / 量子上界 / 缺项**三拒绝（`2**(R·C) < 该形态派生镜头数` ⇒ 拒绝并给出实测数字；`2**C > render.width` ⇒ 拒绝；越出 `1 <= R <= height` ⇒ 拒绝；`render` 缺 `index_grid` ⇒ 拒绝，**不静默回落 4×2**）；② **全量编解码往返**（`decode_index_code(render_shot_card(index=i)) == i` 对 `i < 该形态派生镜头数` 逐序号成立——movie 原值 **2700 镜** 100% 可编码，SC-010）；③ **跨网格帧序列字节不同**（不得用 4 列解码器解 8 列编码）；④ **版本规则**：同一实现下两种网格取值 ⇒ **两个不同版本号**（**强定义务、无"若"字**）；冻结**旧版本字面量**并断言新版本 ≠ 旧版本（镜像 `tests/contract/test_dev_contracts.py:345` 的 `BOOTSTRAP_VERSION` 冻结法）；⑤ 旧 `evaluator_id@version` 的既有节点得分与已落盘工件无写路径（静态断言 + 无迁移脚本）；⑥ **派生镜头数只读 `agents/pilot/scale.py`**：把 `clip_spec.duration_seconds` 调大 ⇒ 派生数与容量下界随之变化，而**容量校验本身不放宽**。（C8/C9；FR-015；SC-010）

- [ ] T1814 [P] 同步会因此变红的既有断言（**改法是把"魔数 16"换成机制，不是放宽**）：`tests/unit/test_pilot_stages.py` 的 `test_镜头计划在分镜渲染器上限内`（当前 `:58-73`）的 `1 <= shots <= 16` / `shots == 16` → `2**(R·C) >= shots` 与 `2**C <= render.width`（对 movie 的 2700 镜而言**严于**旧式；该文件与 T1812 同文件，跨阶段串行）；`tests/unit/test_storyboard_board_render.py` 的 `render_cfg` 夹具（当前 `:45-56`，补 `index_grid`）与 `:151`（`decode_index_code(frame) == index` 往返用例补网格参数 + 新增 **2700 镜**逐序号往返）；`tests/unit/test_storyboard_config.py:181`（缺项样例补键）。（C8；FR-015；SC-010；plan 缺口 6）

- [ ] T1815 `agents/storyboard/board_render.py`（承接 T1813，同文件故不并行）：模块常量 `INDEX_BITS = 4` 与 `_INDEX_ROWS = 2`（当前 `:39-40`）**退役**，编码（当前 `:137-141`）/解码（当前 `:144-155`）/绘制（当前 `:203-208`）三个函数改为**按 R×C 块网格参数化**且**同取** `render_cfg["index_grid"]`（读序固定、不得留静默回落值）；块宽口径 `block_width = max(1, width // 2**C)`；`_require_render_cfg`（当前 `:71-77`）补网格缺项校验；`render_shot_card(..., index=…)`（当前 `:211-229`/`:259`）签名不变，网格经 `render_cfg` 传入。（C8；FR-015；SC-010）

- [ ] T1816 `agents/storyboard/config.py`（承接 T1815，同文件不重叠故可并行但**结论**须一致）：`_require_render`（当前 `:163-186`）对 `storyboard.render.index_grid` **必填** + **容量下界** `2**(R·C) >= 该形态派生镜头数`（**读 `agents/pilot/scale.py` 的同一函数**——`agents/pilot/scale.py` 是派生镜头数的**单一无环持有者**，本模块**不**自行算第二遍（F-04）、不引入 `max_shots` 之类冗余键）+ **量子上界** `2**C <= render.width` 与 `1 <= R <= height`（块宽在块数超像素数时退化），越界即**拒绝启动并给出实测数字**（沿用 `agents/pilot/handoffs.py:244 _clip_spec` 的缺项拒绝口径）；`render` 段随树 `config_snapshot` 冻结（`agents/storyboard/loop.py` 当前 `:269`）故改网格即新快照指纹。（C8；FR-015；SC-010）

- [ ] T1817 `agents/storyboard/evaluators/alignment.py`（承接 T1813）：**生效网格参数进入版本材料**（作为 `implementation_version(*parts)` 的附加部件），使"只改配置网格取值"也必然升版本；受影响面**实测只有一处**（`proxy.emotion_alignment`，`storyboard_cards` 的唯一消费者），`rule.*` 与 `judge.script_fit` 不读帧 ⇒ 版本不变，**如实登记、不得顺手全量升版本**；版本号正则形状不变（`tests/unit/test_storyboard_alignment.py:241` 的成员式校验仍成立且**不得**改为不校验）。同时复核平台载荷：`agents/storyboard/platform/http_real.py:105-122` 全量透传 `cfg.render` ⇒ `index_grid` 入 `render` 会改变该载荷，B 路径 `not_delivered` ⇒ **无在线影响**（如实登记），本地 stub 契约用例 `tests/integration/test_http_real_stub.py` 随 `render_params` 口径同步（**不新增档案配置键、不重写适配器协议**）。（C8/C9；FR-015；SC-010；plan 缺口 8）

**检查点**: ✅ 容量下界/量子上界/缺项三拒绝绿；movie 原值 2700 镜逐序号往返绿；两种网格取值 ⇒ 两个版本号且旧版本字面量冻结断言绿；旧节点/旧工件无写路径；`render` 缺 `index_grid` ⇒ 拒绝启动；派生镜头数只在 `agents/pilot/scale.py` 出现一份（源码扫描无第二处公式）。

## 阶段 4：用户故事 1 - 七环节全链路跑通与最小可行长片（优先级：P1）🎯 MVP

**目标**: 七环节按同一 DAG 串链跑通（链路与环节不变）、体量按形态配置声明的**排练档**缩档、产出可复现样片包五件套、任一环失败不产半包；缺项即拒绝启动。

**独立测试**: 全模拟后端 + 固定时钟跑一轮，断言运行记录七环节全 `done`、五件套齐备且过 `verify_package`、同输入同配置独立工件根重跑逐字节一致；把 `dev` 从阶段表移除（模拟漏同步）即红；`status: unstandardized` 时形态原值在 force 且标注"未标定"。

### 用户故事 1 的测试（先写，确认失败后再实现）

- [ ] T1818 [P] [US1] `tests/unit/test_pilot_rehearsal.py`（新，先写，确认失败；C10）：① **单点解析**——`status=declared` ⇒ 生效体量 = `scale` 覆盖值（`editing.target_duration_s` / `screenplay.target_duration_min` / `screenplay.page_tolerance` / `visual.clip_spec.duration_seconds`），链路拓扑/交接契约/门禁/评估器组合**一行不动**；② `status=unstandardized` ⇒ **不覆盖**（形态原值在 force）且如实标注"未标定"，**无任何发明数字**；③ 缺 `status` / 缺 `scale` 某键 / 缺 `work_kind` ⇒ **拒绝启动**；④ `work_kind=real_work` ⇒ 不缩档，且包内标记可机检地**不为** rehearsal（排练产物不得被标为真实作品）；⑤ **内部一致性机检**（`target_duration_s == script_target_minutes × 60`（容差 `1e-6`）；页数区间 == 分钟 ± 容差；派生镜头数满足**容量下界** `2**(R·C) >= derived_shot_count`（读 `agents/pilot/scale.py`）；`页数 == pilot.scene_count × pilot.lines_per_scene ÷ lines_per_page ∈ 页数区间`，口径 `agents/screenplay/evaluators/page_minutes.py:55-66`）；⑥ **两处时长不一致即拒绝启动**（`target_duration_min: 90` 配 `target_duration_s: 120` ⇒ 错误信息含 `5400 s` / `120 s` 两处实测值；`script_target_minutes: 0.5` 配 `target_duration_s: 30` ⇒ 通过；SC-012①）；⑦ **粒度口径**（30 秒演示档可声明并可经浮点分钟运行级输入对齐）；⑧ **零形态分支**（无"试水档/长片档/排练档"的分支判断）。（C10；FR-013/014；SC-011/012；C-01）

- [ ] T1819 [P] [US1] 扩展 `tests/unit/test_pilot_chain_seven.py`（承接 T1806，同文件跨阶段串行）：**七环节端到端跑通**（全模拟后端 + 固定时钟）——运行记录七环节全 `done`、样片包五件套齐备且过 `verify_package`（`agents/pilot/package.py:105`）、**同输入同配置独立工件根两次运行逐字节一致**、任一环失败 ⇒ 整轮失败 + 其后 `skipped` + **不装配样片包**（零半包）、断点续跑已完成环节**零重跑** + 输入/配置指纹不一致即拒绝、`dev` 产物 `slate` 可寻址且 `detail.policy_version` == 部署指针、七环节 `cost.json` 的 `by_stage` 键集覆盖 `PILOT_STAGE_IDS`。**如实登记（F-08）**：其中"**含本轮新增字段**（`source`/`channels`/`work_kind`/`eval_breakdown`/`volume`）的逐字节一致"依赖阶段 6 的产物面（T1829），故该子断言要**在阶段 6 之后才全绿**；阶段 4 只要求五件套本体逐字节一致。（C1/C2/C4/C11；FR-001/002/003/012；SC-001/007/008）

### 用户故事 1 的实现

- [ ] T1820 [US1] `agents/pilot/pilot.py` 的排练档生效解析接线（承接 T1810，同文件故不并行）：**单点解析**生效体量（一处解析、全链消费）——`status=declared` 时用 `pilot.rehearsal.scale` 覆盖对应体量键，`unstandardized` 时不覆盖并标注；把生效体量快照与 `work_kind` 写入运行记录/包面的**确定性段**（与 T1829 的 `manifest.work_kind` 同一取值来源）；预检报告新增生效来源/档位标注（`pilot_backend` 段旁）。**`code` 侧不得出现档位分支判断**。（C10；FR-013/014；SC-011）

- [ ] T1821 [US1] `ops/pilot.py` + `ops/demo_pilot.py` 收官：① `ops/pilot.py` 的 `run`/`resume`（当前 `:189-190`）与 `precheck` 新增 `--genre-bounds` / `--audience`（`dev` 输入的显式映射，缺项即拒绝启动）；**`--minutes` 改为接受浮点分钟**（与 `PilotInputs.target_duration_min` 浮点化、预检"运行级 ×60 == 生效成片时长"**同批定稿**，C-01/F-13；用法示例取**生效档值**）；`--form movie` 的七环节用法说明（当前 `:7-13` 的模块 docstring 同步）；② `ops/demo_pilot.py`（承接 T1805，同文件故不并行）的端到端演示扩到**七环节**：步骤 1 配置完整性（含 `pilot` 段/容量下界）、步骤 2 七环节串链出包、步骤 3 可复现对照、步骤 4 movie 对照（同一套链代码换配置）、步骤 5 断点续跑、步骤 6 拒绝语义（缺 `pilot.scene_count` / 缺 `budget.tiers.dev` / 两处时长不一致 / 任一环失败不产半包）、步骤 7 排练档与未标定标注；退出码 0 = 全步 ok，**零真实花费、零外部网络**。（C2/C10；FR-002/013/014；SC-001/012）

**检查点**: ✅ `pytest tests/unit -k pilot` 绿（含排练档与七环节端到端；新增字段子集的一致性断言按 F-08 登记，阶段 6 后复核）；`uv run python ops/demo_pilot.py` 退出码 0 且各步 ok；同输入重跑（五件套）逐字节一致率 100%；缺项类各一例拒绝启动（含两处时长不一致）。

## 阶段 5：用户故事 2 - 选题产出 → 剧本输入的跨环节交接契约（优先级：P2）

**目标**: 组合内"本轮进入生产"标记**恰好一条**且指向组内条目 ⇒ 按**字段级交接声明**交给剧本环节（题材/约束/角色逐字段可追溯）；悬空/越界/多条/要点缺失 ⇒ **下游拒绝启动**并点名；上游导出面 schema 变更必须**两侧同步**。

**独立测试**: 用夹具立项组合工件（悬空标记、越界标记、多条标记、要点缺失、字段增删五种注入）跑交接：守恒断言逐项通过；丢字段未声明即报错；标记非唯一/悬空时下游拒绝启动且原因点名；无 `dev` 阶段的链落 `run_level_pilot_inputs` 且生效来源随机读可见。

### 用户故事 2 的测试（先写，确认失败后再实现）

- [ ] T1822 [P] [US2] `tests/unit/test_pilot_dev_script_handoff.py`（新，先写，确认失败；C5~C7）：① **声明形状**——`DevScriptHandoff(mode, reads, renames, dropped, derived, sources)` 与守恒等式 `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`（**"上游" = 可移交要点集合**：`genre`/`constraints`/`characters`，**不是**整张导出面，B-01）；`set(reads) == SCRIPT_INPUT_READS`（`reads` **三类**：承接/运行级/派生，**合计且每键恰一类**），出现未声明读取键 ⇒ 交接拒绝并点名该键；**`dropped` 是与 `reads` 并列的独立集**（不是第四类）；**取数依据 `sources == {"entries","production_marks"}`** 单列登记、既不进 `reads` 也不进 `dropped`；② **读取集源码扫描式锁定**——两式 `inputs[` / `inputs.get(` 扫描 + 下游 loop 输入归一口径（`agents/screenplay/loop.py:186-204`）与调用侧读取点（`agents/pilot/stages.py:904-905`）**双向绑定**，新增读取点未登记即红；③ **改名承接**——`topic ← genre` 登记在 `renames`（**`genre` 不进丢弃集**；承接 ↔ 丢弃互斥），`constraints`/`characters` 为恒等承接，`target_duration_min` 标**运行级**；漏登承接类键（或写成"丢 `genre` + 派生 `topic`"）⇒ 红；④ **取数入口**——`production_marks` **恰好一条**且指向组内条目，悬空/越界/多条/缺失四类各一例 ⇒ **下游拒绝启动**并点名（不静默取第一条、不伪装成"选题为空"）；被标记条目的三个可移交要点任一为空 ⇒ 拒绝并点名（**承接不解除要点校验**，第二道防线与 `rule.slate_structure` 同源）；⑤ **两条来源**——有 `dev` ⇒ `mode=dev_script_handoff`；无 `dev`（既有短剧链）⇒ `mode=run_level_pilot_inputs` 四键全运行级；生效 mode 随机读可见（`StageState.detail` + 预检报告），**禁止静默择一**；⑥ **两侧同步**——上游导出面增删字段而未同步声明 ⇒ 断言红（`SCHEMA_VERSION` + 017 侧快照 + 本侧声明三处缺一即红），改名承接**不**触发导出面变更；⑦ `dev` 失败时下游零调用、如实 `skipped`（不以上一轮残留产物顶替）；⑧ **两层断言各自成立（F-02）**——同名字段子集层 `FieldParity.consistent()` 三式绿 **∧** 承接/运行级层 C6 断言 ①~⑥ 绿（`renames` 层**不**复用 `consistent()`）。（C5/C6/C7；FR-004/005/016；SC-003/011；B-01/F-02/F-11）

### 用户故事 2 的实现

- [ ] T1823 [US2] `agents/pilot/handoffs.py` + `agents/screenplay/loop.py`：① 在手写声明层扩出**运行级**类——`DevScriptHandoff(mode, reads, renames, dropped, derived, sources)`（`reads` 三类：承接含改名 / 运行级 / 派生 + 逐字段来源 + 双模式化 + `dropped` 独立集 + 取数依据登记），**同名字段子集层**继续用 `FieldParity.consistent()`（`agents/pilot/handoffs.py` 当前 `:50-56`，三式**原样保留**），而**承接/运行级层另立断言 ①~⑥（不复用 `consistent()`）**——两条要求**同时可满足**（F-02）：代码侧 = `consistent()` 只对同名键集求值，`renames`/运行级键另走 ①~⑥；② `SCRIPT_INPUT_READS = {"topic", "target_duration_min", "constraints", "characters"}` 常量落 `agents/screenplay/loop.py`（与 `_validate_inputs`（当前 `:186-204`）**同源导出**，不得复制一份字面量）；③ 导出面与调用侧零重复声明：**禁止未声明透传**（下游 loop 的输入视图是权威键集，新增输入字段即断言红）。（C5/C6；FR-004/016；SC-003；F-02）

- [ ] T1824 [US2] `agents/pilot/stages.py`（承接 T1809）+ `agents/pilot/pilot.py`（承接 T1820，两文件均跨阶段串行）：① `_handoff_dev` / `_handoff_script` 接线——`_script_entry`（当前 `:398-441`）按 `mode` 取数（有 `dev` 取交接结果、无 `dev` 回落 `pilot_inputs`，当前 `:403`）并将生效 `mode` 落 `StageState.detail` + 预检报告；② 取数入口取**恰好一条**"进入生产"标记，四类违规 ⇒ `HandoffError` 语义的**下游拒绝启动**（017 导出面**不因缺陷丢条目**，故拒绝必须发生在交接侧）；③ 上游字段集的**反解引用**（import `agents/dev/export_slate.py:25-34` 的 `EXPORT_FIELDS`/`EXPORT_ENTRY_FIELDS`，**不得复制**）；④ 派生键不得占用上游/运行级/承接目标的键名（`派生 ∩ (上游 ∪ 运行级 ∪ 承接映射值集) == ∅`）；⑤ **取数依据与丢弃分开登记**（`entries`/`production_marks` 只作选条入口，不进 `reads`/`dropped`）。**判断项（须实现时留痕）**：`topic` 的来源按 research 决策 3 的**已裁决**读法落地——**承接 `topic ← genre`（改名承接，登记在 `renames`）**，`genre` 不进丢弃集。（C5/C6/C7；FR-004/005/016；SC-003/011；B-01）

**检查点**: ✅ 守恒等式（`下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`）与"每个读取输入均已声明"绿；`genre` 写进丢弃集即红；承接类键漏登 `renames` 即红；取数依据混进 `reads`/`dropped` 即红；两层断言（同名字段子集 `consistent()` ∧ 承接层 ①~⑥）同时绿；悬空/越界/多条/要点缺失四类拒绝启动且原因点名；无 `dev` 链落 `run_level_pilot_inputs` 且 mode 可见。

## 阶段 6：用户故事 3 - 长片体量的性能与预算门禁 + 样片包成本与评估分量（优先级：P3）

**目标**: 全链路成本三方一致（运行记录 ⨯ 落盘账目 ⨯ 网关记账，含 `FAILED`）；各环`evaluator_id@version` 分量逐环入包（缺项即拒绝装配）；每环真实/模拟标注入包且可机读；性能画像与形态配置声明的阈值对照判定"长片体量达标"（固定时钟运行**不得**产出达标结论）。

**独立测试**: 夹具运行记录与账目注入三类偏差 ⇒ 对账逐项报错；评估分量缺项即装配失败；画像对"固定时钟运行"拒绝给出达标结论；阈值未标定时输出"未标定"而非数字。

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T1825 [P] [US3] `tests/unit/test_pilot_evidence_pack.py`（新，先写，确认失败；C11/C12）：① `state.json` 逐环节**非空** `eval_breakdown`（键 = `evaluator_id@version`，形状与口径**取自树节点**原文——不改写、不归一化、不重算；派生产物（如分镜 `shotlist`）显式标 `derived: true` + "无节点分量"，**不得**与缺项混同）；抹掉任一环节的分量面 ⇒ **拒绝装配**（空对象 / "不适用" / 缺环节一律 `PackageError`）；分量面**不得**含 `created_at` 类墙钟字段；② `manifest.json` 的 `stages[].source`（`real|simulated`）+ `channel` + 顶层 `channels` 汇总（`llm` 的来源/`adapter_ref`/`profile_id` + 平台侧逐环节来源）+ `work_kind`；预检报告同步 `stages: {stage_id: source}`；③ **诚实约束**——全模拟 ⇒ 七项全 `simulated` 且包内**无**"真实渠道全链路已跑通"表述、`SIMULATED_NOTE` 在位；把某项改标 `real` ⇒ 断言红（模拟被标为真实恒 0）；`llm_backend: http` 而平台仍模拟 ⇒ `llm` 为 `real`、平台五环节为 `simulated`（分层可机读）；019 保留值 `fallback` 出现即**原样标注**、不得折叠进 `real`/`simulated`；④ **交叉核对**——包内 `llm.source` 与 019 运行记录（`billing/{channel}/runs/{date}.json` 的 `entries[].source`）一致；⑤ 缺项即拒绝装配（`agents/pilot/package.py:79-95 _validate_pieces` 的拒绝语义不放宽）。（C11/C12；FR-003/008/009/011；SC-004/007）

- [ ] T1826 [P] [US3] `tests/unit/test_pilot_performance.py`（新，先写，确认失败；C13）：① 画像落 `pilot/profiles/{run_id}.json`，字段 = 各环节 `started_at`/`finished_at`（`core/orchestration/models.py:242-243`）+ 体量指标（镜头数/片段数/成片时长/页数，全部取自既有明细 `agents/pilot/stages.py` 当前 `:496`/`:577`、reel、`agents/screenplay/artifact.py:434-437`）+ `pilot.performance` 阈值快照 + `verdict` + 时钟口径；`verdict ∈ {meets, below, not_evaluable}`；**成片时长取排练档覆盖后的生效值，与 `screenplay.target_duration_min × 60` 一致（容差 `1e-6`；SC-012①）**；② **固定时钟 ⇒ 恒 `not_evaluable`** + "耗时为确定性常量、不构成性能证据"标注（**不产出达标结论**）；③ **退化交叉核验**——各环节时间戳全等 ⇒ 即使声明 `clock system` 也强制 `not_evaluable`（声明撒谎被证据推翻）；④ `status: unstandardized` ⇒ 只出台账与体量 + "未标定"，**无数字、无达标结论**（`code`/配置零默认）；⑤ **画像不入包**——五件套逐字节面不含墙钟（`PACKAGE_FILES`/`verify_package` 不改）；⑥ **新增字段的墙钟隔离机检**——同输入同配置下一次固定时钟、一次系统时钟两次运行，`source`/`channels`/`work_kind`/`eval_breakdown`/`volume` 子集**逐字节一致**（字段级禁令：无 `created_at`/`now()`/绝对路径/进程内顺序）。（C13；FR-010/012；SC-005/008）

- [ ] T1827 [P] [US3] `tests/unit/test_pilot_cost_three_way.py`（新，先写，确认失败；C13）：① `cost.json` 覆盖七环节、键集须覆盖 `PILOT_STAGE_IDS`、逐项差额在 1e-9 容差内、合计等于逐项之和、`reconciled` 为真；② **三方口径**——运行记录 `StageState.cost_usd` ⨯ 各 Agent 落盘账目（`state.detail["spent_usd"]`）⨯ **网关记账增量**（环节边界只读采样 `LLMGateway.total_cost_usd`，`core/llm_gateway/gateway.py` 当前 `:167`）；③ **可比性分级**——LLM 腿专属环节（`dev`/`script`）逐环节**必须相等**；混合腿环节（如 `promo`）只断言"阶段成本 ≥ 该环节 LLM 记账增量"；账本腿（`billing/{channel}/ledger.json`）仅当 `window.kind == run` 且窗口实例 == 本次 `run_id` 时逐项比对，`day`/`period` 窗口如实备注"窗口累计、不作逐项比对"（**不静默比对、也不静默跳过**）；④ **`FAILED` 照常入账**（原则二），被预算门禁**拒绝**的那一笔**零入账**且与已发生花费在账目上可分辨（019 零成本分支口径）；⑤ `core/billing/runlog.py` 的 `RUN_ENTRY_FIELDS`（当前 `:32-42`）entry 字段集**增删即红**（链式摘要输入，第三腿不得落 entry）。（C13；FR-006；SC-001）

- [ ] T1828 [P] [US3] `tests/contract/test_pilot_film_contracts.py`（新，**C1~C13 聚合断言**）：C1 清单同步不变量与静态断言常驻 + **`LLMGateway(` 构造点计数仍 13 处（复述 019 普查，E-03）**；C2 `dev` 入口只用既有轮次入口（零新增落树路径）+ 装载即静态检查 + **执行超时与零环境对象守护（例外三项）**；C3 `dev` 档位两形态声明 + 缺档拒绝启动 + 调用点计数仍 8；C4 kind 登记与内容类型双向一致；C5~C7 交接守恒、读取集锁定、`renames` 覆盖、取数依据登记、拒绝语义、两侧同步；C8/C9 网格容量下界/量子上界 + 往返 + 升版 + 旧工件逐字节不变；C10 排练档与场景数/每行数配置化（唯一解析者 `PilotConfig`）、两处时长一致（SC-012①）、缺项拒绝、零形态分支；C11 分量入包与缺项拒绝；C12 标注取值域与"模拟被标为真实恒 0"；C13 画像 `verdict` 与三方口径。**本特性的对抗/篡改面在此聚合举证**（不是"沿用既有对抗面"）：改写包内标注或分量后再校验、把 `simulated` 改标 `real`、把墙钟塞进五件套、删掉角色键让"缺项放行"——各 100% 被拒或报警；**零形态分支全量扫描**（`core/`+`agents/`）常驻。（C1~C13；SC-002/004/005/007/008/009；E-03）

### 用户故事 3 的实现

- [ ] T1829 [US3] `agents/pilot/package.py`（承接 T1811，同文件故不并行）：① `build_state`（`:272-292`）新增逐环节 `eval_breakdown`（**取自树节点** `runtime.store.nodes_of(tree_id)`，`tree_id` = `STAGE_TREE_PREFIX[stage_id] + "-round-" + f"{run_id}-{stage_id}"`，**不得**以 stage_id 直推树前缀）+ `volume`；② `build_manifest`（`:220`）新增 `stages[].source`/`channel`/顶层 `channels`/`work_kind`（取值只来自装配面声明，缺失即 `BackendAssemblyError`，包内不得出现空标注）；③ `_validate_pieces`（`:79-95`）新增"七环节分量非空"与"标注齐备"校验（缺项即 `PackageError`）；④ `cost.json` 的**网关第三腿确定性段**（逐环节增量；账本窗口不可比情形随报告侧登记）；⑤ `PACKAGE_FILES`（`:31`）与 `verify_package`（`:105-138`）**不改**（不新增第六件）。（C11/C12/C13；FR-003/006/008/009/012；SC-004/007/008）

- [ ] T1830 [P] [US3] `agents/pilot/backends.py` + `agents/pilot/pilot.py` 的预检报告（E-01）：① 新增 `STAGE_BACKEND_SLOT`（stage_id → 后端槽位：`dev`/`script` → `llm`，其余五环 → 同名平台槽位；**声明式**且 `键集 == PILOT_STAGE_IDS` 的断言常驻）+ 标注归一化视图（`http` ⇒ `real`；`simulated`/`mock` ⇒ `simulated`；019 保留值 `fallback` 原样标注、**不得折叠进 `real`**）；② **把逐环节标注落进预检报告**——`agents/pilot/pilot.py` 的 `_backend_report`（当前 `:240-252`，在 `precheck` 返回的 `pilot_backend` 段内）新增 `stages: {stage_id: source}`，取值走同一 `STAGE_BACKEND_SLOT`，`credentials_checked: false` 与边界说明保留（该文件与 T1820/T1824 同文件 ⇒ 跨阶段串行）。**不得**按 `resolved` 键名与 stage_id 同名匹配推断（`script`/`dev` 无同名键 ⇒ KeyError 或静默缺标注），**不得**由"是否有凭证"反推。**如实登记**：平台腿**无** 019 账单/账本/运行记录面（019 只实例化 LLM 渠道），其标注只来自装配面声明、**无交叉核对面**；平台腿花费不进厂商账单对账（该腿证据随 B/C 路径接入）。（C12；FR-009/011；SC-004；plan 缺口 12；E-01）

- [ ] T1831 [P] [US3] `agents/pilot/run_report.py`（新）+ `ops/pilot.py` 的 `perf` 子命令（承接 T1821，同文件故不并行）：① 性能画像（各环节墙钟 + 体量指标 + 阈值快照 + `verdict` + 时钟口径**两层判定**：入口显式声明 `clock_mode`（无默认，镜像 `ops/pilot.py --fixed-clock` 语义）+ 时间戳退化交叉核验）落 `pilot/profiles/{run_id}.json`（append-only，**不参与包内逐字节比对**）；② **成本第三方腿的采样包装**——在 `build_stage_specs`（承接 T1824，`agents/pilot/stages.py` 同文件串行）阶段边界**只读采样** `LLMGateway.total_cost_usd`（装饰一处；**不构造 `LLMGateway`**（普查仍 13 处）、不改网关契约、不改 `cost_breakdown`、不进树，E-03）；③ `ops/pilot.py` 新增 `perf` 子命令（`--config`/`--clock system|fixed`，退出码 0 达标 / 1 未达标或不可评价 / 2 用法错误）与 JSON 输出。**登记边界（如实）**：单次运行无法自证用过系统时钟，故接受"声明 + 退化核验"两层，残余风险登记为边界。（C13；FR-006/010；SC-001/005；E-03）

**检查点**: ✅ 分量与标注入包、缺项即拒绝装配；全模拟下七项全 `simulated` 且无"真实渠道全链路"表述；指标篡改 100% 被拒；画像对固定时钟恒 `not_evaluable`、未标定不发明数字；三方对账（含 `FAILED` 与零入账分支）逐项可辨；C1~C13 聚合契约绿。

## 阶段 7：打磨与横切关注点

- [ ] T1832 [P] `README.md` 新增"电影长片全链路编排（功能 018）"章节（七环节链与排练档、`pilot/profiles/{run_id}.json` 画像、`ops/pilot.py` 的 `perf` 用法、诚实边界：真实渠道 = LLM 腿真实 + 平台侧按配置（默认模拟））

- [ ] T1833 [P] `specs/018-feature-film-pipeline/quickstart.md` 验证记录逐条回填（各套件通过数、`ops/pilot.py run/resume/inspect --package/perf` 实跑与退出码（**`--minutes` 取生效档值、浮点分钟**）、`ops/demo_pilot.py` 七步与用时、覆盖率、ruff 双绿、五处登记点同步复核、索引网格与版本复核（movie 原值 2700 镜 100% 可编码、两种网格取值 ⇒ 两个版本号、旧节点逐字节不变）、时长口径复核（`target_duration_min × 60 == target_duration_s` 成立、不一致配置拒绝启动）、交接复核（`reads` 四键逐键标类 + `renames` 覆盖 + 取数依据登记、悬空标记拒绝率 100%）、诚实边界复核（全模拟下七环节全 `simulated`、包内无"真实渠道全链路"表述）；**真实生成/投放凭证与平台名、排练档数字留"待运营"**）

- [ ] T1834 门禁复核（命令与 `.github/workflows/ci.yml` 逐字一致）：`uv run ruff check .` + `uv run ruff format --check .`；`uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`（口径不降，含 web）；`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`；`uv run pytest tests/integration -m integration`（本特性**无 DB 迁移**——0010 之后不动，`dev_jobs` 表为 017 既有）；`uv run pytest tests/adversarial -m adversarial`（既有套件照跑不放松；**本特性的对抗面**——改写包内标注/分量、把 `simulated` 改标 `real`、把墙钟塞进五件套——由 T1825/T1828 举证，**不并入**既有套件）；`uv run pytest tests/unbiasedness -m unbiasedness`（不放松）；`uv run python ops/demo_pilot.py` 退出码 0

- [ ] T1835 [P] `docs/三期立项书.md` §3.1 的 G2 行（`:52`）标注交付状态，并把里程碑行（§4 周 8~11，`:163`）、长片周期风险（§7，`:222`）的**排练档缓解**留痕与 §9 下一步（`:263`）的**结转项**（真实生成/投放厂商对接 → 运营侧输入 / G4；多形态插件 → G5）记入交付说明

## 依赖关系与执行顺序

### 阶段依赖

- **阶段 1（搭建）**: 无依赖，可立即开始；**T1802 与 T1804 须同批落地**（新加载器开启后精简夹具缺键即红——"缺项不得启动"的取舍）
- **跨阶段前置（如实登记，与阶段 1 的检查点同口径）**: 阶段 1 的检查点**不可能在 T1809/T1810 之前变绿**——登记点①②要求 `pilot` 段加载器（T1802）与装配/预检（T1809/T1810）就位，登记点③④在 `pilot` 段取值进入配置后成立，登记点⑤的 `dev` 段与 T1812 同属阶段 2；T1805② 的派生断言也只有在 T1802 之后才可能转绿。故**阶段 1 的完成口径 = "配置段与登记点改动就位"**，其检查点的全绿在**阶段 2 结束后**复核
- **阶段 2（`dev` 链首插入）**: 依赖阶段 1；**阻塞全部用户故事的装配级工作**；T1809（含 `agents/pilot/scale.py`）承接 T1806、T1810 承接 T1802（同文件依次串行）、T1812 承接 T1803/T1804（同文件串行）
- **阶段 3（索引网格与版本）**: 依赖阶段 1（`storyboard.render.index_grid` 两形态已在位）；T1815 承接 T1813；T1816/T1817 承接 T1815/T1813（三个文件，结论须一致；T1816 **只读** T1809 落的 `agents/pilot/scale.py`）
- **阶段 4（US1）**: 依赖阶段 2 与阶段 3（链与网格都在位，端到端才跑得通）；T1820 承接 T1810、T1821 承接 T1805（同文件串行）；T1819 的"新增字段子集逐字节一致"要等阶段 6（F-08）
- **阶段 5（US2）**: 依赖阶段 2（`dev` 已在链首、`export_slate` 有产物）；T1824 承接 T1809 与 T1820（同文件串行）
- **阶段 6（US3）**: 依赖阶段 2（阶段 id 与后端槽位）与阶段 4（包面已产出）；T1829 承接 T1811、T1830 承接 T1820/T1824（`pilot.py` 的预检报告，同文件串行）、T1831 承接 T1821 与 T1824（同文件串行）
- **阶段 7（打磨）**: 依赖全部用户故事完成；T1833/T1834 依赖全部实现任务

### 并行机会

- T1802 / T1803 / T1804 / T1805 文件互不重叠，可并行（其中 T1802 与 T1804 的**结论**须一致）
- T1806 / T1807（两份测试）与 T1808（`agents/dev/policy_loader.py` + `ops/dev.py`）文件互不重叠，可并行；T1809（`stages.py` + `scale.py`）/ T1810（`pilot.py`）/ T1811（`package.py` + 同类测试）分属不同文件，**测试先于实现**；T1812 与 T1803/T1804 同文件，**跨阶段串行**
- T1813 / T1814（两份测试）可并行；T1815 / T1816 / T1817 分属三个文件，可并行但**结论**须一致（容量上下界与版本材料同源）
- US1 的 T1818 / T1819（两份测试）与 T1820 / T1821 中的不同文件可并行；T1819 与 T1806 同文件、T1821 与 T1805 同文件，**跨阶段串行**
- US2 的 T1822（测试）与 T1823（`handoffs.py` + `screenplay/loop.py`）可并行；T1824 与 T1809/T1820 同文件，**跨阶段串行**
- US3 的 T1825 / T1826 / T1827 / T1828（四份测试）文件互不重叠；T1829（`package.py`，承接 T1811）、T1830（`backends.py` + `pilot.py` 的 `_backend_report`，承接 T1820/T1824）、T1831（`run_report.py` + `ops/pilot.py` + `stages.py` 的采样装饰，承接 T1821/T1824）互相独立（文件不重叠），但各自都有**跨阶段串行**承接
- 阶段 7 的 T1832 / T1833 / T1835 文件互不重叠（README / quickstart / 立项书）；T1834 在全部实现任务之后

## 实现策略

### MVP 优先（用户故事 1）

1. 阶段 1 → 阶段 2 → 阶段 3（**不可跳过**：链与索引网格不在位，七环节端到端跑不通）
2. 阶段 4（US1）→ 独立验证：`uv run pytest tests/unit -k pilot` + `-k storyboard` + `uv run python ops/demo_pilot.py`（步骤 1/2/3/6）
3. 此时即交付"七环节跑通 + 缩档只改配置"的价值：排练档机检、两处时长一致、缺项即拒绝启动、样片包逐字节可复现

### 增量交付

1. US1 → 七环节链 + 排练档 + 样片包（MVP）
2. US2 → `dev → script` 字段级交接 + 拒绝语义（017 唯一被显式挂到 G2 的跨特性债务）
3. US3 → 证据面（逐环节分量 / 真实·模拟标注 / 性能画像 / 成本三方）+ `perf` 子命令
4. 阶段 7 → 门禁复核 / quickstart 回填 / README 与立项书状态

---

## 备注

- **原则一落点（评估器版本冻结与索引网格容量）**: T1813/T1814/T1815/T1816/T1817——网格参数经 `frame_function_hash()`（`agents/storyboard/board_render.py` 当前 `:233-239`）进 `proxy.emotion_alignment` 版本（`agents/storyboard/evaluators/alignment.py` 当前 `:88-97`）⇒ **网格参数变更即是新版本**，且**仅改配置网格取值也必须升版本**（生效网格参数进 `implementation_version` 的附加部件；**强定义务、无"若"字**）；旧 `evaluator_id@version` 的既有节点得分与已落盘工件**逐字节不变**、无迁移/无回填/无改写路径；渲染与档位随 `config_snapshot` 冻结（`agents/storyboard/loop.py` 当前 `:269`）；派生镜头数**只有一份公式**（`agents/pilot/scale.py`，F-04）
- **原则二落点（成本入账与节点不可变）**: T1827/T1829——七环节 `cost.json` 逐环节入账（含 `FAILED`）、**被预算门禁拒绝的那一笔不入账**（与已发生花费分开登记，019 零成本分支口径）、`dev` 拒绝路径（`artifact_hash is None`）判阶段失败且零成本；`CostRecord` 与树模型**零改动**；本特性无任何改写历史节点/工件的代码路径；**不新增 `LLMGateway(` 构造点**（普查仍 13 处，T1828 复述，E-03）
- **原则三落点（真实渠道门禁与账单对账串联）**: T1810/T1824/T1827/T1830——真实渠道仍只走 **LLM 腿**（019 前置门禁 + 账单对账 + 运行记录）；调用点清单（8 处 `.chat(`）经由**预检前置判定**（缺档即拒绝启动，SC-006；不发明 agent↔环节映射）；成本三方口径的**第三腿**（网关记账）按环节边界**只读采样**（`run_report` **只读** `total_cost_usd`、不构造网关，E-03），落 `cost.json` 的确定性段与报告侧；平台腿标注只来自装配面声明（**无交叉核对面，如实登记**）
- **原则四落点（沙箱隔离与前缀不可泄露——例外三项替代约束）**: T1807（测试，先写）/T1808（实现）——链首 `dev` 只**装载人工策略并调既有轮次入口**（零新增执行路径），例外的三项替代约束**逐项落机检**：① 静态检查在装载时生效（未过 ⇒ 拒绝、0 网关 0 落树）；② **策略执行超时上限**（注入死循环策略 ⇒ 超时判失败而非挂死）；③ **策略零环境对象 / 不触网关**（只喂 `plan(inputs, config)`，不交付 `observed()`/`probe()`/存储/账本/网关句柄；策略本体不持凭证、不经网关、不触生成——网关调用由宿主 `run_dev_round` 发出并受 019 门禁约束）。落点 `agents/dev/policy_loader.py` + `tests/unit/test_dev_policy_loader.py`，口径镜像 017 契约 C14；**不放宽例外条件、也不借此把开发环节升级为自动进化**（E-04）
- **原则五落点（形态配置化与零形态分支）**: T1801/T1802/T1803/T1804/T1809/T1810/T1818——全部新增参数（`pilot.scene_count`/`lines_per_scene`/`rehearsal`/`performance`、`storyboard.render.index_grid`、来源标注口径、交接声明）**两形态均须声明、缺项即报错、不取码内默认**；体量键**唯一解析者 = `PilotConfig`**（消费侧经参数注入，C-02）；`agents/pilot/scale.py` 为叶子模块（不反向 import `agents/storyboard/*`）；`tests/unit/test_pilot_stages.py` 的静态断言（不新增落树路径 / 零形态分支 / `agents/` 不 import `ops/`，当前 `:127-162`）与 `tests/contract/test_pilot_contracts.py:454-464`（`core/`+`agents/` 全量扫描）常驻不放松
- **原则六落点（诚实边界）**: T1825/T1826/T1827/T1828/T1830——逐环真实/模拟**机读标注**（"模拟被标为真实"恒 0）、固定时钟运行恒 `not_evaluable`、阈值与档位未标定**不发明数字**、成本不可比情形（平台腿、`day`/`period` 窗口累计）逐条如实备注
- **有意改造（相对 015/019 模板）**: ① 证据面（分量/标注/性能）落在**用户故事阶段**而非基础阶段——基础阶段只承担链与索引网格；② 新增**报告侧**文件 `pilot/profiles/{run_id}.json` 而**不新增第六件**（五件套口径不变，T1826/T1829）；③ 新增**叶子模块** `agents/pilot/scale.py`（派生镜头数单一持有者，F-04），而非把公式写两遍；④ `ops/demo_pilot.py` 的缩档从"脚本内等值派生"改为**配置声明的排练档**（T1805/T1821），演示的加载器计数改**派生量**；⑤ 策略装载**下沉** `agents/dev/policy_loader.py`（T1808），`ops/dev.py` 收敛为薄调用（不留第二份装载路径）
- **不做**（规格与 plan 已明确，防回潮）: 真实生成/投放厂商对接（= **运营侧输入**，`docs/pilot-upgrade-manifest.json` 的 B/C 路径 `not_delivered`，**结转 G4**）；多形态插件扩展（**G5**，`FORMS` 仍为两套配置）；**形态分支代码**（零 `form ==`）；`CostRecord` 增列（三方口径走报告层与 `cost.json` 确定性段，T1827）；**公网服务化与多租户**（仍是内部 CLI + 只读 web）；`dev` 环节的自动进化（策略来源是人、必须过静态检查、必须人工采纳）；改写历史节点或既有工件（网格参数不回溯、无迁移、无回填；019 运行记录 entry 字段集不动）
- **边界一：真实渠道 = LLM 腿真实 + 平台侧按配置（默认模拟）**——**禁止**声称"真实渠道全链路已跑通"（该表述**只**在七环节 `source` 全为 `real` 时成立，T1825/T1828 机检）；未具备期间保留 `SIMULATED_NOTE`（`agents/pilot/package.py:34-37`）与预检报告 `credentials_checked: false`（`agents/pilot/pilot.py` 的 `_backend_report` 当前 `:247`，不假装验过凭证）；渠道失败**禁止**静默回落模拟并照常计费（回落须显式声明并标注来源）
- **边界二：排练档与性能阈值的数字属运营侧输入，本特性不发明**——机制全部落地（T1801/T1818/T1826），`status: unstandardized` 期间**不覆盖**（形态原值在 force）并如实标注"未标定"；运营给定后**只改配置**（零代码改动）；T1833 如实记"待运营"。**movie 的 `target_duration_s = 5400` 不属此列**：它由既有形态原值 90 分钟算术得出（两处时长一致不变量），是**修 bug**
- **SC 映射**: SC-001→T1819/T1820/T1825/T1826/T1827/T1828/T1831/T1833；SC-002→T1802/T1803/T1804/T1806/T1812；SC-003→T1822/T1823/T1824；SC-004→T1825/T1828/T1829/T1830；SC-005→T1826/T1831；SC-006→T1810/T1824/T1825；SC-007→T1811/T1825/T1828/T1829；SC-008→T1819/T1826/T1828/T1834；SC-009→T1806/T1807/T1828/T1834；SC-010→T1813/T1814/T1815/T1816/T1817；SC-011→T1801/T1802/T1809/T1818/T1822；**SC-012**→T1801/T1805/T1810/T1818。**文档交付（非机检，E-05）**：T1832（README 章节）与 T1835（立项书 G2 状态与结转项）只做文档交付面，不承担任何 SC 的机检判定——SC-001 的"里程碑验收"证据仍以 T1819/T1826/T1827/T1831/T1833 的实跑回填为准
- **plan 的 13 项缺口落点（逐条登记，无未登记项）**: ① 每场景行数（`range(12)`）→ T1801/T1809/T1818；② `dev` 输入两字段与**输入指纹口径变更** → T1810；③ `dev` 无 `exploration_per_round_usd` → T1810（走 LLM 腿档位，不发明单轮预算）；④ 排练档数字未定 → **机制**落 T1801/T1818/T1820，**数字**属运营侧输入、**如实登记为待运营**（T1833）；⑤ 两处时长来源不一致 → T1810/T1818（规格侧 SC-012、FR-013/014 同批补；修复方向 = 长片语义 5400 s）；⑥ 既有测试/夹具/演示的陈旧字面量 → T1803/T1804/T1805/T1811/T1812/T1814（**权重差异循环只复核**，F-09）；⑦ 019 运行记录 entry 字段集不得增删 → T1831（第三腿**不落 entry**，落 `cost.json` 确定性段与报告侧）；⑧ `index_grid` 入 `render` 改变平台载荷 → T1815（本地 stub 契约同步；B 路径 `not_delivered` ⇒ 无在线影响，如实登记）；⑨ "每环节都经按环节分档门禁"缺直接机检面 → T1810（**预检前置** + 调用点清单复核，计数仍 8；**不新造** stage↔环节档映射声明）；⑩ 性能阈值无既定落点 → T1801/T1826/T1831；⑪ 五件套不变 → T1829（画像与第三方腿落**报告侧**，`PACKAGE_FILES`/`verify_package` 不改）；⑫ 平台腿无 019 账单/账本/运行记录面 → T1830（标注只来自装配面声明，如实登记"无交叉核对面"）；⑬ `topic` 来源（**已裁决**：承接 `genre`，`genre` 不进丢弃集）→ T1822/T1823，**无待裁决项**
- 本地验证纪律：命令与 `.github/workflows/ci.yml` 逐字一致（含 `ruff format --check .`；覆盖率口径含 web；对抗/无偏性门禁不放松）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
