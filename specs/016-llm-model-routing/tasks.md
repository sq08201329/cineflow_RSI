# 任务列表：LLM 模型档案与角色路由

**输入**: 来自 `specs/016-llm-model-routing/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（**原则一为核心**：价目随配置快照冻结，禁止内置）；功能 003（网关）、015（`pilot.llm_backend`/冒烟器/核查器/升级清单）已交付；规格含 2026-09-22 澄清两条（角色固定枚举、单档案自动认定默认）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；本特性的关键是**冻结可证伪**（改价目不漂移历史成本）与**缺项 100% 报错**；**本地验证命令与 ci.yml 逐字一致**。

**组织方式**: 按用户故事分组（US1 档案与价目冻结 → US2 角色路由 → US3 凭证中立与工具同步）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [X] T1601 `configs/movie.yaml` 与 `configs/shortdrama.yaml` 新增 `llm` 段（profiles：至少 `deepseek-flash` 与一个自建/本地示例；roles；default_profile；价目沿用 015 已登记的两条 DeepSeek 条目并把它们迁入档案；保留旧扁平键以验证迁移路径）
- [X] T1602 [P] conftest 夹具扩展：配置片段工厂（单档案 / 多档案 / 缺价目 / 缺端点 / 缺凭证名 / 零价目未声明 / 枚举外角色 / 多层映射 / 旧扁平写法 / 未引用档案）+ 假环境变量夹具（含"环境中存在无关 OPENAI_API_KEY"场景）；不改坏既有夹具

## 阶段 2：基础（core/llm_gateway）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [X] T1603 档案模型与解析测试 tests/unit/test_llm_profiles.py（先写：合法单档案/多档案、缺端点/缺凭证名/缺价目三类报错、零价目必须显式声明、`price_note` 缺省入 notes、端点 host 入快照）
- [X] T1604 [P] 实现 core/llm_gateway/profiles.py（`ModelProfile` / `ProfileSnapshot` / `load_profiles`）
- [X] T1605 [P] 迁移测试 tests/unit/test_llm_legacy_migration.py（旧扁平 → 单档案 + 映射说明；新旧并存以新为准 + "旧键被忽略"；旧写法缺价目报错）
- [X] T1606 [P] 实现旧配置迁移（`migrate_legacy`）
- [X] T1607 [P] 路由解析与校验测试 tests/unit/test_llm_routing.py（先写：单档案自动认定默认 + notes 标注；多档案缺默认报错；**枚举外角色报错并列出合法枚举**；映射指向不存在档案/多层/自指报错；未引用档案入 notes）
- [X] T1608 [P] 实现 core/llm_gateway/routing.py（`Role` 枚举按既有调用点盘点后定稿 + `RoleRouting` + `RouteDecision`）

**检查点**: ✅ 档案解析/迁移/路由校验三件套通过（`tests/unit/test_llm_profiles.py` 15 条、`test_llm_legacy_migration.py` 6 条、`test_llm_routing.py` 17 条；ruff 双绿）

---

## 阶段 3：用户故事 1 - 模型档案与价目随快照冻结（优先级：P1）🎯 MVP

**目标**: 档案可用 + 快照冻结 + 改价不污染历史（可证伪）

**独立测试**: 缺项报错矩阵；单档案等价现状；改价目后历史成本不漂移

### 用户故事 1 的测试（先写，确认失败后再实现）

- [X] T1609 [P] [US1] tests/unit/test_llm_profile_snapshot.py（`profile_snapshot()` 字段齐全（价目/备注/host/迁移说明，**无密钥**）；单档案行为与现状等价）
- [X] T1610 [US1] tests/unit/test_llm_price_freeze.py（**冻结可证伪**：一轮运行落树 → 修改配置价目 → 历史节点成本与快照口径一致（零漂移）；新节点用新价目）
- [X] T1611 [P] [US1] tests/contract/test_llm_profile_contracts.py 的 profiles 段（C1~C3 聚合）

### 用户故事 1 的实现

- [X] T1612 [US1] gateway 暴露 `profile_snapshot()`；**六个 Agent 的 `config_snapshot` 构造处并入 `llm_profiles` 键**（逐处接线 + 各包回归）

**检查点**: ✅ 档案可声明、快照可冻结、改价不漂移（`test_llm_profile_snapshot.py` 5 条 + `test_llm_price_freeze.py` 2 条 + `tests/contract/test_llm_profile_contracts.py` C1~C3 19 条；六处 config_snapshot 已并入 `llm_profiles`）——MVP 成立

---

## 阶段 4：用户故事 2 - 角色路由与成本分解（优先级：P2）

**目标**: 角色 → 档案路由 + 成本折算与分解 + 业务代码零厂商字面量

**独立测试**: 各角色命中断言；回落与报错；成本分解金额正确

### 用户故事 2 的测试（先写，确认失败后再实现）

- [ ] T1613 [P] [US2] tests/unit/test_gateway_routing.py（C4：`judge` 命映射；未映射枚举内角色回落默认 + `reason=default_fallback`；枚举外构造期报错）
- [ ] T1614 [P] [US2] tests/unit/test_gateway_cost_breakdown.py（C6：两档案各一次调用 → 分解两条目且金额与各自价目相符；同档案多角色分开；零价目 → 0.0 且标注"零边际成本"；**FR-009 报告层两条断言：分解报告含价目口径备注（如"峰时缓存未命中上限"）与"记账 ≠ 厂商账单"的显式声明**）
- [ ] T1615 [P] [US2] tests/unit/test_no_vendor_literals.py（**静态断言**：`core/`（除 llm_gateway 与配置解析）与 `agents/` 无厂商名/端点/模型名字面量；六个 Agent 调用点均传 `role`）
- [ ] T1616 [P] [US2] tests/contract/test_llm_profile_contracts.py 的 routing 段（C4~C7 聚合，含错误分型与"不静默回落其它档案"）

### 用户故事 2 的实现

- [ ] T1617 [US2] gateway 改造：按角色取档案 → 后端点与价目；`BackendResult` 带 `role`/`profile_id`；`cost_breakdown()` 累积；**mock 路径同样按档案价目折算**（保 mock/http 两路口径一致，断言见 T1614）
- [ ] T1618 [US2] 六个 Agent 的 LLM 调用点传 `role`（按既有语义归类）+ 各包回归

**检查点**: 路由与成本分解成立；业务代码零厂商字面量

---

## 阶段 5：用户故事 3 - 凭证中立与工具同步（优先级：P3）

**目标**: 凭证只按档案声明读取（消除假阳性）+ 核查器/冒烟器/清单以配置为权威

**独立测试**: 假阳性格局断言；分型正确；清单锁可证伪

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T1619 [P] [US3] tests/unit/test_http_backend_injection.py（端点/密钥由路由层注入；**不隐式读 `OPENAI_*`**；档案显式声明旧变量名时报告标注"沿用旧变量名"）
- [ ] T1620 [P] [US3] tests/unit/test_check_credentials_profiles.py（**假阳性归零**：环境存在无关 `OPENAI_API_KEY` 时不影响任何档案判定；unset vs unreachable 分型；档案/用途标注）
- [ ] T1621 [P] [US3] tests/unit/test_smoke_llm_profile.py（`--profile` 生效与不存在报错；`--round` 改写**角色映射**而非散落模型名；缺凭证退出码 1）
- [ ] T1622 [P] [US3] tests/unit/test_upgrade_manifest_lock.py（清单变量名 ⇄ 配置档案逐项一致；**改坏即红**且列出差异；配置加档案未登记即红）
- [ ] T1623 [P] [US3] tests/contract/test_llm_profile_contracts.py 的 readiness 段（C8~C10 聚合）

### 用户故事 3 的实现

- [ ] T1624 [US3] `core/llm_gateway/backends/http.py` 构造入参化（端点/密钥注入）
- [ ] T1625 [US3] `ops/check_credentials.py` 改为读配置档案生成就绪矩阵
- [ ] T1626 [US3] `ops/smoke_llm.py` 支持 `--profile`（`--model` 兼容）与 `--round` 改角色映射
- [ ] T1627 [US3] `docs/pilot-upgrade-manifest.json`（schema 递增）+ 机检锁以配置为权威

**检查点**: 凭证假阳性归零；工具三件套与配置同源——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T1628 契约 C1~C10 全量聚合与 SC 机检（SC-001 零厂商字面量 / SC-002 改价不漂移 / SC-003 缺项与枚举外 100% 报错 / SC-004 假阳性归零 / SC-005 单档案等价现状 / SC-006 清单锁可证伪）
- [ ] T1629 运行 quickstart.md 全部验证命令并回填"验证记录"（含无凭证下的核查器与 `--round --dry-run` 输出；覆盖率 ≥85% 复核；命令与 ci.yml 逐字一致）
- [ ] T1630 [P] 文档同步：README（档案与角色路由用法 + "价目为何不内置"的设计理由）+ `docs/二期升级路径-真实生成与投放.md`（§七 改为按档案/角色）+ `docs/二期交付总览.md`（诚实边界与遗留更新）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T1603→T1604、T1605→T1606、T1607→T1608 三链可并行
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的档案解析（路由按档案解析）；US3 依赖 US1/US2（核查器要读档案；冒烟要按档案）
- **打磨（阶段 6）**: T1628/T1629 依赖全部故事；T1630 可在基础完成后开始

### 并行机会

- 阶段 2：三条链并行
- US1：T1609/T1611 并行；US2：T1613~T1616 四条并行
- US3：T1619~T1623 五条并行

---

## 实现策略

### MVP 优先（用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：档案 + 快照冻结 + 改价不漂移
3. **停下并验证**：缺项报错矩阵、冻结可证伪、单档案等价现状（既有测试全绿）

### 增量交付

1. 搭建 + 基础 → 档案/迁移/路由解析就绪
2. US1 → 档案与冻结（MVP）
3. US2 → 角色路由与成本分解
4. US3 → 凭证中立与工具同步（里程碑验收线）
5. 阶段 6 → 契约聚合/验证记录/文档

---

## 备注

- 宪章原则一落点（本特性核心）：T1610 的"改价不漂移"断言 + T1604 的存档快照（价目与备注入 `config_snapshot["llm_profiles"]`）——**价目内置即历史不可复现**，这条纪律写进 README 与文档（T1630）防回潮
- 澄清决议落点：角色固定枚举（T1607/T1608，枚举外即报错）；单档案自动认定默认（T1607/T1608 + notes 标注）
- 原则三落点：路由只在网关（T1615 静态断言业务代码零厂商字面量；T1617/T1618 只传 `role`）
- 凭证假阳性（本机真实事故）由 T1619/T1620/T1624 消除：端点与密钥注入、只按档案声明读取
- **不做**（规格已明确，防止顺手扩大）：非 OpenAI 兼容协议、两维价目（峰谷/缓存命中）、运行时比价路由
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
