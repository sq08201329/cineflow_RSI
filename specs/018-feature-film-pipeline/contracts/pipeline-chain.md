# 契约：七环节串链（链首插入 `dev` + 清单同步不变量）

> 对应规格 FR-001~003、FR-007、SC-002、US1（场景 1/4/5/6）、澄清第 2 条。实现：`agents/pilot/`
> （`stages.py` 阶段元组/阶段表/`AgentConfigs`/运行时装配、`pilot.py` 预检四处清单、`package.py`
> 产物 kind 登记）。现状：六环节链 `script → storyboard → visual → sound → editing → promo`
> （`agents/pilot/stages.py:94`），`dev` 已在 017 交付为可运行的独立轮次但**未入链**——
> 017 明确选择"不插入"（`specs/017-dev-agent-degraded/spec.md` 澄清第 3 条），插入动作归本特性。

## C1 链首插入与清单同步不变量（漏一处即红）

```
PILOT_STAGE_IDS = ("dev", "script", "storyboard", "visual", "sound", "editing", "promo")
```

- **链首是 `dev` 不是 `script`**：`dev` 阶段 `depends_on=()`，`script` 改为 `depends_on=("dev",)`；
  拓扑序恰为上式（`core/orchestration/dag.py:27 topological_order`）。
- **必须同步的集中声明点**（逐处点名的机械清单，任一处漏改即红）：
  ① `agents/pilot/stages.py:94` 阶段元组；② `:1018` `build_stage_specs` 阶段表（新增 `dev` 项）；
  ③ `:102-111` `AgentConfigs`（新增 `dev: DevConfig`）；④ `:138-198` `build_runtime` 运行时装配——
  含 `:173-180` 建表段（新增 `agents/dev/db.py:60 create_jobs_schema`，`dev` 有自己的作业表）；
  ⑤ `agents/pilot/pilot.py` **四处**：`:114-129` 配置加载器、`:135-140` 权重循环、`:198-205`
  `AgentConfigs` 构造、`:208-224` 预检预算循环；⑥ `agents/pilot/package.py:173-181` 产物 kind 登记；
  ⑦ 形态配置两形态声明；⑧ 测试与夹具侧同类字面量（见"会变红的既有断言"）。
- **不靠人记得，靠断言**（规格边界情况第 1 条的正解）：三处一致性断言——
  ① `tuple(spec.stage_id for spec in build_stage_specs(runtime)) == PILOT_STAGE_IDS`；
  ② `STAGE_CONFIG_SECTION`（单一映射声明）的值域 == `AgentConfigs` 的字段名集，且键域 == `PILOT_STAGE_IDS`
  ——`script → screenplay`、`dev → dev`，**不得**以 stage_id 直推段名；
  ③ 阶段表 `output_kind` 的**全部取值** ∈ `package._KIND_CONTENT_TYPE`（C4）。
- 静态断言**方向不得反转**，插链后仍须常驻：`tests/unit/test_pilot_stages.py:127-137`（编排层不新增
  落树路径、无形态分支）与 `:140-162`（`agents/` 不得 import `ops/`）——故 `dev` 阶段的策略装载只能走
  `agents/` 侧（C2），**不得**从 `ops/dev.py` 取用。

### 会因此变红的既有断言（必须同步更新，不得为过测试放宽）

`tests/unit/test_pilot_stages.py:37-56`（阶段顺序与装配）、`tests/contract/test_pilot_contracts.py:368-375`
（`completed_stages` 六元组字面量）、`tests/unit/test_pilot_package.py:59-66` 与 `:72-79`（阶段 id 列表）、
`tests/unit/test_pilot_run.py:90-97`（同）、`tests/unit/test_pilot_product_labels.py:127`（"六阶段全部有产物"
计数）及 `:36-44`（内容类型对照表，测试侧独立声明，须与 C4 双向一致）、`tests/unit/test_form_switch.py:151-159`
与 `:260-281`（权重与形态差异清单）。**书面备忘**：`tests/conftest.py:3159-3163` 明写"不声明 `dev` 段……
那属 G2/018"，该夹具（`_MINIMAL_MOVIE_CONFIG`，起始 `:3164`）与注释一并更新。

### 场景

1. 装配 DAG → 拓扑序 == 七元组；每环依赖前一环；阶段元组/阶段表/`AgentConfigs` 三处不一致即红
2. 模拟漏同步（从阶段表删 `dev`）→ 一致性断言红（不是"少一环也能跑"）
3. 静态断言常驻：`agents/pilot/stages.py` 无形态字面量、无新增落树路径；`agents/` 不 import `ops/`

## C2 `dev` 阶段入口与产出契约

`run_dev_round(round_id, policy, store, artifacts, engine, gateway, config, inputs) -> DevRoundResult`
（`agents/dev/loop.py:490`）

- 入口：`_dev_entry(stage_input)` 只做搬运与判定，**零新增落树路径**——落树/幂等/成本对账全部由
  `run_dev_round` 既有实现承担（节点一次性 INSERT，`FAILED` 同样入账）。
- **策略装载必须走 `agents/` 侧**：现状 `exec(compile(source…))` + 静态检查 + 版本核验只在
  `ops/dev.py:81-123 _load_policy`（源码文本通道在 `agents/dev/policy_versions.py:68 load_policy_source`），
  而 `agents/` 不得 import `ops/` ⇒ **必须**把装载（静态检查 `policies/static_check.check_policy_source`
  前置 → 版本核验 → 实例化）下沉到 `agents/dev/` 侧单一实现，`ops/dev.py` 改为薄调用（不得留第二份，
  同 019"两处收敛目标"口径）。装载失败 ⇒ 阶段 `failed` 且原因点名（**不**静默取"最新/第一条"策略）。
