# 任务列表：短剧形态试水作品（形态配置 + 链式交接 + 端到端可复现样片）

**输入**: 来自 `specs/015-pilot-shortdrama/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（**原则五为核心**：形态全配置零代码、自研轻量 DAG、执行器零业务概念）；功能 001~014 已交付；规格含 2026-09-21 澄清三条（候选重试语义、样片包交付物、执行器分层）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；**静态断言本特性密度最高**（无形态分支 / 执行器零业务概念）；**本地验证命令与 ci.yml 逐字一致**（`ruff check .` + `ruff format --check .`）。

**组织方式**: 按用户故事分组（US1 形态配置 → US2 链式交接 → US3 端到端运行与样片包）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T1501 创建 core/orchestration/ 与 agents/pilot/ 包骨架、pilot/ 数据目录（runs/packages + .gitkeep）、configs/shortdrama.yaml 初版（form: shortdrama + 覆盖全部加载器所需段：evaluator_weights 各 Agent、各 Agent 段、replay/pooling/dreaming/calibration/drift/deployment/web；形态差异：权重与阈值、短剧节奏基准曲线（前段权重上调）、外环日级、预算与并行度下调、竖屏 1~3 分钟规格）
- [x] T1502 [P] conftest 夹具扩展：素材夹具（剧本/ShotList/片段/音轨/成片的最小可用产物）、形态配置夹具（movie 精简副本 + shortdrama）、pilot 临时目录、阶段执行入口桩；不改坏既有夹具

## 阶段 2：基础（core/orchestration 通用执行器）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T1503 模型测试 tests/unit/test_orchestration_models.py（先写：StageSpec/StageState/RunRecord 字段与校验——状态枚举合法迁移、产物引用非空约束、指纹格式）
- [x] T1504 [P] 实现 core/orchestration/models.py
- [x] T1505 [P] DAG 测试 tests/unit/test_orchestration_dag.py（C1：拓扑序正确、环依赖拒绝、依赖不存在拒绝、stage_id 重复拒绝）
- [x] T1506 [P] 实现 core/orchestration/dag.py（轻量 DAG，零业务概念）
- [x] T1507 [P] 执行器测试 tests/unit/test_orchestration_executor.py（C2/C3：阶段状态机、失败后其后 skipped、**断点续跑不重跑已完成阶段（调用计数机检）**、输入指纹不一致拒绝续跑、完成后续跑幂等、**执行器代码零环节/形态字面量（静态断言）**）
- [x] T1508 [P] 实现 core/orchestration/executor.py（阶段执行 + 状态机 + 断点续跑 + 指纹校验）
- [x] T1509 [P] 账目测试 tests/unit/test_orchestration_ledger.py（C4：按 stage/形态汇总 == 各 Agent 成本之和；不一致即报错）
- [x] T1510 [P] 实现 core/orchestration/ledger.py

**检查点**: 通用执行器四件套通过（含零业务概念静态断言）——用户故事可开始

---

## 阶段 3：用户故事 1 - 短剧形态配置与零代码切换（优先级：P1）🎯 MVP

**目标**: 短剧配置过全部加载器 + 同链双形态差异可归因配置 + 静态扫描零形态分支

**独立测试**: 配置完整性红→绿驱动补齐；双配置运行差异逐项归因

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T1511 [P] [US1] tests/unit/test_config_integrity.py（shortdrama.yaml 必须通过**全部**加载器：各 Agent `*Config.from_yaml` + replay/pooling/dreaming/calibration/drift/deployment/web 段解析；缺项即红——这是零代码切换的真实检验）
- [x] T1512 [P] [US1] tests/unit/test_form_switch.py（两套配置同链运行均成功、差异逐项可归因配置（权重/阈值/基准曲线/预算/规格）；**静态断言：core/ 与 agents/ 代码（除 agents/pilot 的配置读取）无 `shortdrama` 字面量分支或 `form ==` 判断**）

### 用户故事 1 的实现

- [x] T1513 [US1] 依 T1511/T1512 驱动补齐 configs/shortdrama.yaml 至全部加载器通过（短剧节奏基准曲线含前段权重上调；外环日级；预算与并行度下调；竖屏规格）

**检查点**: 短剧配置可加载 + 双形态零代码切换成立——MVP 成立

---

## 阶段 4：用户故事 2 - 六环节链式交接（优先级：P2）

**目标**: 四段交接纯映射 + 双向快照断言 + 上游不合格下游拒绝

**独立测试**: 四段字段集双向一致；拒绝语义

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T1514 [P] [US2] tests/unit/test_handoffs.py（C5~C8：剧本→ScriptSegment（复用 009 export_segment）双向字段集一致；ShotList→视觉生成参数（镜头数 == 参数数）；视觉+声音→剪辑输入（镜头库条目一致、含/无音轨两路径）；成片→宣发物料；各下游校验通过）
- [x] T1516 [P] [US2] tests/contract/test_pilot_contracts.py 的 handoffs 段（C5~C9 聚合，含上游 FAILED → 下游执行计数 0 的拒绝语义断言）

### 用户故事 2 的实现

- [x] T1515 [US2] 实现 agents/pilot/handoffs.py（四段纯映射函数 + 双向字段集断言辅助）

**检查点**: 四段交接双向锁定 + 拒绝语义成立

---

## 阶段 5：用户故事 3 - 端到端编排与可复现样片（优先级：P3）

**目标**: 六阶段定义 + 试水运行编排（预检/断点续跑/可复现）+ 样片包 + 账目对账 + CLI + 升级路径

**独立测试**: 一次运行产出样片包；两次运行逐字节一致；续跑不重跑；账目零差异

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T1517 [P] [US3] tests/unit/test_pilot_stages.py（六阶段 StageSpec：依赖 script→storyboard→visual→sound→editing→promo、执行入口接线、候选重试语义（环节内换候选、全败才 failed 并记录全部判 0 理由））
- [x] T1519 [P] [US3] tests/unit/test_pilot_run.py（C10：启动前预检拒绝（输入不足/配置缺项 → 零成本零落树）；一次运行六阶段 done；**同输入同配置两次运行逐字节一致**；断点续跑不重跑；输入/配置变更后拒绝续跑）
- [x] T1521 [P] [US3] tests/unit/test_pilot_package.py（C11/C12：五件套齐备 + manifest 含**"模拟生成"标注** + 配置指纹；缺件即装配失败；账目对账零差异、篡改即报错）

### 用户故事 3 的实现

- [x] T1518 [US3] 实现 agents/pilot/stages.py（六阶段定义 + 各 Agent 既有 loop 入口接线）
- [x] T1520 [US3] 实现 agents/pilot/pilot.py（预检 → build_dag → executor.run → 样片包；run/resume 两入口；依赖 T1504~T1510、T1515、T1518）
- [x] T1522 [US3] 实现 agents/pilot/package.py（manifest + reel + products + cost + state 五件套装配）
- [ ] T1523 [US3] 实现 ops/pilot.py CLI（run / resume / inspect）+ docs 升级路径文档（B/C 凭证清单、预算口径、切换方式）+ 结构化清单（凭证环境变量名/适配器类/切换命令，字段可机检）

**检查点**: 一次运行产出可复现样片包 + 账目对账——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T1524 契约聚合补全 tests/contract/test_pilot_contracts.py 的 orchestration 段（C1~C4）与 pilot-run 段（C10~C13），与 handoffs 段合成 C1~C13 全量；**并补两条宪章级机检**：①**FR-011 落树路径守卫**——静态断言 `core/orchestration/` 与 `agents/pilot/` 不直接调用树写入 API（落树只经各 Agent 既有 loop 入口）+ 运行期树写入计数 == 各 Agent 入口写入计数（审计断言）；②**SC-005 依赖清单机检**——扫描 `pyproject.toml`/`uv.lock` 断言无 Airflow 类外部编排框架依赖
- [ ] T1525 端到端演示 ops/demo_pilot.py（quickstart 六步：配置完整性 → 短剧运行出样片包 → 可复现对照 → movie 对照（零代码切换）→ 断点续跑 → 拒绝语义；退出码 0，确定性夹具 + 临时目录）
- [ ] T1526 运行 quickstart.md 全部验证命令并记录（验证记录回填；覆盖率 ≥85% 复核；命令与 ci.yml 逐字一致）
- [ ] T1527 [P] 更新 README.md（试水运行用法 + **"模拟生成"边界说明**）与 docs/二期立项书.md 里程碑表（周 11~12 试水作品 A 路径已交付注明；真实作品留运营）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T1503→T1504 一链；T1505→T1506、T1507→T1508、T1509→T1510 并行
- **用户故事（阶段 3+）**: US1 与 US2 相互独立（配置 / 交接），可并行；US3 依赖前两者（需要可加载配置与交接函数）
- **打磨（阶段 6）**: T1524/T1525 依赖全部故事；T1527 可在基础完成后开始

### 并行机会

- 阶段 2：四条链并行
- US1：T1511/T1512 并行；US2：T1514/T1516 并行
- US3：T1517/T1519/T1521 三测试并行；T1518、T1520、T1522 顺序实现（依赖链）

---

## 实现策略

### MVP 优先（用户故事 1 + 通用执行器）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：短剧配置过全部加载器 + 双形态零代码切换
3. **停下并验证**：配置完整性、静态断言、差异归因

### 增量交付

1. 搭建 + 基础 → 通用执行器就绪（可复用基建）
2. US1 → 形态配置（MVP）
3. US2 → 四段交接
4. US3 → 端到端运行与样片包（里程碑验收线）
5. 阶段 6 → 契约聚合/demo/文档

---

## 备注

- 宪章原则五落点（本特性核心）：T1513（形态全配置）+ T1512（静态断言无形态分支）+ T1507/T1508（执行器零业务概念静态断言）+ T1506（自研 DAG，禁 Airflow——依赖清单机检）
- 澄清决议落点：候选重试与终止语义（T1517/T1518）；样片包交付形态（T1521/T1522）；执行器分层（T1504~T1510 归 core，T1515~T1522 归 agents/pilot）
- 诚实边界：样片包与清单强制"模拟生成"标注（T1521）；不使用版权素材（夹具为合成内容）；升级路径文档化（T1523）
- 既有门禁不被削弱：各阶段调用各 Agent 既有 loop（预算/幂等/落树/评估器版本全沿用）；编排不新增落树路径（T1518/T1520）
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
