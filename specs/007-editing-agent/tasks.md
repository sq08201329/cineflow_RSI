# 任务列表：剪辑 Agent 闭环（粗剪→精剪→成片 + 策略自动进化）

**输入**: 来自 `specs/007-editing-agent/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（六原则均相关）；功能 001-006、010 已交付；规格含 2026-09-20 澄清会话两条决议（judge 输入 = EDL 摘要、场景分区约束）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；无偏性 τ≥0.95 发布阻塞；新增评估器三件套（单测 + 注册元数据 + 对比样本）；本地验证命令必须与 ci.yml 逐字一致（含 `ruff format --check .`——010/006 教训）。

**组织方式**: 按用户故事分组（US1 执行器与渲染 → US2 五评估器 → US3 回放/做梦/校准接入）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T701 创建 agents/editing/ 包骨架与 configs/movie.yaml 追加 editing 段（exploration_per_round_usd=400、edits_per_round=3、target_duration_s=120 + duration_tolerance_s=10、shot_limits {min_shot_ms: 500, max_shot_ms: 20000}、transition_rules {allowed: [cut, dissolve, fade], dissolve_max_ms: 2000, forbid_jump_cut_within_scene: true}、pacing_baseline 分段配置、d_cap、render {fps/尺寸/price_per_second_usd=0.05/编码单线程档}、judge {3 提示词 + 锚点 EDL 集}）+ evaluator_weights.editing（三 gate + pacing 0.6 + narrative 0.4）
- [x] T702 [P] conftest 夹具扩展：镜头库工厂（≥6 镜头分区归属）、SceneStructure 工厂、EDL 工厂（合法 + 五类非法变体：引用不存在/出入点越界/跨分区/场景乱序/非法转场）、素材帧夹具、editing 临时库夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T703 迁移测试 tests/unit/test_migration_0006.py（先写：edit_render_jobs schema、唯一键 (round_id, edl_hash)、actual ≤ estimated CHECK、GRANT 纪律源码断言）
- [x] T704 实现迁移 ops/migrations/versions/0006_edit_render_jobs.py + agents/editing/db.py 表定义（0005 同模式；依赖 T703 失败确认）
- [x] T705 [P] 镜头库/分区测试 tests/unit/test_editing_shots.py（ShotEntry 校验、SceneStructure 分区无重复/引用存在/有序）
- [x] T706 [P] 实现 agents/editing/shots.py
- [x] T707 [P] EDL 测试 tests/unit/test_edl.py（规范化 JSON 键序稳定、四层校验：引用/越界/分区归属与顺序/转场规则库——五类非法各拒绝）
- [x] T708 [P] 实现 agents/editing/edl.py（四层校验，与转场门禁共用配置规则库）
- [x] T709 [P] 渲染合成测试 tests/unit/test_editing_render.py（同 EDL 两次 mp4 逐字节一致、单线程确定性编码参数断言、叠化/混音元数据正确、元数据镜头时长序列与 EDL 一致）
- [x] T710 [P] 实现 agents/editing/render.py（numpy 定点拼接/叠化 alpha 混合/混音增益叠加 → mp4，复用 004 encode_mp4 路径并固定单线程档——消除 x264 flake 根因）
- [x] T711 [P] 配置测试 tests/unit/test_editing_config.py（editing 段解析、基准曲线缺失即报错、价目缺失即报错、judge 锚点集解析）
- [x] T712 [P] 实现 agents/editing/config.py
- [x] T713 [P] 摘要函数测试 tests/unit/test_editing_summary.py（EDL → 结构化文本确定性、同 EDL 重算逐字节一致、摘要函数哈希稳定）
- [x] T714 [P] 实现 agents/editing/summary.py

**检查点**: 迁移/分区/EDL/渲染/配置/摘要六件套单测通过——用户故事可开始

---

## 阶段 3：用户故事 1 - 剪辑决策执行与确定性渲染合成（优先级：P1）🎯 MVP

**目标**: 渲染适配器双实现 + 探索执行器（四层校验前置/预算/幂等/两段式落盘）

**独立测试**: 模拟渲染器跑一轮：逐字节复现、非法 EDL 五类执行前拒绝、预算拒绝、幂等重建（评估器桩注入，不接 US2 真实评估器）

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T715 [P] [US1] 渲染契约测试 tests/contract/test_editing_platform_contract.py（C10~C13：estimate ≥ actual、mp4 可探测时长/帧率符合配置、元数据键齐全、错误分型、模拟全过/真实 skip）
- [x] T716 [P] [US1] 执行器测试 tests/unit/test_editing_loop.py（C1/C2 全场景：非法 EDL 五类执行前拒绝 0 渲染 0 成本、一轮 3 EDL 落树、预算超界拒绝已执行入账、同 round_id 重建 0 重复、渲染失败成本照计、素材不足/总长不足预检 FAILED 注明；评估器桩注入）

### 用户故事 1 的实现

- [x] T717 [US1] 实现 agents/editing/platform/base.py（EditRenderAdapter 协议 + 错误分型 + RenderedFilm）+ simulated.py（确定性模拟渲染器，用 T710；estimated ≥ actual）
- [x] T718 [P] [US1] 实现 agents/editing/platform/http_real.py（真实渲染骨架，EDIT_RENDER_* 环境变量，无凭证 skip）
- [x] T719 [US1] 实现 agents/editing/loop.py（run_editing_round：EDL 校验前置 → 预算门禁 → 渲染 → 内容寻址 → 评估器协议注入（桩可换）→ quantize → 一次性 INSERT → EditingRoundResult；freeze_round_tree 终态门禁——004/006 同构；依赖 T704、T708、T712、T717）

**检查点**: 一轮剪辑（模拟渲染器）成片落树 + 幂等 + 非法拒绝成立——MVP 成立

---

## 阶段 4：用户故事 2 - 评估器组合与合成评分（优先级：P2）

**目标**: 三 gate（时长/分布/转场）+ pacing proxy + judge.narrative_flow，接入执行器替换桩

**独立测试**: 剪辑属性注入夹具逐评估器断言；gate 短路不跑 judge；重算逐位一致

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T720 [P] [US2] tests/unit/test_editing_rules.py（C4~C6：时长 130s 过/150s gate 判 0、含 300ms 镜头分布判 0、非法转场判 0、合法序列全过；gate 违规总分 0）
- [x] T721 [P] [US2] tests/unit/test_editing_pacing.py（C7：分段统计口径、加权距离误差 <1e-6、贴近 vs 背离基准分差显著、缺基准拒绝启动）
- [x] T722 [P] [US2] tests/unit/test_editing_judge.py（C8：摘要成对比较、3 提示词投票胜率、平局 0.5 如实记录、网关计费入账、Mock 后端同比较重跑逐位一致、版本号含提示词/锚点/摘要三段哈希）
- [x] T723 [P] [US2] tests/unit/test_editing_composite.py（C9：gate 违规短路——judge 未调用网关计数不增、适用分量归一合成、quantize 定点；注册元数据断言（cost_per_call/deterministic/kind）+ 与 004/006 既有评估器对比样本——宪章测试纪律三件套）

### 用户故事 2 的实现

- [x] T724 [US2] 实现 agents/editing/evaluators/duration.py + shot_distribution.py + transitions.py（三 gate；转场与 T708 校验同库）
- [x] T725 [US2] 实现 agents/editing/evaluators/pacing.py（分段统计 + 加权欧氏距离映射）
- [x] T726 [US2] 实现 agents/editing/evaluators/narrative.py（judge：摘要 × 3 提示词 × 锚点 EDL 集成对比较；三段哈希版本号；依赖 T714）
- [x] T727 [US2] 执行器接线真实五评估器（evaluator_weights.editing + composite + quantize 替换桩，gate 短路纪律；依赖 T719、T724~T726）

**检查点**: 五评估器全绿；节点 eval_breakdown 五分量齐全；gate 短路省 LLM 成本可断言

---

## 阶段 5：用户故事 3 - 回放接入、无偏性验收与做梦进化（优先级：P3）

**目标**: 剪辑树入池 + τ≥0.95 发布阻塞 + 做梦首轮基线 + 010 盲评纳入（里程碑验收线）

**独立测试**: 无偏性对照（含注入拒绝）；回放零渲染零网关审计；盲评 editing 正常产出

### 用户故事 3 的测试（先写，确认失败后再实现）

- [x] T728 [P] [US3] 无偏性测试 tests/unbiasedness/test_editing_unbiased.py（C15：剪辑夹具池回放 vs 真实重跑（模拟渲染器重执行 + 五评估器重算）τ ≥ 0.95；注入偏差——篡改评估器版本/judge 胜率/节奏口径——100% 拒绝）
- [x] T729 [P] [US3] 回放与校准接入测试 tests/unit/test_editing_replay.py（C14/C16：EDL 规范化精确匹配、UNKNOWN 语义、回放全程 render + 网关调用计数 0 审计、分树最近树只做 validation、010 build_blind_list(agent_id="editing") 正常产出 + dreaming/010 无 editing 特判静态证明）

### 用户故事 3 的实现

- [x] T730 [US3] champion 策略 policies/history/editing/{version}.py（手工首版：镜头库 + 分区约束 → EDL 网格贪心，OptimalPolicy.solve() 形态）+ meta.json 谱系根（parent_version=null）+ 剪辑模拟器池接线（依赖 T727）
- [x] T731 [US3] 做梦一轮接入验证（agent_id="editing" 演示档 M=8 全流程 + 首轮基线落盘；零改动预期，缺口按最小泛化注明；验证后回填 quickstart 做梦命令为实际形态）

**检查点**: τ ≥ 0.95 通过 + 首轮基线落盘——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [x] T732 集成测试 tests/integration/test_editing_pg.py（真实 PG：0006 迁移执行、唯一键冲突幂等重建、两段式全链路、成本对账）
- [x] T733 [P] 实现端到端演示 ops/demo_editing_loop.py（quickstart 六步；断言退出码 0、逐字节一致、gate 短路、τ 达标）
- [x] T734 运行 quickstart.md 全部验证步骤并记录（验证记录回填 quickstart；覆盖率 ≥85% 复核；**验证命令与 ci.yml 逐字一致含 `ruff format --check .`**）
- [x] T735 [P] 更新 README.md（剪辑闭环用法）与 docs/二期立项书.md 里程碑表（F2 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T703→T704 一链；T705→T706、T707→T708、T709→T710、T711→T712、T713→T714 五链并行——阻塞所有用户故事
- **用户故事（阶段 3+）**: US1 依赖基础（评估器桩注入保持独立）；US2 依赖 US1 执行器（T727 接线）；US3 依赖 US2 完整打分链路
- **打磨（阶段 6）**: T732/T733 依赖全部故事；T735 可在基础完成后开始

### 并行机会

- 阶段 2：六条测试/实现链并行（T708/T710 是关键路径，US1/US2 都依赖）
- US1：T715 与 T716 并行；T718 与 T717 并行
- US2：T720/T721/T722/T723 四测试并行；US3：T728/T729 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：EDL 校验 + 渲染执行器（评估器桩）成片落树
3. **停下并验证**：逐字节复现、非法拒绝、幂等重建正确

### 增量交付

1. 搭建 + 基础 → 迁移/分区/EDL/渲染/配置/摘要就绪
2. US1 → 执行器与渲染（MVP）
3. US2 → 五评估器接线（信号源就位）
4. US3 → 回放/无偏性/做梦（里程碑验收线）
5. 阶段 6 → 集成/demo/文档

---

## 备注

- 澄清决议落点：judge 输入 = EDL 摘要（T713/T714、T722、T726；三段哈希版本号）；场景分区约束（T705/T706、T707/T708、T716 非法变体）
- US1 评估器桩注入是保持故事独立性的显式取舍（同 006 模式），T727 替换真实评估器
- 单线程确定性编码（T710）同时是 004 flake 根因的修复路径；若可安全回移 004 视觉编码参数，在 T734 验证时评估（另记运维事项，不混入本特性范围）
- dreaming/010 零改动预期（006 已实证）；缺口按"最小泛化 + 注明"，不得为剪辑特化 dreaming 代码（原则五）
- 本地验证纪律：**命令与 ci.yml 逐字一致**（`ruff check .` + `ruff format --check .`），010/006 格式事故教训
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
