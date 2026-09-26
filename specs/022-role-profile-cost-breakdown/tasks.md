# 任务列表：角色 × 档案成本分解（022）

**输入**: `specs/022-role-profile-cost-breakdown/`（spec.md：3 用户故事 / 8 FR / 5 SC；plan.md：D1~D6 决策 + 契约修订小节）。引用一律以符号名/键名为锚。

**前置条件**: 宪章 v2.0.0（原则二 CostRecord 必入账、原则五依赖单向 `agents → core`、原则六未标定如实标注）。已核实事实（plan D1~D6）：`LLMResult` 逐调用携带 `role`/`profile_id`/`usage`/`cost_usd`/`cached`；本地缓存命中不进网关 `_breakdown`；五件套逐字节比对面不含 `CostRecord` 序列化；`core/tree/store.py` 的 `CostRecord(**row.cost)` 缺键可读回。

**测试说明**: TDD——测试任务排在对应实现之前并先红；全部离线；既有断言零删除零放宽、只按扩展更新。

**⚠️ 执行者分工（硬约束）**: 子代理只跑**单文件快速子集**；六类慢门禁（覆盖率 ≥85%、契约两腿、集成、对抗〔串行、不具并行安全性〕、无偏性、ruff 双绿）**全部由父代理在宿主机执行**（T2224~T2229）。

**⚠️ 提交分批（§3）**: **T2211 机制提交单独入库**（models + attribution + gateway docstring + 其单测）⇒ 该提交即接入侧基线 ref；**T2223 接入提交**（loops / replay / web / 契约 / 宪章 / 集成测试）。

---

## 阶段 0 勘查核对（只读）

- [x] T2203 穷举**带 llm_calls 的 `CostRecord` 构造点**（`agents/**/loop.py`、`agents/promo/material.py`、`agents/promo/ingest.py`、`core/replay/observation.py` 等；区分"真实调用结果归集"与"失败/预估入账"两类），清单与分类登记进本文件末节批次登记
- [x] T2204 [P] 核对 `web/queries.py` 成本查询现状形状（`get_costs` 的分组维度与 `tree_nodes.cost` 读法）与 `core/replay/observation.py` 的 `CostRecord` 汇总方式，确定接入点

## 阶段 1 机制侧（US1 数据面，= 机制提交）

- [x] T2205 [P] [US1] 扩展 `tests/unit/test_tree_models.py`：`llm_breakdown` D2 校验（负值/空键/Σ 超和拒绝、Σ ≤ 总量合法、空合法）、`asdict` 往返、**缺键旧 dict 读回 ⇒ 默认 + 不报错**——先红
- [x] T2206 [P] [US1] 新建 `tests/unit/test_tree_attribution.py`：`add_call`（空 role/profile_id 即 ValidationError；cached 调用由调用侧跳过、工具不复查）、`merge`（同键累加、可结合性、不改入参）——先红
- [x] T2207 [US1] `core/tree/models.py`：`CostRecord` 增列 `llm_breakdown: dict = field(default_factory=dict)` + `__post_init__` 结构校验（不耦合 Role 枚举，原则五）——绿 T2205
- [x] T2208 [P] [US1] 新建 `core/tree/attribution.py`：`add_call` / `merge` 薄工具（纯函数，零网关依赖）——绿 T2206
- [x] T2209 [P] [US3] `core/llm_gateway/gateway.py` 的 `cost_breakdown()` docstring 过时口径（"CostRecord 口径不变（分解只在报告层呈现）"）改为新口径
- [x] T2210 快速核对：单文件跑 T2205/T2206 两测试文件 + `ruff check` 触及文件
- [x] T2211 **机制提交**（父代理）：仅 T2205~T2209 触及文件入库，提交号记为 `mechanism_ref` 并登记末节

## 阶段 2 接入侧 A（US1 loops 与汇总，基线 = T2211 提交）