- 版本来源 = 形态配置的部署指针 `deployment.dev.current_policy_version`（`configs/movie.yaml:625`、
  `configs/shortdrama.yaml:624`；按 `stages.py:205-227` 的直读 YAML 口径读取），缺指针/源码不存在 ⇒
  **启动前拒绝**（不回落）。
- 产出：`StageOutcome(products=(ProductRef(kind="slate", ref=<slate_hash>, content_hash=<slate_hash>),),
  cost_usd=result.spent_usd, candidates=<由 result.job 构造：artifact_hash 非空 ⇒ 1.0，否则 0.0 + 理由>,
  detail={artifact_hash, policy_version, entry_count, production_marks, cost_reconciliation, spent_usd})`。
  `kind` 取值与 C4 一致；`detail` 键集是本阶段对下游的**唯一**可见面。
  `content_hash` 的可核性：工件按 `artifacts.put(artifact.canonical_json().encode())` 入对象存储
  （`agents/dev/loop.py:720`），`put` 返回 BLAKE3 全文哈希（`core/tree/artifacts.py:24-26`）⇒
  `content_hash == slate_hash()`（`agents/dev/artifact.py:259-261`），两条取数路径同值。
- 失败语义：`result.job["status"] == "rejected"`（执行前拒绝）与 `failed`（已发生费用照计）都判 `failed`，
  下游 `script` 如实 `skipped`、零调用（`core/orchestration/models.py:439-457` 已机检"失败点之后必须 skipped"）。
- **输入来源（判断项）**：`run_dev_round` 的 `_validate_inputs`（`agents/dev/loop.py:176-191`）要求
  `genre_bounds` + `audience`，而运行级输入是 `PilotInputs(topic, target_duration_min, characters,
  constraints)`（`agents/pilot/pilot.py:40-58`）——**两套字段名不重合**。规格把字段级映射留给 plan，
  故本契约只绑定**形状与禁令**：`dev` 输入必须由**显式声明**的运行级映射（或形态配置声明）产生，
  逐字段可追溯；**未声明的直通禁止**；任一 `dev` 实际读取的字段无来源 ⇒ 预检拒绝启动（不静默补默认）。
  具体键名与映射函数归 plan。

### 场景

1. 合规运行 → `dev` 阶段 `done`、产物 `slate` 可寻址、`detail.policy_version` 与部署指针一致
2. 策略未过静态检查 / 部署指针缺失 / 源码不存在 → 启动前或装配期拒绝（0 网关 0 落树）
3. `dev` 执行前拒绝 → 阶段 `failed` 且原因点名，`script` 及之后全 `skipped`、下游调用计数 0
4. 同 `run_id` 二次触发 → 撞树锚点幂等重建（0 重复节点 0 重复扣费）

## C3 `dev` 阶段的预算与预检处理（LLM 腿档位，非静默）

- `dev` **无平台槽位**（`agents/pilot/backends.py:60 PLATFORM_SLOTS` 不含 `dev`/`screenplay`），
  也**无 `exploration_per_round_usd`**（`agents/dev/config.py:149-163` 字段表里没有该键）——
  故预算面走 LLM 腿分档：预检循环（`pilot.py:208-224`）对 `dev` 命中 `getattr(config,
  "exploration_per_round_usd", None) is None` 分支，登记 `budget.tiers` 的**环节 id** 声明额度，
  **不得**新增 `0.0` 占位、不得为 `dev` 造 `exploration_per_round_usd`。
- `budget.tiers.dev` 必须在**两形态均声明**：`configs/movie.yaml:537-541`、`configs/shortdrama.yaml:539-543`
  （现已在位）；缺档即预检拒绝启动（019 口径不放宽，SC-006）。
- 调用点已声明环节 id：`agents/dev/loop.py:632` 的 `stage="dev"`（019 C9）——本特性**不新增 `.chat(`
  调用点**（计数仍为 8），故 `tests/unit/test_no_vendor_literals.py` 的调用点断言无需扩面，但须复核
  `dev` 取值 ∈ 两形态档位键集。
- 归属口径**不发明映射**：`budget.tiers` 的键就是调用点声明的 `stage=` 取值；`dev` 恰好同名，属巧合
  而非规则（不得据此推广"agent 名 == 环节 id"）。

### 场景

1. 两形态均声明 `dev` 档 → 预检通过；缺 `dev` 档 → 启动拒绝（0 调用 0 落树）
2. 超限 → 调用前拒绝、该笔零入账（019 零成本分支），失败原因点名"预算拒绝 + 剩余/所需额度"
3. `DevConfig` 出现 `exploration_per_round_usd` → 断言红（LLM 腿选项不得混入平台腿口径）

## C4 `dev` 产物的 kind 登记

- `agents/pilot/package.py:173-181 _KIND_CONTENT_TYPE` 新增 `"slate": "json"`（`TopicSlate` canonical JSON）；
  未登记即 `check_product_kinds` 拒绝装配（`:197-217`，既有行为，不放宽）。
- 登记面与测试侧对照表**双向一致**：`tests/unit/test_pilot_product_labels.py:36-44` 的
  `_EXPECTED_CONTENT` 同步新增 `"slate": "json"`；两侧任一单侧新增即红。
- `slate` 的 `content_hash` = `slate_hash()`（`agents/dev/artifact.py:259-261`，BLAKE3 canonical JSON），
  与 `ProductRef.content_hash` 的 64 位 hex 约束（`models.py:110-113`）相容。

### 场景

1. `dev` 产物 `kind="slate"` 与字节自描述类型（`json`）一致 → 装配通过
2. 去掉 `_KIND_CONTENT_TYPE` 登记 → 装配拒绝且点名"未登记内容类型"；测试侧对照表不补 → 红
