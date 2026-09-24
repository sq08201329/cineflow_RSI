# 契约：样片包证据面（评估分量 + 真实/模拟标注 + 性能与预算门禁）

> 对应规格 FR-008~012、SC-004/005/007/008、US3（场景 1~5）、澄清第 5/7 条。
> 实现：`agents/pilot/package.py`（五件套不变，`PACKAGE_FILES` `:31`）、
> `agents/pilot/pilot.py`（预检报告）、`agents/pilot/backends.py`（装配面声明）、
> `core/orchestration/{models,ledger}.py`（运行记录与账目）、新增报告侧性能画像。

## C11 逐环节评估分量入包（取自树节点）

- **现状缺口（015 遗留）**：样片包 `state.json` 只有候选的 `candidate_id`/`score`/`reasons`
  （`package.py:272-292 build_state`），而分量级 `eval_breakdown` 只留在树节点
  （`core/tree/models.py:76`）——015 规格曾把它列为交付物（`specs/015-pilot-shortdrama/spec.md:80`），
  本特性补齐（FR-008）。
- 落点：`state.json` 新增 `eval_breakdown`，形状 = `{stage_id: {node_id: {"<evaluator_id@version>":
  {"score": …, "diagnostics": …}}}}`，**口径复用节点原文**（键 = `evaluator.spec.key` =
  `evaluator_id@version`，`core/evaluators/base.py:69-72`；落点 `agents/screenplay/loop.py:302-305` 同款），
  不改写、不归一化、不重算。
- 取数：`runtime.store.nodes_of(tree_id)`；`tree_id` = `STAGE_TREE_PREFIX[stage_id] + "-round-" +
  f"{run_id}-{stage_id}"`（轮次树前缀见 [../data-model.md](../data-model.md)；`_nodes_by_artifact`
  口径 `stages.py:1154-1158`，只取 `depth >= 1`）。**不得**以 stage_id 直推树前缀（`script` 实为
  `screenplay-round-`，`dev` 实为 `dev-round-`）。
- 覆盖面：逐环节至少覆盖该环节全部候选节点（`depth >= 1`）；节点按 `node_id` 排序，故确定性。
- **缺项即拒绝装配**（FR-008）：`_validate_pieces`（`package.py:79-95`）新增校验——七环节
  **每个**都要有非空分量面；空对象 / "不适用" / 缺环节 ⇒ `PackageError`（不得以空冒充实，SC-007）。
- 确定性：分量面只含评估器键、分数与 `diagnostics`（诊断值本身确定性）；**不得**写入 `created_at`
  类墙钟字段（`TreeNode.created_at` 为 `time.time()`，`core/tree/models.py:80`——不得外泄入包）。

## C12 逐环节真实/模拟标（机读，不得谎报）

- **现状**：平台侧五环节的取值只存在于装配面 `PilotBackends.resolved`（`backends.py:146-151`）与
  预检报告（`pilot.py:240-252`），**没有进包**；`dev`/`script` 两环节无平台槽位
  （`PLATFORM_SLOTS` `backends.py:60` 不含它们），来源只能取 LLM 腿口径（`backends.py:227`：
  `real` iff `llm_backend == http`，否则 `simulated`）。
- 落点：`manifest.json` 的 `stages[]` 新增 `source`（取值域 `real` / `simulated`）与 `channel`
  （该环节生效的后端/档案引用）；顶层新增 `channels` 汇总（`llm` 的来源、`adapter_ref`、`profile_id`
  + 平台侧逐环节来源）。预检报告同步新增 `stages: {stage_id: source}`（FR-009 要求预检报告也带）。
- **取值只来自装配面声明**：不得推断、不得默认、不得由"是否有凭证"反推；`dev`/`script` 取 LLM 腿
  口径；平台五环节取各自的 `resolved` 槽位。缺失声明 ⇒ 装配期已拒绝（`BackendAssemblyError`），
  包内不得出现空标注。
- **诚实约束**：`simulated` **恒不得**被标为 `real`（SC-004"模拟被标为真实次数恒为 0"）；019 的保留值
  `fallback`（`core/billing/runlog.py:28 RUN_SOURCES`）出现即**原样标注**并排除出"真实链路"口径，
  不得折叠进 `real` 或 `simulated`（回落须显式声明，019 既定）。
- **LLM 腿可交叉核对**：包内 `llm.source` 须与 019 运行记录的逐调用 `source` 一致
  （`billing/{channel}/runs/{date}.json` 的 `entries[].source`）；模拟日的包标注 == `simulated` 且
  不计入 019 的 `covered_days`（口径同源，不另立）。
- **"真实渠道全链路已跑通"的门控**（FR-011 / SC-004）：该结论**只**在七环节 `source` 全为 `real`
  时才可产出；任一为 `simulated`（今天的默认情形）⇒ 包与画像**不得**出现该表述，且须保留
  `SIMULATED_NOTE`（`package.py:34-37`）与预检报告的 `credentials_checked: false`（`pilot.py:247`，
  不假装验过凭证）。真值域张力已登记：FR-009 写二值、019 是三值，本契约的处置是"二值为常域、
  `fallback` 原样透传且不算真实"。

## C13 性能与预算门禁的证据面 + 包的可复现性

