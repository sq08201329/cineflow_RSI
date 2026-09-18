# 任务列表：宣发 Agent 全闭环

**输入**: 来自 `specs/003-promo-loop/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章（原则一/二/三/六直接相关）；功能 001（树/评估器）、002（回放/沙箱）已交付

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%（本期口径扩为 core + agents）；适配器契约套件双实现同跑。

**组织方式**: 按用户故事分组（US1 探索闭环 → US2 回放验证 → US3 进化报告）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [ ] T201 创建目录：agents/promo/{evaluators,platform}、core/llm_gateway/backends/、tests/contract/、policies/history/promo/
- [ ] T202 [P] configs/movie.yaml 追加 promo 段（exploration_per_round_usd、promo_pilot_ratio、物料规格、敏感词库、模型价目表、模拟平台分布参数）

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [ ] T203 迁移 0002：可变运营表 promo_campaigns（ops/migrations/versions/0002_promo_campaigns.py；唯一键 (round_id, material_id)；不加 immutable 触发器）
- [ ] T204 网关测试 tests/unit/test_llm_gateway.py（计费入账、缓存命中零成本、重试退避、缺价目报错、Mock 确定性）——先写确认失败
- [ ] T205 网关实现 core/llm_gateway/gateway.py + backends/mock.py + backends/http.py（骨架，凭证环境变量注入；契约见 contracts/llm-gateway.md）
- [ ] T206 [P] 测试夹具扩展 tests/conftest.py（运营表引擎夹具、SimulatedPlatform 工厂、Mock 网关工厂）

**检查点**: 迁移可执行、网关可用——用户故事可开始

---

## 阶段 3：用户故事 1 - 宣发线上探索闭环（优先级：P1）🎯 MVP

**目标**: 生成 → 合规门禁 → 预算门禁 → 投放 → 回流 → 一次性落盘冻结 → 成本对账，轮次幂等

**独立测试**: 模拟平台一轮闭环 + 审计对账

### 用户故事 1 的测试（先写，确认失败后再实现）

- [ ] T207 [P] [US1] 合规评估器单测 tests/unit/test_promo_compliance.py（尺寸/时长/敏感词拦截；缺配置拒投而非放行）
- [ ] T208 [P] [US1] CTR 估计器单测 tests/unit/test_ctr.py（分桶贝塔平滑、稀疏桶回退先验、快照哈希版本化、确定性）
- [ ] T209 [P] [US1] 平台真值评估器单测 tests/unit/test_platform_metrics.py（归一化合成、越界指标拒绝、写入即冻结语义）
- [ ] T210 [US1] 执行器单测 tests/unit/test_promo_loop.py（2% 边界含最小货币单位、事务扣减防双花、重复触发幂等、FAILED 成本入账、对账一致）
- [ ] T211 [US1] 适配器契约套件 tests/contract/test_platform_adapter.py（花费上限/幂等键/状态机/指标 schema/错误映射；双实现同跑，真实实现无凭证跳过）

### 用户故事 1 的实现

- [ ] T212 [P] [US1] 实现 agents/promo/evaluators/compliance.py（rule.material_compliance@1.0.0 注册）
- [ ] T213 [P] [US1] 实现 agents/promo/evaluators/ctr.py（proxy.ctr_history，分桶平滑 + 快照版本化）
- [ ] T214 [P] [US1] 实现 agents/promo/evaluators/platform_metrics.py（human.platform_metrics，真值归一化）
- [ ] T215 [P] [US1] 实现 agents/promo/platform/base.py + simulated.py（PromoMaterial/Campaign/MetricSnapshot 模型 + 确定性模拟平台 + 内部账本）
- [ ] T216 [US1] 实现 agents/promo/material.py（网关生成物料 → 工件内容寻址落库；依赖 T205、T215）
- [ ] T217 [US1] 实现 agents/promo/loop.py（执行器：门禁/幂等/状态机/对账/RoundResult；依赖 T212-T216、T203）
- [ ] T218 [US1] 实现 ops/ingest_metrics.py（回流管道：快照校验 → 一次性完整节点 INSERT 冻结；依赖 T217）
- [ ] T219 [P] [US1] 实现 agents/promo/platform/http_real.py（契约同构真实适配器骨架，凭证注入）

**检查点**: 模拟平台一轮闭环跑通，对账一致、幂等成立

---

## 阶段 4：用户故事 2 - 树冻结入池与回放验证（优先级：P2）

**目标**: 轮次树显式冻结入池；真值回放零生成；对接无偏性验收

**独立测试**: 冻结树入池 + 参考策略回放命中真实回流节点

### 用户故事 2 的测试与实现

- [ ] T220 [US2] 集成测试 tests/integration/test_promo_replay.py（先写：冻结校验、真值回放得分即冻结常数、生成调用恒 0、τ 报告对接 002 门禁语义；PG 可用时真实执行，否则 SQLite）
- [ ] T221 [US2] 实现轮次树冻结入口（loop.py 增加 freeze_round_tree：config_snapshot 冻结评估器版本组合与权重 → 复用 002 pool 入池；依赖 T217）

**检查点**: 真实回流数据可回放，无偏性链路可用

---

## 阶段 5：用户故事 3 - 首轮进化曲线产出（优先级：P3）

**目标**: 基线 vs 变体策略回放对比，EvolutionReport JSON

**独立测试**: 双版本回放报告字段完整、谱系可追溯

### 用户故事 3 的测试与实现

- [ ] T222 [US3] 报告单测 tests/unit/test_evolution_report.py（先写：双曲线、成本、pareto_auc/并行惩罚分量、verdict、谱系引用）
- [ ] T223 [P] [US3] 手写基线与变体策略 policies/history/promo/（经 002 versioning 落版本号；两策略探索参数不同）
- [ ] T224 [US3] 实现 agents/promo/report.py（复用 002 模拟器池回放两版本 → EvolutionReport；依赖 T221、T223）

**检查点**: 首轮进化曲线产出（里程碑验收）

---

## 阶段 6：打磨与横切关注点

- [ ] T225 [P] CI 更新 .github/workflows/ci.yml：新增 contract job（tests/contract）；覆盖率口径加 agents（--cov=core --cov=agents --cov-fail-under=85）
- [ ] T226 [P] 实现端到端演示 ops/demo_promo_loop.py（quickstart 验证 3 全流程；本地真实执行，退出码 0）
- [ ] T227 运行 quickstart.md 全部验证步骤并记录结果
- [ ] T228 [P] 更新 README.md（宣发闭环用法、适配器凭证配置说明、门禁清单现状）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: 依赖搭建完成（T204 先测试、T205 实现；T206 可并行）——阻塞所有用户故事
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的树产出（T217/T218）；US3 依赖 US2 的池
- **打磨（阶段 6）**: T226/T227 依赖全部故事；T225/T228 可在基础完成后开始

### 用户故事依赖

- **US1（P1）**: 闭环本体，不依赖其他故事
- **US2（P2）**: 依赖 US1 的轮次树（两段式落盘产物）
- **US3（P3）**: 依赖 US2 的池与两手写策略版本（T223 可提前并行）

### 并行机会

- 阶段 2：T203 与 T204/T206 并行
- US1：T207–T209 测试并行；T212–T215 实现并行
- US3：T223 与 US1/US2 任意任务并行
- 打磨：T225、T226、T228 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：模拟平台闭环 + 幂等 + 对账
3. **停下并验证**：一轮探索端到端、重复触发零重复扣费
4. 即得"真实评估数据开始积累"的最小价值形态

### 增量交付

1. 搭建 + 基础 → 网关与运营表就绪
2. US1 → 探索闭环（MVP）
3. US2 → 回放验证（真实真值进模拟器池）
4. US3 → 首轮进化曲线（里程碑验收）
5. 阶段 6 → CI 与演示就绪

---

## 备注

- 宪章约束落点：版本冻结（T207-T209/T212-T214）、immutable 两段式落盘（T203/T218）、LLM 全走网关（T205/T216）、预算门禁（T210/T217）、单向依赖（agents/promo 只 import core）
- 真实渠道无凭证不假装投放（T219 契约骨架 + 契约套件跳过语义；宪章原则六）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