- [x] T2212 [P] [US1] 新建 `tests/integration/test_cost_attribution.py`：固定种子多 Agent 运行 ⇒ 逐节点 Σ calls/tokens ≤ 总量且非缓存节点取等；**全部新节点分解Σ与运行内网关 `cost_breakdown()` 逐格一致**（SC-001/002）；含一条"缺键旧行读回 ⇒ 展示三态之「未标定」"用例——先红
- [x] T2213 [US1] `agents/screenplay/loop.py` 接入 `add_call`（含失败/预估节点按 plan D4"拟走角色 × 档案"记录 `calls=1, tokens=0`，profile_id 取 `gateway.route(role)` 判定值）；**顺带修正 :796 欠计缺陷**——`generated.usage` 在手 ⇒ `llm_tokens` 补填真实值（断言按扩展更新）
- [x] T2214 [P] [US1] `agents/promo/{material.py,loop.py,ingest.py}` 接入（`_cost_record` 增参传递 breakdown；cached 结果跳过；归属直接取 `LLMResult.role`/`profile_id`）
- [x] T2215 [P] [US1] 其余 loops 按 T2203 清单接入（visual / storyboard / editing / dev 中带 llm_calls 的构造点；judge 归属 profile_id 取 `gateway.route(role)` 判定值）；**顺带修正 `dev/loop.py:675` 欠计缺陷**——已完成调用 tokens 补入 `llm_tokens`（断言按扩展更新）；**sound 零接入**（无 LLM 字段）
- [x] T2216 [P] [US1] `core/replay/{observation.py,trajectory.py}` 的 `CostRecord` 汇总接入 `merge`（`total_cost` 分解可回溯）
- [x] T2217 核对：T2212 转绿 + 触及文件单测单文件跑 + ruff

## 阶段 3 接入侧 B（US2 消费面）

- [x] T2218 [P] [US2] `web` 查询分组测试先红（文件依 T2204 结论）：`group_by=role_profile` 分组值 == 网关分解同键；含历史节点时「未标定」单列、不摊入任何分组；「缓存命中（零计费）」与「未标定」不混标（D3 三态）；「未标定」组计数/占比可断言取得（SC-003）
- [x] T2219 [US2] `web/queries.py`：`get_costs` 新增 `group_by=role_profile` 维度（读原始 JSON 键在场性区分三态；既有维度行为不变）——绿 T2218

## 阶段 4 治理件（US3）

- [x] T2220 [US3] `specs/016-llm-model-routing/contracts/gateway-routing.md` C6 **仅替换末句**为"六字段口径不变 + 分解为 022 扩展字段、口径同 `cost_breakdown()`"（diff 除此句外为空）
- [x] T2221 [P] [US3] `.specify/memory/constitution.md` 原则二 `CostRecord` 条目后**增句**登记扩展字段口径（不删改既有条款；版本 v2.0.0 → **v2.1.0** MINOR，2026-09-26；`AGENTS.md` 引用同步）
- [x] T2222 [US3] 规格勾稽：`spec.md` 开放问题 1（表示形态=嵌套映射）已被 D1 裁决、假设段"网关采样边界"条已被 D6 闭环（analyze 修订已就地落实）⇒ 规格状态 Draft → Reviewed 并登记
- [x] T2223 **接入提交**（父代理）：T2212~T2222 触及文件入库

## 阶段 5 收口门禁（父代理宿主机，**串行**，贴实测数字）

