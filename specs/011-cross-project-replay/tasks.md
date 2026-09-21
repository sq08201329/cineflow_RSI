# 任务列表：跨项目发现树合并回放（池化复用）

**输入**: 来自 `specs/011-cross-project-replay/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（原则一/二/三/五/六均相关）；功能 001-010 已交付；规格含 2026-09-21 澄清两条（冲突即 UNKNOWN、命中占比判定）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；合并口径无偏性 τ≥0.95 为发布阻塞；**本地验证命令与 ci.yml 逐字一致**（`ruff check .` + `ruff format --check .`）。

**组织方式**: 按用户故事分组（US1 池化构建 → US2 回放与稀释 → US3 谱系与合并验收）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T1001 configs/movie.yaml 追加 replay.pooling 段（min_trees=3、dilution_hit_ratio_threshold=0.7、allow_cross_form=false、enabled_for_dreaming=false）+ replay/pools/ 数据目录约定（.gitkeep）
- [x] T1002 [P] conftest 夹具扩展：多项目树工厂（≥2 项目 × ≥2 棵同 Agent 同形态树，project_id 归属）、评估器版本集变体（同/不同版本集）、**得分冲突变体**（A/B 同结构键同版本集不同得分）、时间重叠变体（同 created_at 多棵）、pooling 临时目录夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T1003 版本分组键测试 tests/unit/test_version_group_key.py（先写：config_snapshot → evaluator_versions_hash 确定性、同语义同 hash、版本差异不同 hash、config 微调同 hash（如实标注不影响分组））
- [x] T1004 [P] 实现版本分组键计算（core/replay/merged_pool.py 内的 evaluator_versions_hash；同输入同 hash）
- [x] T1005 [P] 领域模型测试 tests/unit/test_pooling_models.py（MergedPool/VersionGroup/ScoreConflict/HitDistribution/DilutionAlert/PoolSnapshot/CrossProjectLineage frozen 与校验）
- [x] T1006 [P] 实现 core/replay/pooling_models.py（frozen dataclass：MergedPool/VersionGroup/ScoreConflict/HitDistribution/DilutionAlert/PoolSnapshot/CrossProjectLineage；独立模块，与 core/tree/models.py、core/calibration/models.py 惯例一致）

**检查点**: 版本分组键与模型就绪——用户故事可开始

---

## 阶段 3：用户故事 1 - 跨项目池化构建与分组（优先级：P1）🎯 MVP

**目标**: 按 (Agent, 形态) 分组合并多项目树 + 版本集分组 + 前置条件 + 可重现 + 快照

**独立测试**: 多项目夹具构建：分组/版本分组/前置拒绝/可重现/快照幂等逐断言

### 用户故事 1 的测试（先写，确认失败后再实现）

- [ ] T1007 [P] [US1] tests/unit/test_merged_pool.py（C1 场景 1~5：A/B 各 2 棵 → 4 棵合并归属可追溯、版本不同按版本集分组不混池、树 <3 拒绝注明、同输入两次构建快照哈希一致、跨形态未显式拒绝）
- [ ] T1008 [P] [US1] tests/unit/test_pool_snapshot.py（C2 场景 1~3：首次快照落盘字段齐全、重复构建幂等、dreaming 开关状态与前置判定入快照）

### 用户故事 1 的实现

- [ ] T1009 [US1] 实现 core/replay/merged_pool.py（build_merged_pool：分组 + 版本集分组 + 前置条件 + (created_at, project_id) 稳定排序可重现；依赖 T1004、T1006）
- [ ] T1010 [US1] 实现 core/replay/pool_snapshot.py（persist_pool_snapshot：replay/pools/{agent}/{form}/{pool_id}.json 只增不改幂等；**pool_id = 分组输入的确定性哈希**——同输入即同 id，是幂等落盘的前提）

**检查点**: 合并池构建可重现 + 快照落盘——MVP 成立

---

## 阶段 4：用户故事 2 - 池化回放与稀释控制（优先级：P2）

**目标**: 跨项目匹配（含冲突口径）+ 命中分布双报告 + 稀释告警 + 零生成 + 分树

**独立测试**: 跨项目命中/冲突 UNKNOWN/分布与告警/分树排序逐断言

### 用户故事 2 的测试（先写，确认失败后再实现）

- [ ] T1011 [P] [US2] tests/unit/test_cross_match.py（C3 场景 1~4：A 有 B 无 → 跨项目命中、A/B 同分 → 命中、**A/B 不同分 → UNKNOWN + ScoreConflict（树清单/得分/归属齐全）**、版本集不同不命中）
- [ ] T1012 [P] [US2] tests/unit/test_hit_stats.py（C4 场景 1~3：A 命中 8/B 命中 2 → 双报告 + A 占比 0.8>0.7 告警（命中占比判定、树数占比参考同报告）、单项目构成 → 占比 1.0 告警 + 标注、conflicts 字段含 ScoreConflict）
- [ ] T1013 [P] [US2] tests/unit/test_pooling_replay.py（C5：回放全程生成/渲染/网关调用计数 0 审计、跨项目分树全局时间排序（tie-break project_id）、最近树只做 validation）

### 用户故事 2 的实现

- [ ] T1014 [US2] 实现 core/replay/cross_match.py（规范化精确匹配跨项目；冲突 → UNKNOWN + ScoreConflict；不取均值/不取最新/不终止）
- [ ] T1015 [US2] 实现 core/replay/hit_stats.py（per-project 与合并口径统计、命中占比判定 + 树数占比参考、稀释告警）
- [ ] T1016 [US2] 池化回放接线（merged_pool × 002 SimulatorPool 集成：probe/observed 走 cross_match；分树全局排序；依赖 T1009、T1014、T1015）

**检查点**: 跨项目命中 + 冲突 UNKNOWN + 稀释告警成立

---

## 阶段 5：用户故事 3 - 跨项目谱系查询与合并验收（优先级：P3）

**目标**: 谱系跨项目链路 + 同树双池一致性 + 合并口径 τ≥0.95 + 做梦开关默认关闭

**独立测试**: 谱系跨项目查询、同树双池得分一致、合并口径 τ、开关默认关闭逐断言

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T1017 [P] [US3] tests/unit/test_cross_lineage.py（C6：跨项目 policy_version → 树与子版本（归属标注）、单项目版本跨项目字段为空列表而非缺失）
- [ ] T1018 [P] [US3] tests/unit/test_pooling_acceptance.py（C7/C9：同树在单项目池与合并池回放得分序列一致（逐树逐策略）；做梦默认单项目池（开关状态入快照机检）；开关开启但前置条件不足 → 单项目池 + 注明）
- [ ] T1019 [P] [US3] tests/unbiasedness/test_merged_pool_unbiased.py（C8：合并池夹具回放 vs 真实重跑 τ ≥ 0.95；注入偏差 ≥3 形态 100% 拒绝）

### 用户故事 3 的实现

- [ ] T1020 [US3] 实现 core/replay/cross_lineage.py（跨项目谱系查询，三维索引读路径扩展）
- [ ] T1021 [US3] dreaming 侧开关最小改动（读取 replay.pooling.enabled_for_dreaming：true → 合并池，false/前置不足 → 单项目池并注明；不特化 dreaming 逻辑；依赖 T1009）

**检查点**: 谱系跨项目可查 + 一致性验收 + τ ≥ 0.95 + 开关默认关闭——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T1022 契约测试聚合 tests/contract/test_pooling_contracts.py（C1~C9 全场景端到端断言，含 SC-002 同树双池一致率 100% 与 SC-004 前置/单项目告警 100%）
- [ ] T1023 [P] 端到端演示 ops/demo_merged_pool.py（quickstart 六步；断言退出码 0、跨项目命中、冲突 UNKNOWN、稀释告警、一致性、开关默认）
- [ ] T1024 运行 quickstart.md 全部验证步骤并记录（验证记录回填；覆盖率 ≥85% 复核；命令与 ci.yml 逐字一致）
- [ ] T1025 [P] 更新 README.md（跨项目池化用法与稀释控制说明）与 docs/二期立项书.md 里程碑表（F6 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T1003→T1004、T1005→T1006 两链并行
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的池（匹配/统计需要 MergedPool）；US3 依赖 US2 的匹配与统计（一致性/τ 需要完整回放路径）
- **打磨（阶段 6）**: T1022/T1023 依赖全部故事；T1025 可在基础完成后开始

### 并行机会

- 阶段 2：两条链并行
- US1：T1007 与 T1008 并行
- US2：T1011/T1012/T1013 三测试并行；US3：T1017/T1018/T1019 三测试并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：合并池构建可重现 + 快照
3. **停下并验证**：分组/版本分组/前置条件/可重现正确

### 增量交付

1. 搭建 + 基础 → 版本分组键与模型就绪
2. US1 → 池化构建（MVP）
3. US2 → 回放与稀释（核心语义）
4. US3 → 谱系与合并验收（里程碑验收线）
5. 阶段 6 → 契约聚合/demo/文档

---

## 备注

- 澄清决议落点：冲突即 UNKNOWN（T1011/T1014，ScoreConflict 三要素齐全）；命中占比判定（T1012/T1015，树数占比参考同报告；单项目构成标注）
- 版本分组键（T1003/T1004）是"同语义最小单位"——避免逐评估器版本碎片化成单树池
- 一致性验收（T1018）是"合并不改变回放语义"的可证伪断言——SC-002 的机检落点
- 做梦开关（T1021）是最小改动：读取配置选择池，不特化 dreaming 逻辑（原则五）；默认关闭是稀释的保守闸门
- 无新 DB 表（只读扩展）；快照文件化沿用 005/010 惯例（replay/pools/ 数据目录）
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
