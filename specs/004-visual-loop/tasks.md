# 任务列表：视觉 Agent 闭环

**输入**: 来自 `specs/004-visual-loop/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章（原则一/二/三/五/六）；功能 001/002/003 已交付

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%（core + agents 口径）；适配器契约套件双实现同跑；一致性验收为发布阻塞。

**组织方式**: 按用户故事分组（US1 探索闭环 → US2 回放验证 → US3 一致性验收）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T301 创建目录 agents/visual/{evaluators,platform}
- [x] T302 [P] 新增依赖 numpy、imageio、imageio-ffmpeg（uv add；版本锁定进 uv.lock）
- [x] T303 [P] configs/movie.yaml visual 段补全（片段规格、生成预算、judge 锚点集=固定生成参数集（经确定性模拟生成器产出锚点工件，参数+工件哈希进 judge 版本号）、judge 3 提示词文本、帧采样规则、五评估器权重）

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T304 迁移 0003 visual_gen_jobs（可变运营表；唯一键 (round_id, params_hash)；无 immutable 触发器）
- [x] T305 [P] 定点归一测试 tests/unit/test_quantize.py（先写：6 位小数、边界、幂等）
- [x] T306 [P] 实现 core/evaluators/quantize.py（quantize_score 纯函数，业务无关）
- [x] T307 帧采样测试 tests/unit/test_frames.py（先写：ffprobe 解析、采样逐字节确定性、损坏文件报错不崩溃）
- [x] T308 实现 agents/visual/frames.py（ffprobe 探测 + 等间隔 N=8 采样 + 采样规格元信息）
- [x] T309 [P] conftest 夹具扩展（SimulatedVideoGen 工厂、程序化小片段工厂、visual 运营表引擎）

**检查点**: 迁移可执行、帧采样确定性证明、quantize 可用

---

## 阶段 3：用户故事 1 - 视觉线上探索闭环（优先级：P1）🎯 MVP

**目标**: 参数 → 生成 → 五评估器 → 合成 → 两段式落盘 → 对账；预算门禁与幂等

**独立测试**: 模拟生成器一轮闭环 + 崩溃隔离 + 对账审计

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T310 [P] [US1] tests/unit/test_visual_format_compliance.py（规格合规/违规、ffmpeg 不可用报错）
- [x] T311 [P] [US1] tests/unit/test_visual_aesthetic.py（统计代理映射 [0,1]、退化输入、重算一致）
- [x] T312 [P] [US1] tests/unit/test_visual_identity.py（跨镜头余弦、单镜头满分注明、重算一致）
- [x] T313 [P] [US1] tests/unit/test_visual_flicker.py（抖动/伪影检测、稳定片段高分、重算一致）
- [x] T314 [P] [US1] tests/unit/test_visual_cinematic.py（3 提示词投票、锚点集胜率映射、提示词变更 → 注册键变更 SC-007、网关调用全入账）
- [x] T315 [US1] tests/unit/test_visual_loop.py（预算门禁含边界、幂等零重复、单评估器崩溃隔离、对账三方一致、合规 0 分短路不跑 judge）
- [x] T316 [US1] tests/contract/test_video_gen_adapter.py（预估/实际花费、幂等键、状态机、工件可解码、错误映射；双实现同跑）

### 用户故事 1 的实现

- [x] T317 [P] [US1] 实现 agents/visual/evaluators/format_compliance.py
- [x] T318 [P] [US1] 实现 agents/visual/evaluators/aesthetic.py
- [x] T319 [P] [US1] 实现 agents/visual/evaluators/identity.py
- [x] T320 [P] [US1] 实现 agents/visual/evaluators/flicker.py
- [x] T321 [P] [US1] 实现 agents/visual/evaluators/cinematic.py（judge 委员会经网关）
- [x] T322 [P] [US1] 实现 agents/visual/platform/base.py + simulated.py（确定性程序化生成 + 账本）
- [x] T323 [US1] 实现 agents/visual/clip.py（生成编排 → 工件内容寻址落库 + probe_meta；依赖 T308、T322）
- [x] T324 [US1] 实现 agents/visual/loop.py（执行器；依赖 T317-T323、T304、T306）
- [x] T325 [P] [US1] 实现 agents/visual/platform/http_real.py（契约同构骨架，凭证注入）

**检查点**: 模拟生成器一轮闭环跑通，崩溃隔离与对账成立

---

## 阶段 4：用户故事 2 - 树冻结入池与回放（优先级：P2）

**目标**: 视觉树冻结入池；真值回放零生成；大工件内容寻址

**独立测试**: 冻结树入池 + 参考策略回放命中真实生成节点

- [x] T326 [US2] tests/integration/test_visual_replay.py（先写：冻结校验、真值回放、生成调用恒 0、工件哈希引用；PG 优先退 SQLite）
- [x] T327 [US2] 实现 loop.py freeze_round_tree（GenJob 全终态校验 + config_snapshot 创建时写全；依赖 T324）

**检查点**: 视觉探索数据可回放

---

## 阶段 5：用户故事 3 - 一致性验收（优先级：P3）

**目标**: 重算逐字节一致率 100% + τ 门禁 + 注入漂移 100% 拒绝 + JSON 报告

**独立测试**: 已知一致/已知漂移用例验证判定

- [x] T328 [US3] tests/unit/test_visual_consistency.py（先写：全一致 pass、注入乱序采样变体 reject、漂移清单字段、样本不足 reject、τ 对接 002）
- [x] T329 [US3] 实现 agents/visual/consistency.py（verify_consistency + ConsistencyReport；依赖 T327、五评估器）

**检查点**: 里程碑验收线（回放打分与真实重跑一致性）达成

---

## 阶段 6：打磨与横切关注点

- [x] T330 [P] 实现端到端演示 ops/demo_visual_loop.py（quickstart 验证 3 全流程；<5 分钟断言入报告；本地真实执行退出码 0）
- [x] T331 运行 quickstart.md 全部验证步骤并记录结果
- [x] T332 [P] 更新 README.md（视觉闭环用法、凭证配置说明、门禁清单现状）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: 依赖搭建（T305/T306 与 T307/T308 可并行，T309 独立）——阻塞所有用户故事
- **用户故事（阶段 3+）**: US1 依赖基础；US2 依赖 US1 执行器；US3 依赖 US2 的冻结树与五评估器
- **打磨（阶段 6）**: T330/T331 依赖全部故事；T332 可在基础完成后开始

### 并行机会

- 阶段 1：T302、T303 并行；阶段 2：T305/T306 与 T307/T308 并行
- US1：T310–T314 测试并行；T317–T322 实现并行
- US3：T328 与 US2 收尾可并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：模拟生成器闭环 + 崩溃隔离 + 对账
3. **停下并验证**：一轮探索端到端、预算门禁与幂等成立

### 增量交付

1. 搭建 + 基础 → 帧管线与运营表就绪
2. US1 → 视觉闭环（MVP）
3. US2 → 回放验证
4. US3 → 一致性验收（里程碑验收线）
5. 阶段 6 → 演示与文档就绪

---

## 备注

- 宪章约束落点：评估器确定性+版本冻结（T310-T314/T317-T321 + quantize T306）、昂贵动作仅线上（T315/T324）、immutable 两段式（T304/T324）、零生成回放（T326）、一致性验收发布阻塞（T328/T329）
- 与 promo 执行器保持各自实现（research 决策 7，YAGNI）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