- **性能画像（报告侧，不入包）**：`pilot/profiles/{run_id}.json`，字段 = 各环节
  `started_at`/`finished_at`（运行记录 `StageState`，`core/orchestration/models.py:242-243`）+
  体量指标 + 阈值快照 + `verdict` + 时钟口径。
  - 体量指标全部取自既有明细，不新造测量：镜头数（分镜 `detail["shot_count"]`，`stages.py:496`）、
    片段数（视觉 `detail["clip_count"]`，`:577`）、成片时长（剪辑 `detail["reel"]`）、
    页数（`ScriptArtifact.page_count(lines_per_page)`，`agents/screenplay/artifact.py:434-437`）。
  - `verdict ∈ {meets, below, not_evaluable}`，与 `pilot.performance` 的阈值对照（阈值键 = 七环节 id）。
  - **固定时钟运行不得产出达标结论**：`clock_mode = fixed` ⇒ `verdict = not_evaluable` 且标注
    "耗时为确定性常量，不构成性能证据"。两层判定：① 入口**显式声明**时钟口径（无默认，
    镜像 `ops/pilot.py` 的 `--fixed-clock` 语义）；② **退化交叉核验**——各环节
    `started_at`/`finished_at` 全等 ⇒ 强制 `not_evaluable`（声明撒谎被证据推翻）。
    **登记边界（如实）**：单次运行无法自证用过系统时钟，声明层不可自证；本契约不改
    `RunRecord` 字段（015 冻结工件），故接受"声明 + 退化核验"两层，残余风险登记为边界。
  - 阈值 `status: unstandardized` ⇒ `verdict = not_evaluable` + 标注"未标定"，
    **不得**在配置或代码里发明数字（SC-005）。
- **预算门禁不重做**（与 019 串联）：每环节额度 = `budget.tiers` 的**环节 id** 声明
  （`configs/movie.yaml` 当前 `:531-541`、`configs/shortdrama.yaml` 当前 `:533-543`；`dev` 档在位），
  证据 = 019 的运行记录 + 账本 + 告警留痕；超限为**调用前拒绝、零入账**（019 零成本分支口径），
  被拒的那一笔与"已发生花费"在证据面上**分开登记**（原则二只覆盖已发生）。本特性**不新增**调用点、
  不新增计费机制、不新开凭证面；`dev` 档缺声明 ⇒ 预检拒绝启动（`agents/pilot/pilot.py` 预检的
  缺档拒绝与按环节登记循环，当前 `:144-159`/`:208-224`）。
- **网关构造面与账目取样面不扩**（E-03）：本特性**不新增 `LLMGateway(` 构造点**——019 的构造点普查
  仍为 **13 处**（真实 2 + `ops/` 离线显式 `None` 8 + `ops/` 离线注入真守卫 3），聚合断言须复述该计数；
  成本第三方腿的实现件 `agents/pilot/run_report.py` **只读** `LLMGateway.total_cost_usd`
  （环节边界只读采样，网关侧当前 `:167` 的"网关账本（对账三方之一）"），**不构造网关、不改网关契约、
  不改 `cost_breakdown`**；019 运行记录的 entry 字段集（`core/billing/runlog.py` 的 `RUN_ENTRY_FIELDS`，
  当前 `:32-42`，链式摘要输入）**增删即红**。
- **体量口径一致性是同一证据面**（SC-012①）：性能画像与包内 `volume` 的成片时长取自**排练档覆盖后的
  生效值**，与 `screenplay.target_duration_min × 60` 一致（容差 `1e-6`）；不一致 ⇒ 预检**拒绝启动并
  点名两处实测值**、装配与画像都**不得**产出（机检条目见 C10）。
- **可复现性（五件套口径不变）**：
  - `PACKAGE_FILES` 仍为五件（`package.py:31`），`verify_package`（`:105-138`）不新增文件；
    性能画像**不入包**（墙钟只能在报告侧，规格边界情况"重跑一致性"）。
  - 同输入同配置、独立工件根两次运行 ⇒ 五件套逐字节一致（含本轮新增的 `source` / `channels` /
    `work_kind` / `eval_breakdown` / `volume` 字段）；沿用既有断言
    `tests/contract/test_pilot_contracts.py:378-384`。
  - **新增字段的墙钟隔离机检**（本契约的判断项）：同一输入/配置下，**一次固定时钟、一次系统时钟**
    两次运行，上述新增字段子集**逐字节一致**（全包跨时钟比对本特性不立断言——015 未立，
    不擅自扩大断言面）。字段级禁令：新增字段不得含 `created_at`/`now()`/绝对路径/进程内顺序。
  - 包内字段与产物标签的一贯性：`check_product_kinds`（`package.py:197-217`）与分析字段同步生效，
    `dev` 的 `slate` kind 登记见 [pipeline-chain.md](pipeline-chain.md) C4。

### 场景

1. 七环节全 `done` → `state.json` 逐环节有非空 `eval_breakdown`（键 = `evaluator_id@version`）；
   抹掉任一环节的分量面 ⇒ 装配拒绝（不产半包）
2. 全模拟后端 → `manifest.stages[].source` 七项全 `simulated`，包内无"真实渠道全链路"表述，
   `SIMULATED_NOTE` 在位；把某项改标 `real` ⇒ 断言红
3. 声明 `llm_backend: http` 而平台侧仍模拟 → `llm` 为 `real`、平台五环节为 `simulated`，
   `channels` 汇总如实分层（分层标注可机读率 100%）
4. 固定时钟运行 → 画像 `verdict = not_evaluable` + "不构成性能证据"；时间戳全等 ⇒ 即使声明
   `clock system` 也被强制 `not_evaluable`
5. `pilot.performance.status = unstandardized` → 画像只出台账与体量 + "未标定"，无数字、无达标结论
6. 同输入同配置跨时钟两次运行 → 新增字段子集逐字节一致；样片包仍为五件套、过 `verify_package`
7. 某环节超 `budget.tiers` 额度 → 调用前拒绝、该笔零入账、证据留 019 的运行记录与告警；
   拒绝笔与已发生花费在证据面上可辨
