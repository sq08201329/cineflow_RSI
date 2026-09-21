# 任务列表：judge 漂移自动检测

**输入**: 来自 `specs/012-judge-drift-detection/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（**原则六为核心**：系统只写 suspect、处置人工留痕）；功能 010（分布快照/台账/信度）与 006~009（judge 评估器）已交付；规格含 2026-09-21 澄清三条（滑动窗口基线、分级处置、默认仅 judge 类）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；漂移检出双向断言（注入检出 + 稳定不误报）；**本地验证命令与 ci.yml 逐字一致**（`ruff check .` + `ruff format --check .`）。

**组织方式**: 按用户故事分组（US1 检测 → US2 状态机与门禁 → US3 报表与联动）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T1101 configs/movie.yaml 追加 calibration.drift 段（window=5、buckets=10、psi_threshold=0.2、quantile_threshold=0.1、min_samples=3、suspect_weight=0.5、confirmed_exclude=true、scope_kinds=["judge"]、double_signal 规则）+ calibration/drift/ 数据目录约定（metrics/status/dispositions/reports 子目录 + .gitkeep）
- [x] T1102 [P] conftest 夹具扩展：分布快照序列工厂（基线周期序列 + 均值平移/方差展宽/双峰化三形态漂移 + 稳定序列 + 样本不足 + 缺口周期）、010 台账与信度报告夹具（含 judge 条目）、drift 临时目录夹具；不改坏既有夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T1103 drift 模型测试 tests/unit/test_drift_models.py（先写：frozen 六模型字段与校验——占比/阈值域、状态机合法迁移（系统只写 suspect 的模型层约束）、detector_version 格式）
- [x] T1104 [P] 实现 core/calibration/drift_models.py（DriftBaseline/DriftMetrics/DriftStatus/DriftDisposition/DriftReport/DeployEvidenceVerdict）
- [x] T1105 [P] drift 配置测试 tests/unit/test_drift_config.py（calibration.drift 段解析、阈值/窗口/范围缺失即报错、double_signal 规则）
- [x] T1106 [P] 实现配置解析（core/calibration/drift_config.py 或并入 config——与 010 风格一致）
- [x] T1107 [P] 统计原语测试 tests/unit/test_drift_stats.py（PSI 口径：同分布 PSI≈0、平移/展宽/双峰超阈、epsilon 平滑不炸；分位数偏移向量精度 < 1e-6）
- [x] T1108 [P] 实现 core/calibration/drift_stats.py（psi() + quantile_shifts() 纯函数）

**检查点**: 模型/配置/统计原语三件套单测通过——用户故事可开始

---

## 阶段 3：用户故事 1 - 漂移检测与告警（优先级：P1）🎯 MVP

**目标**: 滑动窗口基线 + 双维判定 + 口径版本化 + 只读纪律

**独立测试**: 已知漂移三形态 100% 检出、稳定 0 误报、首周期基线、样本不足/无数据注明

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T1109 [P] [US1] tests/unit/test_drift_detect.py（C1 场景 1~6：稳定 normal、三形态漂移检出、首周期 no_baseline 记基线、样本不足 insufficient、评估器升版独立基线、proxy/rule 默认关闭注明）
- [x] T1111 [P] [US1] tests/unit/test_drift_versioning.py（C2：同输入同口径记录逐字节一致（detector_version 相同）、口径升级新 detector_version 且历史不改写、只读审计——树零写入/评估器零变更/生成与 LLM 调用 0）

### 用户故事 1 的实现

- [x] T1110 [US1] 实现 core/calibration/drift_metrics.py 的 detect_drift（快照序列读取 → 滑动窗口基线 → PSI + 分位数偏移 → 判定 → 记录落盘 metrics/{agent}/{evaluator_id}/{period}.json；依赖 T1104、T1108）
- [x] T1112 [US1] 实现口径版本计算（detector_version = drift_detector@1.0.0+{算法+阈值哈希}）与记录不可改写（已存在即拒绝/幂等同口径）

**检查点**: 漂移检出/不误报/基线/口径版本成立——MVP 成立

---

## 阶段 4：用户故事 2 - 漂移处置门禁与人工留痕（优先级：P2）

**目标**: 状态机（系统只写 suspect）+ 分级合成门禁 + 部署证据接口 + 处置留痕

**独立测试**: 状态机三路径 + 三态合成差异 + 证据接口拒绝 + 留痕不可改写

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T1113 [P] [US2] tests/unit/test_drift_status.py（C3：suspect 登记含 trigger_metrics、人工确认 → confirmed_drift、误报 → false_alarm → 恢复 normal、系统写终态拒绝（机检）、留痕只增不改）
- [x] T1114 [P] [US2] tests/unit/test_drift_gate.py（C4/C5：normal 权重不变、suspect ×suspect_weight、confirmed_drift 归零、三态合成结果差异断言、deploy_evidence_verdict 对 suspect/confirmed_drift 拒绝 + 理由、normal/false_alarm 允许）

### 用户故事 2 的实现

- [x] T1115 [US2] 实现 core/calibration/drift_status.py（register_suspect/dispose/registry 文件化只增不改；依赖 T1104）
- [x] T1116 [US2] 实现 core/calibration/drift_gate.py（gate_weights + deploy_evidence_verdict；权重变化自然升版的说明入 docstring）
- [x] T1117 [US2] 各 Agent loop 接线（visual/editing/storyboard/screenplay 四处：合成前调 gate_weights 一行；各包回归全绿；**promo 与 sound 无 judge 层，不接**）

**检查点**: 状态机 + 门禁 + 证据接口成立；五处接线回归不破

---

## 阶段 5：用户故事 3 - 漂移监控报表与信度联动（优先级：P3）

**目标**: 周期报表（轨迹/状态/处置）+ 双信号强化告警 + CLI

**独立测试**: 报表字段齐全、双信号两路径、ScoreConflict 附注不入阈值

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T1118 [P] [US3] tests/unit/test_drift_report.py（C6/C7：items 齐全（指标序列/基线/阈值/状态/处置记录）、已处置项如实呈现、无数据标注"无数据"、双信号强化（漂移 ∧ 信度低于 target）与单信号常规两路径、**F6 ScoreConflict 附注口径：读取 F6 持久化来源（最近一次池化回放的命中分布文件，若有）；F6 侧未持久化时附注字段为空并注明"无持久化来源"，不得伪造冲突数据**——附注不参与阈值判定）

### 用户故事 3 的实现

- [x] T1119 [US3] 实现 core/calibration/drift_report.py（build_report → calibration/drift/reports/{period}.json；读检测记录 + 010 信度报告；**ScoreConflict 附注读取 F6 持久化来源，无持久化则字段为空并注明"无持久化来源"**；依赖 T1110、T1115）
- [x] T1120 [US3] ops/calibrate.py 新增 drift 子命令（检测 + 报表 + dispose 处置入口；节奏：close → drift → report；依赖 T1110、T1119）

**检查点**: 报表与联动成立；CLI 可走通检测→报表→处置

---

## 阶段 6：打磨与横切关注点

- [x] T1121 契约测试聚合 tests/contract/test_drift_contracts.py（C1~C8 全场景端到端断言，含 SC-002 系统只写 suspect 机检、SC-003 证据接口拒绝 100%、SC-005 只读审计）
- [x] T1122 [P] 端到端演示 ops/demo_judge_drift.py（quickstart 六步；断言退出码 0、检出/不误报/分级处置/证据拒绝/双信号）
- [x] T1123 运行 quickstart.md 全部验证步骤并记录（验证记录回填；覆盖率 ≥85% 复核；命令与 ci.yml 逐字一致）
- [x] T1124 [P] 更新 README.md（漂移监控用法与处置流程说明）与 docs/二期立项书.md 里程碑表（F7 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T1103→T1104、T1105→T1106、T1107→T1108 三链并行
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 的检测记录（状态登记需 DriftMetrics）；US3 依赖 US1/US2（报表需指标序列与状态）
- **打磨（阶段 6）**: T1121/T1122 依赖全部故事；T1124 可在基础完成后开始

### 并行机会

- 阶段 2：三条链并行
- US1：T1109 与 T1111 并行
- US2：T1113 与 T1114 并行；T1115 与 T1116 并行（不同文件）
- US3：T1118 单链（报表依赖状态与检测产出）

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：漂移检出与口径版本化
3. **停下并验证**：三形态检出、稳定不误报、只读审计

### 增量交付

1. 搭建 + 基础 → 模型/配置/统计原语就绪
2. US1 → 检测（MVP）
3. US2 → 状态机与门禁（污染控制）
4. US3 → 报表与联动（决策输入）
5. 阶段 6 → 契约聚合/demo/文档

---

## 备注

- 宪章原则六落点（本特性核心）：T1113（系统只写 suspect 机检 + 终态仅人工）+ T1114（证据接口拒绝）+ T1118（双信号强化）+ T1121 契约聚合
- 澄清决议落点：滑动窗口基线（T1109/T1110）；分级处置（T1114/T1116）；默认仅 judge 类（T1109 场景 6 + T1105 配置）
- 数据源零新增：全程只读 010 产物（快照/台账/信度）；T1111 只读审计断言承载 SC-005
- 合成门禁接线（T1117）是五处一行改动 + 各包回归；权重变化自然升版（composite 哈希机制自动承载）
- F9 未交付：deploy_evidence_verdict 接口先就位（T1116），部署流程不在本特性
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
