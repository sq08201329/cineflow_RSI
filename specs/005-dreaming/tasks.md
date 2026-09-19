# 任务列表：做梦层与谱系报表

**输入**: 来自 `specs/005-dreaming/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章（原则一~六均相关）；功能 001-004 已交付（回放/沙箱/网关/双 Agent 闭环）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%（core + agents + dreaming 口径）；塌缩检测双向断言；里程碑验收线 = 连续 5 轮无塌缩。

**组织方式**: 按用户故事分组（US1 生成打分 → US2 筛选审批 → US3 谱系曲线）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T401 创建 dreaming/ 包骨架与 configs/movie.yaml 追加 dreaming 段（candidates_per_round=128、recent_k、lambda=0.5、epsilon_random=0.1、validation_top_ratio=0.2、collapse_window=3、collapse_threshold=0.7、replay_parallelism=1）
- [x] T402 [P] conftest 夹具扩展（多棵不同时间树池工厂、冠军策略源码工厂、确定性变异器种子）

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T403 reward 测试 tests/unit/test_reward.py（先写：梯形归一化口径、单点/空曲线边界、并行惩罚、λ 注入）
- [x] T404 实现 dreaming/reward.py（pareto_auc + RewardBreakdown；依赖 T403）
- [x] T405 [P] digest 测试 tests/unit/test_digest.py（最近 K 轮摘要、哈希可复核、空历史首轮）
- [x] T406 [P] 实现 dreaming/digest.py
- [x] T407 [P] 谱系元数据测试 tests/unit/test_lineage_meta.py（meta.json schema、幂等落盘、两源冲突报错）
- [x] T408 [P] 实现谱系元数据读写（dreaming/lineage.py 的 meta 读写部分）

**检查点**: reward/digest/meta 三件套单测通过——做梦管线可开始

---

## 阶段 3：用户故事 1 - 候选生成与沙箱打分（优先级：P1）🎯 MVP

**目标**: M 套候选 → 静态检查 100% 拦截 → 沙箱全池回放 → reward 排名

**独立测试**: 确定性变异器 + 池夹具跑一轮做梦逐断言

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T409 [P] [US1] tests/unit/test_candidates.py（Mutator 确定性/逐字节可复现/候选互异；LLMGenerator 网关计费 + 代码块切分 + 网关失败不重试）
- [x] T410 [US1] tests/unit/test_dreaming_pipeline.py（M 套流程、违规 0 回放、哈希去重、reward 排名正确、零生成审计断言、failed_all_rejected/failed_all_unknown 状态）

### 用户故事 1 的实现

- [x] T411 [P] [US1] 实现 dreaming/candidates.py（CandidateGenerator 协议 + MutatorGenerator + LLMGenerator；依赖 T406）
- [x] T412 [US1] 实现 dreaming/pipeline.py（生成 → 静态检查 → 沙箱回放 → reward 排名；依赖 T404、T408、T411，复用 002 static_check/run_policy）

**检查点**: 一轮做梦（演示档 M=8）沙箱回放排名产出

---

## 阶段 4：用户故事 2 - 防过拟合与人工审批（优先级：P2）

**目标**: train/validation 筛选 + 审批闸门 + 部署指针

**独立测试**: 过拟合注入用例与审批三路径

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T413 [P] [US2] tests/unit/test_overfit.py（最近树永远只 validation、前 20% 判定、首轮跳过注明、过拟合 100% 丢弃、泛化不误判）
- [x] T414 [P] [US2] tests/unit/test_approve.py（审批单字段、approve/reject 落盘、部署指针仅 approved 可更新（SC-005 机检）、拒绝后指针不变）

### 用户故事 2 的实现

- [x] T415 [US2] 实现 dreaming/overfit.py（分树 + 判定纯函数）
- [x] T416 [US2] 实现 dreaming/approve.py（审批单生成 + decide + 部署指针；依赖 T408、T415）

**检查点**: 筛选与审批门禁成立

---

## 阶段 5：用户故事 3 - 谱系报表与进化曲线验收（优先级：P3）

**目标**: 全链路谱系 JSON + 逐轮 reward 曲线 + 塌缩检测（里程碑验收线）

**独立测试**: 谱系汇聚、曲线、塌缩双向断言

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T417 [P] [US3] tests/unit/test_lineage.py（两源汇聚、父/树/子字段完整、冲突报错、epsilon_random 标记）
- [x] T418 [P] [US3] tests/unit/test_collapse.py（正常序列不误报、注入塌缩 100% 告警并指明起始轮、轮数 <window 不塌缩）

### 用户故事 3 的实现

- [x] T419 [US3] 实现 dreaming/lineage.py 完整版（build_lineage + build_curve + detect_collapse；依赖 T408）

**检查点**: 谱系报表与曲线可产出

---

## 阶段 6：打磨与横切关注点

- [x] T420 [US1-US3] 做梦端到端集成测试 tests/integration/test_dreaming_e2e.py（真实沙箱回放候选一轮做梦全流程；Docker 可用时真实执行）
- [x] T421 [P] 实现端到端演示 ops/demo_dreaming.py（5 轮做梦演示档 M=8 → 审批 → 进化曲线 + 谱系报表；断言 collapse=false、零生成、LLM 入账；本地真实执行退出码 0）
- [x] T424 [US1] M=128 全量档计时基准 tests/integration/test_dreaming_benchmark.py（SC-001：一轮做梦 M=128 全池沙箱回放全程 < 30 分钟断言；本地 Docker 真实执行一次并记录实测耗时）
- [x] T425 对齐 003 进化报告口径（F1）：agents/promo/report.py 的 pareto_auc 复用 dreaming/reward.py 的梯形归一化实现 + 口径回归测试
- [x] T422 运行 quickstart.md 全部验证步骤并记录结果
- [x] T423 [P] 更新 README.md（做梦层用法、审批操作说明、一期里程碑全景）；同步 pyproject.toml 的 coverage source 加 "dreaming"（本地与 CI 口径一致）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: 依赖搭建（T403/T404 与 T405/T406 与 T407/T408 三链可并行）——阻塞所有用户故事
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的 DreamRound 产出；US3 依赖 US2 的谱系落盘
- **打磨（阶段 6）**: T420–T422、T424 依赖全部故事完成；T425 依赖 T404（reward.py 权威口径）；T423 可在基础完成后开始

### 并行机会

- 阶段 2：三条测试/实现链并行
- US1：T409 与 T410 可并行起步（T410 依赖 T411 实现后联调）
- US2：T413、T414 并行；US3：T417、T418 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：一轮做梦候选打分排名
3. **停下并验证**：静态拦截与 reward 口径正确

### 增量交付

1. 搭建 + 基础 → reward/digest/meta 就绪
2. US1 → 做梦本体（MVP）
3. US2 → 筛选与审批闸门
4. US3 → 谱系与曲线（里程碑验收线）
5. 阶段 6 → 5 轮演示与文档，一期收官

---

## 备注

- 宪章约束落点：静态检查前置（T410/T412）、零生成回放（T410）、人工 approve 闸门（T414/T416）、谱系全链路（T417/T419）、配置化参数（T401）、口径一致性（T425）、全量计时（T424）
- 谱系元数据与做梦轮次均文件化（meta.json / dreaming/history/*.json），不建 DB 表（research 决策 3）
- ε=0.1 随机预算一期仅配置校验 + 谱系标记，执行器接线不在本期（spec FR-011 注）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
