# 任务列表：外环周校准（人评锚点 → calibration → 权重再拟合）

**输入**: 来自 `specs/010-weekly-calibration/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（原则一/二/三/五/六均相关）；功能 001-005 已交付（树/注册中心/回放/双闭环/做梦层）；规格含 2026-09-19 澄清会话四条决议

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%（core + agents + dreaming 口径）；偏差数学双向断言；清单零泄露与提案门禁为机检契约。

**组织方式**: 按用户故事分组（US1 盲评清单与录入 → US2 偏差台账报告 → US3 提案与生效）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T501 创建 core/calibration/ 包骨架与 configs/movie.yaml 追加 calibration 段（period_days=7、top_k=5、min_samples=3、bias_threshold=0.15、reliability_target=0.6、ridge_lambda=1.0、self_pairing_exclusions={"platform_truth": ["human.platform_metrics"]}）
- [x] T502 [P] conftest 夹具扩展：带 eval_breakdown 的多评估器夹具树工厂、人评条目工厂、promo 回流夹具、calibration/ 临时数据目录夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T503 迁移测试 tests/unit/test_migration_0004.py（先写：schema 字段、唯一键 (node_id, reviewer, round_id)、SQLite 侧 INSERT-only 触发器拒绝 UPDATE/DELETE、score CHECK 域）
- [x] T504 实现迁移 ops/migrations/versions/0004_calibration_anchors.py（双方言触发器复用 0001 模式 + GRANT USAGE 纪律，依赖 T503 失败确认）
- [x] T505 [P] 领域模型测试 tests/unit/test_calibration_models.py（frozen 不可变、score ∈ [0,1] 校验、source 枚举 human_blind/platform_truth、状态机合法迁移）
- [x] T506 [P] 实现 core/calibration/models.py（AnchorScore/CalibrationRound/PairingRecord/BiasRecord/WeightProposal）
- [x] T507 [P] 配置解析测试 tests/unit/test_calibration_config.py（calibration 段读取、缺节报错、self_pairing_exclusions 映射、阈值类型校验）
- [x] T508 [P] 实现 core/calibration/config.py（calibration 段解析；plan 外小增补——避免 weights.py 职责外溢，见备注）

**检查点**: 迁移双方言触发器 + 模型 + 配置三件套单测通过——用户故事可开始

---

## 阶段 3：用户故事 1 - 盲评任务生成与人评录入（优先级：P1）🎯 MVP

**目标**: top-k 盲评清单（零泄露）+ 人评录入通道（冻结/幂等）+ promo 平台真值锚点适配

**独立测试**: 夹具树生成清单断言零泄露；录入三路径（合法/重复/越界）；平台锚点幂等

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T509 [P] [US1] tests/unit/test_blind_selection.py（top-k 降序、样本不足注明、序列化键白名单递归断言无 score/eval_breakdown、对 promo 调用报错——C1 场景 1~4）
- [x] T510 [P] [US1] tests/unit/test_anchor_intake.py（score 域校验、reviewer 非空、round 状态门禁、清单内节点校验、同键重复幂等拒绝计数、整批不中断——C2 场景 1~3）
- [x] T511 [P] [US1] tests/unit/test_platform_anchors.py（metric_weights 归一化口径复用、reviewer=渠道标识、同轮重复采集幂等——C3 场景 1~2）

### 用户故事 1 的实现

- [x] T512 [US1] 实现 core/calibration/selection.py（build_blind_list；依赖 T506、T508）
- [x] T513 [US1] 实现 core/calibration/anchors.py（intake_anchors；依赖 T504、T506、T512）
- [x] T514 [P] [US1] 实现 agents/promo/anchors.py（collect_platform_anchors 业务侧适配；依赖 T513 的写入接口）
- [x] T515 [US1] CLI ops/calibrate.py 的 round / intake 子命令（依赖 T512、T513）

**检查点**: 盲评清单产出且零泄露机检通过；人评与平台锚点入库冻结——MVP 成立

---

## 阶段 4：用户故事 2 - 偏差检测与 calibration 台账（优先级：P2）

**目标**: 防自循环配对 + 偏差/相关数学 + append-only 台账 + 每周信度报告

**独立测试**: 注入已知偏移数据集双向断言；台账追加前后注册中心版本不变

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T516 [P] [US2] tests/unit/test_pairing.py（platform_truth 锚点剔除 human.platform_metrics 分量并注明、visual 人评全量配对、eval_breakdown 缺分量跳过注明——C4 场景 1~2）
- [x] T517 [P] [US2] tests/unit/test_bias.py（注入 +0.2 偏移误差 <1e-6、Pearson 与参考实现一致、负相关标记、judge 走 Kendall τ 复用 002 且禁止胜率当分数、样本不足不产偏差——C5 场景 1~3）
- [x] T518 [P] [US2] tests/unit/test_ledger_report.py（台账 append-only 首轮行逐字节不变、最新快照读取、报告 schema 四要素 + meets_target + alerts、台账追加前后注册中心 spec 集合与 calibration 不变——C6 场景 1~2 + 版本不变断言）
- [x] T532 [P] [US2] tests/unit/test_anchor_snapshots.py（FR-009：锚点得分分布快照 per 评估器 per 周期落盘 calibration/snapshots/{agent_id}/{evaluator_id}/{period}.json——分布分桶与分位数口径、每周期一条、字段可供 F7 漂移检测消费）

### 用户故事 2 的实现

- [x] T519 [US2] 实现 core/calibration/pairing.py（pair_anchors，exclusions 配置驱动；依赖 T508、T513）
- [x] T520 [US2] 实现 core/calibration/bias.py（compute_bias；Pearson 自实现 + τ 复用 core/replay/unbiasedness.py）
- [x] T521 [US2] 实现 core/calibration/ledger.py + core/calibration/report.py（append_ledger / build_report / 锚点分布快照写入——FR-009，快照与台账同轮次落盘）
- [x] T522 [US2] 轮次收口管线与 CLI report 子命令（rounds/{agent_id}/{round_id}.json 落盘 + 状态机 open→intake→closed；依赖 T519~T521）

**检查点**: 一轮校准从配对到信度报告全通；负相关告警成立

---

## 阶段 5：用户故事 3 - 权重再拟合提案与生效门禁（优先级：P3）

**目标**: 约束岭回归自动拟合候选权重 + 确认/搁置两键门禁 + 生效管线（新版本 + 定点改写 + 回滚）

**独立测试**: 拟合约束断言；提案生成/搁置/确认/过期拒绝四路径；历史节点审计一致

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T523 [P] [US3] tests/unit/test_refit_fit.py（拟合结果非负且和为一；小样本下向现权重收缩——收缩保守性断言；零样本退化返回现权重；λ_ridge 注入）
- [x] T524 [P] [US3] tests/unit/test_refit_gate.py（超阈生成 pending/未超阈 None/负相关禁止/首轮基线不判超阈；confirm 生效：新版本 `+w{哈希}` 注册 + calibration 填台账快照 + yaml 定点改写保注释；shelve 零变更机检（spec 集合 + 文件哈希前后一致）；过期 based_version 拒绝；生效失败回滚提案 failed——C7/C8 场景全覆盖）

### 用户故事 3 的实现

- [x] T525 [US3] 实现 core/calibration/refit.py（约束岭回归 numpy 实现 + maybe_propose + confirm/shelve + configs 定点改写 + 回滚；依赖 T520、T521；定点改写实现标注 WS3 遗留共用）
- [x] T526 [US3] CLI ops/calibrate.py 的 propose / confirm / shelve 子命令（确认人/时间落盘；依赖 T525）

**检查点**: 提案全流程成立；未经确认生效次数为 0（SC-005 机检）

---

## 阶段 6：打磨与横切关注点

- [x] T527 契约测试聚合 tests/contract/test_calibration_contracts.py（C1~C9 全场景端到端断言，含 SC-002 零泄露机检、SC-006 历史节点审计、FR-011 校准全流程生成/投放调用计数为 0 的零昂贵动作审计断言）
- [x] T528 集成测试 tests/integration/test_calibration_pg.py（真实 PG：INSERT-only 触发器双侧证明、唯一键冲突、生效管线对真实 configs 副本的定点改写）
- [x] T529 [P] 实现端到端演示 ops/demo_calibration.py（quickstart 六步：清单→录入→平台锚点→偏差台账→报告→提案确认；断言退出码 0、版本不变、台账追加）
- [x] T530 运行 quickstart.md 全部验证步骤并记录结果（含覆盖率 ≥85% 复核）
- [x] T531 [P] 更新 README.md（周校准操作说明）与 docs/二期立项书.md 里程碑状态；确认 pyproject coverage 口径自动覆盖 core/calibration

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T503→T504 一链；T505→T506、T507→T508 两条链并行——阻塞所有用户故事
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的锚点入库（配对需要锚点 + eval_breakdown）；US3 依赖 US2 的台账与偏差（提案的证据来源）
- **打磨（阶段 6）**: T527/T528 依赖全部故事；T529 依赖 T526；T531 可在基础完成后开始

### 并行机会

- 阶段 2：三条测试/实现链并行
- US1：T509/T510/T511 三测试并行；T514 与 T512/T513 并行
- US2：T516/T517/T518/T532 并行；US3：T523/T524 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：盲评清单 + 录入冻结 + 平台锚点
3. **停下并验证**：零泄露机检与幂等拒绝正确

### 增量交付

1. 搭建 + 基础 → 迁移/模型/配置就绪
2. US1 → 锚点通道（MVP）
3. US2 → 偏差台账与信度报告（F7 数据源就绪）
4. US3 → 提案与生效门禁（里程碑验收线：首轮盲评入库 + 再拟合流程走通）
5. 阶段 6 → 契约聚合/集成/demo/文档

---

## 备注

- 澄清决议落点：台账不升版（T518 版本不变断言、T521）；promo 不盲评（T509 场景 4、T514）；候选权重自动拟合 + 禁止编辑（T523~T526，CLI 无编辑路径）；防自循环剔除（T516、T519）
- plan 外增补 core/calibration/config.py（T507/T508）：calibration 段解析独立成模块，避免 core/evaluators/weights.py 承担非权重配置
- 定点改写实现（T525）与 WS3 遗留 #3（dreaming approve yaml 丢注释）共用，完成后 WS3 该项同步收口
- judge 偏差复用 core/replay/unbiasedness.py 的 τ-b（T520），不新造数学轮子
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