- [x] T2224 覆盖率 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`（≥85%）
- [x] T2225 契约两腿：`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`
- [x] T2226 集成 `uv run pytest tests/integration -m integration`
- [x] T2227 对抗 `uv run pytest tests/adversarial -m adversarial`（**必须串行**，不与任何套件并跑）
- [x] T2228 无偏性 `uv run pytest tests/unbiasedness -m unbiasedness`（τ≥0.95）
- [x] T2229 `uv run ruff check . && uv run ruff format --check .`
- [ ] T2230 勾选回填 + 实测数字入本文件 + 批次/未决登记（含 pilot 五件套逐字节比对既有测试仍绿的证据行）

---

## 依赖与串行点

- 阶段 0 → 阶段 1（T2203 清单是 T2213~T2216 的接入面）；T2207 ← T2205、T2208 ← T2206（TDD）；**T2211 是全部接入侧任务的基线**
- T2212 先红依赖 T2207/T2208 已在机制提交中（集成测试引用新字段）
- 同文件串行：`agents/promo/loop.py` 被 T2214 单点触碰；loops 间互不重叠 ⇒ T2213/T2214/T2215/T2216 可并行（委派仍按 §2.2 一次一个子代理）
- T2220 与 T2221 互不重叠 [P]；T2223 依赖阶段 2~4 全绿

## 批次登记（执行中回填）

| 批次 | 内容 | 提交号 | 门禁实测 | 未决项 |
| --- | --- | --- | --- | --- |
| 机制 | T2205~T2209 | 3929cb0（机制提交；设计件前置提交 75a714b） | 单文件 62 例绿 / ruff 触及文件绿 | — |
| 接入 | T2212~T2222 | e39012d（接入提交） | 抽查 74 例绿（web_board 23 + web_server 47 + cost_attribution 4）；慢门禁见"门禁终轮"行 | ① judge `last_usage` 仅聚合值（评估器零改动红线）⇒ judge 格 prompt/completion 拆分不可得，tokens/cost 合计精确、记于首格，如实登记；② 展示层在三态外如实增设第四态 `no_llm`（键在场且空且 llm_calls==0，父代理裁决认可）；③ `web/server.py` 路由补 `group_by` 透传（T2219 面扩展，父代理裁决）；④ `core/replay/trajectory.py` 无汇总点零改动（simulator 经 `sum_costs` 已覆盖） |
| 收口修复 | 门禁首轮红两处的修复 | 6480ed7（web parity 修复：get_node 详情成本改全量 `_cost_payload`）+ 8ab83b9（接入清单基线重打 4e90164→6480ed7）+ 86b9cfa（web_queries 断言按扩展加键 + 3 新文件 format + E501） | 修复点快核：parity 用例+web 单测 71 例绿；onboarding 18 例绿（越界 0 / exit 0）；ruff 614 全绿 | 首轮门禁如实登记：契约两腿各 1 红（web_parity 详情字段——`_cost_record` 六字段投影丢了新键）、集成 4 红（021 接入清单检出 022 改动越界——机制件批次再动，按测试内"重打纪律"前移基线，先例 8b413d4/d26e8e7）；第二轮 1 红 `test_deploy_cli.py::test_spot_check_creates_task_and_lists_pending`（与 022 改动面无交集，单跑/文件级跑均绿，终轮未复现 ⇒ 记一次性 flake，留观察） |
| 门禁终轮 | T2224~T2229（父代理宿主机串行，树 = 86b9cfa） | — | **覆盖率 92.75% ≥85%（4561 passed，1:05:29）**；契约腿1 **542 passed**（56 skipped）；契约腿2 stub **598 passed**；集成 **120 passed**（55 deselected）；对抗 **25 passed**；无偏性 **40 passed**；ruff check 全绿 + format **614 files** 已格式化 | — |

**T2203 构造点清单**（勘查实测，agent-1 回报）：

- **(a) 可归集（LLMResult / judge usage 在手）**：`agents/promo/material.py:53`（cost dict）→ `agents/promo/loop.py:94` `_cost_record`（调用点 :375/:397/:420/:442/:465/:493）；`agents/promo/ingest.py:130`（metrics 透传）；`agents/screenplay/loop.py:874`（成功，judge usage 经 `_score()` :305-328）；`agents/dev/loop.py:779`/`:712`/`:761`/`:659`（逐调用 dict :635-645）；`agents/storyboard/loop.py:576`、`agents/visual/loop.py:462`/`:486`、`agents/editing/loop.py:618`（judge `last_usage`）
- **(b) 失败/预估入账（按 D4"拟走角色×档案"记录）**：`agents/screenplay/loop.py:766`（网关失败 estimated）；`agents/dev/loop.py:675`（GatewayError，calls 含失败笔）
- **(b') usage 真丢失、不归集**：`agents/screenplay/loop.py:855`、storyboard/editing 崩溃路径（judge usage 崩溃即失，六字段现状不动）
- **(c) 纯汇总**：`core/replay/observation.py:63-77` `sum_costs()`（六字段逐项加，T2216 接入 `merge`）；`core/replay/simulator.py:79/:193` 零初始化与累入；`core/tree/store.py:82` 还原（零改动）
- **sound 全程无 LLM 字段**（无 judge）⇒ 022 零接入
- **裁决登记（父代理，analyze 后）**：① `screenplay/loop.py:796` 与 `dev/loop.py:675` 的 **llm_tokens 欠计缺陷顺带修正**（usage 在手未填/丢失；不补则 Σ ≤ 校验假红；原则二/六，断言按扩展更新）；② judge 归属：role 静态已知，`profile_id` 取 `gateway.route(role)` 判定值（价目随快照冻结 ⇒ 与调用时一致）；promo 处直接用 `LLMResult` 自带归属

**T2204 结论**：`web/queries.py` `get_costs`（`_COST_SQL` :80 / `_cost_record` :339-346）读**原始 JSON dict**（三态区分可行）；现状分组 `(agent_id, ISO 周)`、只聚合 `generation_api_cost_usd`；返回 `{items, agents, periods, total_usd, node_count}`；既有单测 `tests/unit/test_web_board.py:106-137` ⇒ T2218 落该文件。
