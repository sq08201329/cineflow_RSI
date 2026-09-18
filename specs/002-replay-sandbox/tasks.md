# 任务列表：回放模拟器与沙箱化策略执行

**输入**: 来自 `specs/002-replay-sandbox/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章（原则三/四为不可协商门禁）；功能 001 已交付（TreeStore/模型/评估器）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；对抗测试为合并阻塞门禁，**不允许跳过**（无 Docker 环境报错而非 skip）；无偏性为发布阻塞。

**组织方式**: 按用户故事分组（US1 回放引擎 → US2 沙箱拦截 → US3 无偏性验收）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T101 按 plan.md 创建目录：core/replay/、core/sandbox/backends/、policies/history/、tests/adversarial/、tests/unbiasedness/
- [x] T102 [P] 配置 pytest 新 markers（`adversarial`、`unbiasedness`）与覆盖率口径（core 含 replay/sandbox）
- [x] T103 [P] configs/movie.yaml 追加 replay 段（worker_count、latency_quantum_ms、unbiasedness_tau_threshold，对齐开发文档 §7 示例）

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T104 实现 VirtualClock core/replay/clock.py（tick_decision/tick_execution ⌈k/W⌉；worker_count≥1、batch≥1 校验）
- [x] T105 [P] 实现 ReplayTrajectory core/replay/trajectory.py（frozen + JSON 报告序列化；结局状态机）
- [x] T106 [P] 实现 Budget 与 SimulatorEnv/ExplorationPolicy 协议 policies/base.py（回放装配时 max_generation_calls 强制为 0 的断言）
- [x] T107 [P] 实现生成参数规范化 core/replay/matching.py（规范化 JSON 精确相等，决策 5）
- [x] T108 [P] 实现观测投影 core/replay/observation.py（Observation/ProbeResult 模型 + 字段白名单投影）
- [x] T109 [P] 测试夹具扩展 tests/conftest.py（小树构建工厂：可控父/参/得分的历史树；录制轨迹夹具生成器）

**检查点**: 时钟/轨迹/匹配/投影单测通过，模拟器可开始

---

## 阶段 3：用户故事 1 - 零成本回放探索策略（优先级：P1）🎯 MVP

**目标**: 由冻结树构建模拟器；observed/probe 语义、UNKNOWN、预算、时钟、轨迹全正确

**独立测试**: 手工小树 + 确定性手工策略，逐断言轨迹与手工预期一致

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T110 [P] [US1] 时钟单测 tests/unit/test_clock.py（⌈k/W⌉、决策轮计数、非法参数）
- [x] T111 [P] [US1] 匹配与投影单测 tests/unit/test_matching_observation.py（规范化相等、缺字段不匹配、白名单投影不泄漏）
- [x] T112 [P] [US1] 模拟器单测 tests/unit/test_simulator.py（揭示状态机、UNKNOWN 不得分、多节点同揭示、FAILED 节点可回放、预算耗尽拒绝、已揭示单调递增、零生成断言）
- [x] T113 [P] [US1] 模拟器池单测 tests/unit/test_pool.py（多树合并、未冻结树拒绝、异 Agent 拒绝、空池空轨迹）
- [x] T114 [US1] 参考策略端到端回放单测 tests/unit/test_replay_e2e.py（确定性手工策略全程回放，轨迹与手工预期逐字段一致——US1 验收场景 1-6）

### 用户故事 1 的实现

- [x] T115 [US1] 实现 ReplaySimulator core/replay/simulator.py（from_trees/observed/probe/trajectory；预算递减；揭示迁移；依赖 T104-T109）
- [x] T116 [US1] 实现模拟器池 core/replay/pool.py（冻结校验、同 Agent 校验、合并候选视图；依赖 T115）
- [x] T117 [US1] 实现参考手工策略 tests/stubs.py 追加 ReferencePolicy（确定性贪心，供单测/演示/无偏性共用）

**检查点**: US1 全部单测通过——进程内回放语义完整正确

---

## 阶段 4：用户故事 2 - 沙箱化执行与前缀拦截（优先级：P2）

**目标**: 策略在容器内经 stdio IPC 回放；三类作弊策略全拦截；合并阻塞门禁

**独立测试**: 对抗套件在真实容器后端上跑通，作弊必然失败

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T118 [P] [US2] IPC 协议单测 tests/unit/test_ipc_protocol.py（消息 schema、白名单、1MB 上限、超时映射 protocol_violation、值语义校验）
- [x] T119 [P] [US2] 静态检查单测 tests/unit/test_static_check.py（白名单 import 通过；socket/open/eval/getattr 逃逸被拒）
- [x] T120 [US2] 作弊策略实现 tests/adversarial/cheating_policies.py（peek_latent / timing_side_channel / hash_oracle 三件套 + 网络与文件 IO 尝试）
- [x] T121 [US2] 对抗测试 tests/adversarial/test_adversarial.py（三件套必然失败/被拒；计时断言双重判定：全部响应时间 ∈ 时延量子整数倍，且与隐藏得分 |Pearson r| < 0.1（SC-007）；**无 Docker 报错而非 skip**）
- [x] T122 [US2] 沙箱端到端集成测试 tests/integration/test_sandbox_e2e.py（正常策略容器内回放全程：版本落盘、轨迹回传、容器回收无孤儿）

### 用户故事 2 的实现

- [x] T123 [P] [US2] 实现协议层 core/sandbox/protocol.py（编码/解码/校验/抖动填充）
- [x] T124 [P] [US2] 实现静态检查 policies/static_check.py（AST 白名单）
- [x] T125 [P] [US2] 实现策略版本管理 policies/history 写入逻辑 policies/versioning.py（BLAKE3 前 12 位、幂等）
- [x] T126 [US2] 实现沙箱入口 core/sandbox/runner.py（静态检查→版本落盘→起容器→IPC 桥接→回收；依赖 T123-T125）
- [x] T127 [P] [US2] 实现后端 core/sandbox/backends/docker_hardened.py（加固旗标，本地兜底）
- [x] T128 [P] [US2] 实现后端 core/sandbox/backends/docker_gvisor.py（--runtime=runsc，CI 权威）与后端探测装配 available() 链
- [x] T129 [US2] 沙箱内策略侧 IPC 客户端 core/sandbox/policy_side.py（容器入口：读策略文件、跑 solve、应答 IPC；依赖 T123）

**检查点**: 对抗套件全绿（作弊全拦截）、正常策略容器内回放成功

---

## 阶段 5：用户故事 3 - 回放无偏性验收（优先级：P3）

**目标**: Kendall τ ≥ 0.95 门槛判定 + JSON 报告；注入偏差 100% 拒绝

**独立测试**: 已知一致/已知偏移轨迹对验证判定正确性

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T130 [P] [US3] τ 计算单测 tests/unit/test_kendall.py（τ-b 同分处理、样本不足拒绝、已知答案对照）
- [ ] T131 [US3] 无偏性验收测试 tests/unbiasedness/test_unbiasedness.py（一致轨迹放行、注入偏差 100% 拒绝、报告 schema 校验、FAILED 轮次剔除）

### 用户故事 3 的实现

- [ ] T132 [US3] 实现 core/replay/unbiasedness.py（kendall_tau + verify_unbiasedness + UnbiasednessReport）
- [ ] T133 [US3] 实现轨迹夹具 tests/unbiasedness/fixtures.py（一致轨迹对与 N 种注入偏差轨迹对的生成器）

**检查点**: 三故事全部独立可用

---

## 阶段 6：打磨与横切关注点

- [ ] T134 [P] CI 接入：.github/workflows/ci.yml 增加 adversarial job（装 runsc 并以 gVisor 后端跑对抗套件，失败即阻塞）与 unbiasedness job；单测 job 覆盖率口径不变
- [ ] T135 [P] 实现端到端演示 ops/demo_replay.py（quickstart 验证 4：小树→模拟器→沙箱回放→轨迹 JSON；生成调用审计断言）
- [ ] T138 [US1] 3 万节点回放性能基准 tests/integration/test_replay_benchmark.py（SC-006：分支因子 10 建树 → 构建模拟器 → 沙箱/进程内回放参考策略，全程 < 10 分钟断言；优先 PG，不可用时退 SQLite 内存库，保证本地可跑）
- [ ] T136 运行 quickstart.md 全部验证步骤并记录结果
- [ ] T137 [P] 更新 README.md（回放/沙箱/对抗/无偏性的用法与门禁说明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: 依赖搭建完成——阻塞所有用户故事
- **用户故事（阶段 3+）**: 全部依赖基础阶段；US2 的沙箱桥接依赖 US1 的模拟器接口（T126/T129 依赖 T115），US3 依赖 US1 的轨迹产出
- **打磨（阶段 6）**: T134、T135、T136、T138 依赖全部用户故事完成（T138 依赖 US1 的模拟器与参考策略 T117）；T137 可在基础完成后开始

### 用户故事依赖

- **US1（P1）**: 基础后可开始——进程内模拟器，不依赖容器
- **US2（P2）**: 依赖 US1 的 ReplaySimulator 接口（沙箱回放的对象）；对抗语义独立可测
- **US3（P3）**: 依赖 US1 的 ReplayTrajectory；验收计算本身独立可测

### 并行机会

- 阶段 1：T102、T103 并行
- 阶段 2：T105–T109 并行
- US1：T110–T113 测试并行
- US2：T118、T119 测试并行；T123、T124、T125、T127、T128 实现并行
- US3：T130 与 US2 任意任务并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1（进程内回放语义全对）
3. **停下并验证**：T114 端到端回放逐字段一致
4. 即得"历史可回放"的最小价值形态

### 增量交付

1. 搭建 + 基础 → 基础就绪
2. US1 → 回放引擎（MVP）
3. US2 → 沙箱与对抗门禁（作弊必拦截）
4. US3 → 无偏性发布门禁
5. 阶段 6 → CI 与演示就绪，达成周 4~6 里程碑验收（作弊全拦截；τ≥0.95）

---

## 备注

- 宪章约束落点：零生成（T106/T112）、物理不可达（T121/T122/T126）、计时量子化（T121/T123）、τ 门槛（T131/T132）、对抗 CI 阻塞（T134）、覆盖率 ≥85%（T102）
- 对抗测试无 Docker 环境**报错不跳过**（T121）——门禁不允许静默豁免
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
