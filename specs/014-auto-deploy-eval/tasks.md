# 任务列表：策略部署评估自动化（证据门槛 + 影子模式 + 人工事后抽检）

**输入**: 来自 `specs/014-auto-deploy-eval/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（**原则六新条款为核心**：证据门槛三要件、抽检否决回滚、影子模式前置）；功能 005（做梦/审批/指针/谱系）、009（禁止名单）、011（池化口径）、012（漂移证据接口）已交付；规格含 2026-09-21 澄清三条（无偏性前置、渐进抽检、轮次收口触发）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；本特性核心门禁为**组合矩阵双向断言**（不满足即拦截）与**失败路径**（回滚失败保持人工）；**本地验证命令与 ci.yml 逐字一致**（`ruff check .` + `ruff format --check .`）。

**组织方式**: 按用户故事分组（US1 门槛判定 → US2 影子模式 → US3 自动部署与抽检）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T1401 deployment/ 数据目录约定（mode/evidence/shadow/deploys/spot_checks/rollbacks 子目录 + .gitkeep）+ configs/movie.yaml 的 deployment 段扩展（mode_default=manual、gate {validation_top_ratio=0.2, require_unbiasedness=true, allow_without_judge=false}、shadow {min_days=14, min_candidates=20}、spot_check {first_n=5, ratio=0.2}）
- [x] T1402 [P] conftest 夹具扩展：候选与证据组合矩阵夹具（全满足/单要件不满足/证据缺失/禁止名单）、池化回放结果夹具（011 口径）、漂移状态夹具（012 verdict）、指针与部署留痕夹具、deployment 临时目录夹具；不改坏既有夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T1403 模型测试 tests/unit/test_deployment_models.py（先写：10 个 frozen 模型的字段与校验——判定优先级枚举、模式迁移合法性、影子计时非负、误入率分子分母 ≤ 分母 等）
- [x] T1404 [P] 实现 core/deployment/models.py（EvidenceBundle/GateVerdict/EvidenceSnapshot/DeployModeState/ShadowEvent/ShadowReport/AutoDeployEvent/SpotCheckRecord/RollbackEvent + 枚举）
- [x] T1405 [P] 配置测试 tests/unit/test_deployment_config.py（deployment.gate/shadow/spot_check 解析；缺项即报错；阈值域校验）
- [x] T1406 [P] 实现配置解析（core/deployment/config.py 或并入既有 core 配置风格）
- [x] T1407 [P] 模式状态机测试 tests/unit/test_deploy_mode.py（C4：合法迁移、manual→auto 直连拒绝、影子期区间累计、重标定标记对 auto 的阻断）
- [x] T1408 [P] 实现 core/deployment/mode.py（DeployModeState 持久化 deployment/mode.json，只增不改）

**检查点**: 模型/配置/模式状态机就绪——用户故事可开始

---

## 阶段 3：用户故事 1 - 证据门槛评估与判定（优先级：P1）🎯 MVP

**目标**: 证据包（前置 + 三要件）+ 判定优先级 + 证据快照

**独立测试**: 组合矩阵全覆盖；缺证据与禁止名单的拦截语义

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T1409 [P] [US1] tests/unit/test_deployment_evidence.py（C1：前置无偏性未通过 → 整体证据不足；三要件齐备；validation 缺失/池化回放缺失/漂移缺失 → 对应要件 missing；无 judge 的 Agent（promo/sound）→ drift 要件 not_applicable；口径来源引用齐全）
- [x] T1410 [P] [US1] tests/unit/test_deploy_gate.py（C2：全满足 eligible、单要件不满足 blocked + 理由、缺证据 insufficient_evidence、screenplay/dev forbidden_agent（优先级最高）、allow_without_judge=false 时无 judge 判 blocked）
- [x] T1417 [P] [US1] tests/contract/test_deployment_contracts.py 的 evidence-gate 部分（C1~C3 端到端聚合，含 C3 快照幂等）

### 用户故事 1 的实现

- [x] T1411 [US1] 实现 core/deployment/evidence.py（前置 + 三要件；口径复用 005/011/012，不新造对比逻辑；依赖 T1404、T1406）
- [x] T1412 [US1] 实现 core/deployment/gate.py（优先级判定 + 证据快照落盘 deployment/evidence/...，同判定幂等）

**检查点**: 门槛判定矩阵全绿、快照可追溯——MVP 成立

---

## 阶段 4：用户故事 2 - 影子模式与对照报告（优先级：P2）

**目标**: 影子期不部署 + 对照报告 + 影子期门禁 + 唯一入口三行为

**独立测试**: 影子期指针变化 = 0；门禁缺口拒绝；误入率可重算

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T1413 [P] [US2] tests/unit/test_deployment_shadow.py（C5：影子事件差异分类四类、对照报告字段齐全、**误入率重算 == 报告值**、影子期指针变更次数 0）
- [x] T1414 [P] [US2] tests/unit/test_deploy_gate_shadow_window.py（C4 门禁：影子期时长不足拒绝、候选数不足拒绝、双满足才允许 auto、recalibration_required 时即使影子期满足也拒绝）
- [x] T1418 [P] [US2] tests/contract/test_deployment_contracts.py 的 shadow-mode 部分（C4~C5 聚合）

### 用户故事 2 的实现

- [x] T1415 [US2] 实现 core/deployment/shadow.py（ShadowEvent/ShadowReport/误入率 recompute；依赖 T1404、T1408）
- [x] T1416 [US2] 实现唯一入口 evaluate_candidate（core/deployment/auto_deploy.py 的入口部分：manual 只落快照 / shadow 落快照 + 影子事件 / auto 交判定后路径）+ dreaming 轮次收口接线（一处）+ CLI 雏形（manual/shadow 行为可演示）

**检查点**: 影子模式成立（判定照跑、指针不动、报告可读）——门禁前置成立

---

## 阶段 5：用户故事 3 - 自动部署、人工抽检与回滚（优先级：P3）

**目标**: 自动部署执行 + 渐进抽检 + 否决回滚（三件事）+ 漂移回滚评估

**独立测试**: 部署留痕与历史零修改；抽检渐进；否决三件事与失败路径

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T1419 [P] [US3] tests/unit/test_auto_deploy.py（C7：正常部署（指针更新 + 事件留痕 + 历史节点零修改）、**指针与留痕不一致 → 拒绝 + 告警**、同周期多候选按 reward 择一、非 eligible 不部署）
- [x] T1420 [P] [US3] tests/unit/test_spot_check_rollback.py（C8/C9：前 first_n 次全量产任务、之后按比例、长期未复核告警（不自动通过）、**否决三件事同时生效（机检三断言）**、回滚目标缺失 → 显式报错 + 保持人工、清标记后仍需影子期满足）
- [x] T1421 [P] [US3] tests/unit/test_deploy_drift_assessment.py（C10：部署后漂移 → 回滚评估记录落盘、指针不动）
- [x] T1425 [P] [US3] tests/contract/test_deployment_contracts.py 的 auto-deploy-spotcheck 部分（C6~C10 聚合）

### 用户故事 3 的实现

- [x] T1422 [US3] 实现 core/deployment/auto_deploy.py 的 auto_deploy（指针一致性检测 + 定点改写 core/yaml_edit + 留痕；**谱系 `source=auto` 的落点 = 部署事件留痕引用候选版本（`deployment/deploys/`），不重写 005 的 meta.json**（其只增不改，与 009/011 的采纳留痕同款口径）；依赖 T1415、T1416）
- [x] T1423 [US3] 实现 core/deployment/spot_check.py（渐进抽检 + veto_and_rollback 三件事逻辑事务 + 漂移回滚评估）
- [x] T1424 [US3] 实现 ops/deploy.py CLI（mode / evaluate / shadow-report / spot-check / veto / assess-drift；风格对齐 ops/calibrate.py）

**检查点**: 自动部署 + 抽检 + 回滚全通——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [x] T1426 端到端演示 ops/demo_deploy_gate.py（quickstart 六步：判定矩阵 → 影子（指针不变）→ 影子门禁拒绝 → auto 部署 → 渐进抽检 + 否决回滚三件事 → 误入率重算；退出码 0，确定性夹具 + 临时目录）
- [x] T1427 运行 quickstart.md 全部验证步骤并记录（验证记录回填；覆盖率 ≥85% 复核；命令与 ci.yml 逐字一致）
- [x] T1428 [P] 更新 README.md（部署自动化用法：模式切换/影子报告/抽检/回滚；**明确真实 2 周影子期属运营**）与 docs/二期立项书.md 里程碑表（F9 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T1403→T1404 一链；T1405→T1406、T1407→T1408 并行
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的判定与快照（影子事件需判定结果）；US3 依赖 US2 的唯一入口与模式状态机
- **打磨（阶段 6）**: T1426 依赖全部故事；T1428 可在基础完成后开始

### 并行机会

- 阶段 2：三条链并行
- US1：T1409/T1410 并行；US2：T1413/T1414 并行；US3：T1419/T1420/T1421 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：门槛判定 + 证据快照（可机检的放行标准）
3. **停下并验证**：组合矩阵与优先级、缺证据拦截

### 增量交付

1. 搭建 + 基础 → 模型/配置/模式就绪
2. US1 → 门槛判定（MVP）
3. US2 → 影子模式（上线前置）
4. US3 → 自动部署 + 抽检 + 回滚（里程碑验收线）
5. 阶段 6 → demo/文档

---

## 备注

- 宪章原则六新条款落点（本特性核心）：T1410（门槛三要件 + 缺证据拦截 + 禁止名单）、T1414（影子期门禁）、T1420（否决三件事 + 失败路径保持人工）、T1413（误入率可重算）
- 澄清决议落点：无偏性前置（T1409/T1411）；渐进抽检（T1420/T1423）；轮次收口触发与唯一入口三行为（T1416）
- 指针一致性检测（T1419/T1422）防外部绕过：指针与 deployment/deploys 留痕不一致即拒绝 + 告警
- dreaming 接线仅一处（T1416）：轮次收口后调 evaluate_candidate；**不得**在 dreaming 内实现部署逻辑（原则五单向依赖：dreaming → core）
- 真实 2 周影子期属运营：本特性交付机制、计时与对照报告；README 明确（T1428）
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
