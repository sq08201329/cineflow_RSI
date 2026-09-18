# 任务列表：发现树与评估器框架（L1 基建第一批）

**输入**: 来自 `specs/001-tree-evaluators/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章（原则一/二为不可协商门禁）、spec.md 三个用户故事（P1→P3）

**测试说明**: 本项目宪章要求 TDD 与覆盖率 ≥85%（里程碑门禁），因此每个用户故事**必须**先写测试并确认失败后再实现。

**组织方式**: 任务按用户故事分组，每个故事可独立实现、独立测试、独立交付。

## 格式：`[ID] [P] [Story] 描述`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 属于哪个用户故事（US1/US2/US3）
- 描述中包含具体文件路径

## 阶段 1：搭建（共享基础设施）

**目的**: 项目初始化与基本结构

- [x] T001 按 plan.md 创建目录结构：core/tree/、core/evaluators/、configs/、tests/unit/、tests/integration/、ops/migrations/
- [x] T002 用 uv 初始化 Python 3.11+ 项目，pyproject.toml 声明依赖：sqlalchemy>=2.0、psycopg[binary]、alembic、boto3、blake3、uuid-utils、pyyaml；dev 依赖：pytest、pytest-cov
- [x] T003 [P] 配置 pytest（pyproject.toml：markers `integration`、testpaths、coverage 源=core）
- [x] T004 [P] 配置 ruff（lint + format）

---

## 阶段 2：基础（阻塞性前置条件）

**目的**: 所有用户故事共用的基础设施

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T005 创建错误类型体系 core/tree/errors.py（TreeStoreError 基类 → ValidationError / DuplicateError / NotFoundError / ImmutableViolationError，见 contracts/tree-store.md）
- [x] T006 [P] 创建 ops/dev.compose.yml（PostgreSQL 16 + MinIO 开发依赖，见 research.md 决策 5）
- [x] T007 初始化 Alembic 迁移框架 ops/migrations/（env.py 指向 PG；迁移账号与应用账号分离，应用账号执行 REVOKE）
- [x] T008 编写首个迁移：tree_nodes / discovery_trees 表 + reject_mutation() 触发器 + REVOKE UPDATE,DELETE（DDL 见 data-model.md §2；依赖 T007）
- [x] T009 [P] 创建 configs/movie.yaml 最小骨架（evaluator_weights 占位，供 US3 读取）
- [x] T010 [P] 创建测试公共夹具 tests/conftest.py（SQLite 内存引擎、临时目录 LocalArtifactStore 的 fixture；桩评估器不在此定义，唯一定义来源见 T025 tests/stubs.py）

**检查点**: 迁移可执行、触发器生效、测试夹具可用——用户故事可开始

---

## 阶段 3：用户故事 1 - 不可变的发现树记录与谱系查询（优先级：P1）🎯 MVP

**目标**: 探索尝试以不可变节点落盘，工件内容寻址去重，谱系按（项目, Agent, 策略版本）可查

**独立测试**: 追加节点后修改/删除历史节点必须被拒；三维过滤查询正确；失败节点成本完整

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T011 [P] [US1] 领域模型单测 tests/unit/test_tree_models.py（frozen 不可改、score/status 一致性、depth 递推校验、PLANNED 拒落盘）
- [x] T012 [P] [US1] TreeStore 契约单测 tests/unit/test_tree_store.py（SQLite：append/get/children/trees_by/nodes_of；eval_breakdown 键格式校验 FR-009；错误语义按契约表）
- [x] T013 [P] [US1] ArtifactStore 单测 tests/unit/test_artifact_store.py（Local 实现：内容寻址、幂等去重、未命中 ArtifactNotFoundError、内容不符 ArtifactCorruptedError）
- [x] T014 [US1] immutable 集成测试 tests/integration/test_immutability.py（Docker PG：UPDATE/DELETE 被触发器与权限双重拒绝；随机抽样节点重算 score 与落盘一致）
- [x] T015 [US1] PG 行为集成测试 tests/integration/test_tree_store_pg.py（jsonb 读写、三维索引过滤正确性、3 万节点基准（真实分支形态，分支因子 10）：append p99 < 50ms、children p99 < 100ms）

### 用户故事 1 的实现

- [x] T016 [P] [US1] 实现领域模型 core/tree/models.py（NodeStatus/CostRecord/TreeNode/DiscoveryTree，校验规则见 data-model.md §1）
- [x] T017 [P] [US1] 实现表定义 core/tree/db.py（SQLAlchemy Core Table + 索引，对齐迁移 DDL）
- [x] T018 [P] [US1] 实现内容寻址存储 core/tree/artifacts.py（blake3 哈希 + LocalArtifactStore + S3ArtifactStore）
- [x] T019 [US1] 实现 TreeStore core/tree/store.py（追加/读取/谱系查询，存储层异常转换为 ImmutableViolationError；依赖 T016、T017）
- [x] T020 [US1] US1 检查点验证：全部 US1 测试通过且以 frozen/触发器两条路径各自证明不可变

**检查点**: US1 完整可运行、可独立演示（探索全程可追溯）

---

## 阶段 4：用户故事 2 - 评估器注册与版本冻结（优先级：P2）

**目标**: 评估器以 `evaluator_id@version` 唯一注册；非确定性评估器（human 锚点除外）被拒

**独立测试**: 重复注册报错；非确定性注册被拒；human 例外放行；evaluate/compare 返回合规值

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T021 [P] [US2] 评估器模型单测 tests/unit/test_evaluator_models.py（EvaluatorSpec 必填校验、EvalResult score ∈ [0,1] 校验）
- [x] T022 [P] [US2] 注册中心单测 tests/unit/test_registry.py（契约表全行覆盖：重复键、非确定性、human 例外、缺字段、get 未命中含可用版本列表）

### 用户故事 2 的实现

- [x] T023 [P] [US2] 实现 core/evaluators/base.py（EvaluatorKind/EvaluatorSpec/EvalResult/ArtifactRef/Evaluator 抽象基类）
- [x] T024 [US2] 实现注册中心 core/evaluators/registry.py（register/get/list_all + RegistrationError；依赖 T023）
- [x] T025 [US2] 创建确定性桩评估器 tests/stubs.py（rule/proxy/judge/human 各一 + 非确定性反例；桩评估器的唯一定义来源，conftest 仅做 fixture 包装；供 US2/US3 与集成测试共用）

**检查点**: US1、US2 各自独立工作

---

## 阶段 5：用户故事 3 - 合成评分与硬规则门禁（优先级：P3）

**目标**: 多评估器结果按形态配置权重合成总分；硬规则 0 分即总分 0；权重快照随树冻结

**独立测试**: 桩评估器构造 breakdown——验证门禁语义、加权和、键不匹配拒绝、快照冻结

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T026 [P] [US3] 合成评分单测 tests/unit/test_composite.py（gate 语义、加权求和、WeightMismatchError、不做隐式归一化）
- [x] T027 [P] [US3] 权重读取单测 tests/unit/test_weights.py（从 configs/movie.yaml 读取、缺键报错、与快照冻结配合）

### 用户故事 3 的实现

- [x] T028 [US3] 实现 core/evaluators/composite.py（composite_score：硬规则门禁 + 加权求和 + WeightMismatchError，语义表见 contracts/evaluator-registry.md §3）
- [x] T029 [US3] 实现权重配置读取 core/evaluators/weights.py（读取 configs/*.yaml，产出注入 composite_score 的 weights；依赖 T028、T009）

**检查点**: 三个用户故事全部独立可用，"评估 → 合成 → 落盘"闭环打通

---

## 阶段 6：打磨与横切关注点

**目的**: 验收门禁与跨故事的收尾

- [x] T030 [P] 覆盖率门禁接入 CI（pytest --cov=core --cov-fail-under=85；GitHub Actions 工作流 .github/workflows/ci.yml，集成测试步骤使用 T006 的 compose）
- [x] T031 [P] 实现 immutable 审计脚本 ops/audit_immutable.py（随机抽 100 个历史节点重算 score 与落盘值比对，不一致即非零退出——宪章每日定时任务的最小实现）
- [x] T032 实现端到端演示脚本 ops/demo_tree_eval.py（quickstart.md 验证 3 的六步场景，输出 JSON 报告）
- [x] T035 [US1] 消费方模拟契约测试 tests/integration/test_consumer_contract.py（模拟回放消费方，仅通过 TreeStore 公开接口跑通建树→追加→三维查询→失败节点读取全路径，验证 SC-005）
- [x] T036 [US1] schema 一致性集成断言 tests/integration/test_schema_consistency.py（迁移后的 PG schema 与 core/tree/db.py 的 SQLAlchemy metadata 逐表比对，防迁移 DDL 与 Table 定义双份维护漂移）
- [x] T033 运行 quickstart.md 全部验证步骤并记录结果（覆盖率、集成测试、演示退出码）
- [x] T034 [P] 编写 README.md（开发环境搭建、测试命令、目录约定）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖——可立即开始
- **基础（阶段 2）**: 依赖搭建完成（T008 依赖 T007）——阻塞所有用户故事
- **用户故事（阶段 3+）**: 全部依赖基础阶段完成；可按 P1 → P2 → P3 顺序推进，也可并行
- **打磨（阶段 6）**: T032、T033、T035、T036 依赖全部用户故事完成（T035 依赖 US1 的 TreeStore 公开接口；T036 依赖 T008/T017 的迁移与表定义）；T030、T031、T034 可在基础完成后开始

### 用户故事依赖

- **US1（P1）**: 基础后可开始——不依赖其他故事
- **US2（P2）**: 基础后可开始——与 US1 无代码依赖，可独立测试
- **US3（P3）**: 基础后可开始——`composite_score` 纯函数不依赖 US1/US2；快照冻结演示在打磨阶段（T032）才与 US1 汇合

### 每个用户故事内部

- 测试先写、确认失败，再实现（宪章测试纪律）
- 模型先于存储/注册实现；核心实现先于演示脚本
- 故事完成、检查点验证通过后再进入下一优先级

### 并行机会

- 阶段 1：T003、T004 并行
- 阶段 2：T006、T009、T010 并行
- US1：T011–T013 测试并行；T016–T018 实现并行
- US2：T021、T022 测试并行；T023 与 US1 任意任务并行
- US3：T026、T027 测试并行
- 打磨：T030、T031、T034 并行

## 并行示例

```bash
# 基础完成后，三个故事的首批测试可同时启动：
Task: "T011 [US1] 领域模型单测 tests/unit/test_tree_models.py"
Task: "T021 [US2] 评估器模型单测 tests/unit/test_evaluator_models.py"
Task: "T026 [US3] 合成评分单测 tests/unit/test_composite.py"
```

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1（搭建）+ 阶段 2（基础）
2. 完成阶段 3（US1：树存储 + immutable + 谱系查询）
3. **停下并验证**：T020 检查点——US1 测试全绿、两条不可变路径各自证明
4. 即达成里程碑"探索全程可追溯"的最小可用形态

### 增量交付

1. 搭建 + 基础 → 基础就绪
2. US1 → 检查点验证 → MVP
3. US2 → 检查点验证 → 注册冻结可用
4. US3 → 检查点验证 → 评估闭环
5. 阶段 6 → 门禁与审计就绪，达到周 1~3 里程碑验收（覆盖率 ≥85%）

---

## 备注

- [P] 任务 = 不同文件，无依赖；[Story] 标签用于追溯到 spec.md 用户故事
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
- 宪法约束落点：immutable（T008/T014/T031）、注册冻结（T022/T024）、权重不硬编码（T029）、覆盖率 ≥85%（T030）、公开接口完备性 SC-005（T035）
