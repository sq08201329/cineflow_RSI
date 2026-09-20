# 任务列表：分镜 Agent 闭环（剧本→分镜脚本/动态预演 + 策略自动进化）

**输入**: 来自 `specs/008-storyboard-agent/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（六原则均相关）；功能 001-007、010 已交付；规格含 2026-09-20 澄清会话两条决议（coverage 场景级 + 必覆盖清单、情绪对齐读预演帧）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；无偏性 τ≥0.95 发布阻塞；新增评估器三件套（单测 + 注册元数据 + 对比样本）；**本地验证命令与 ci.yml 逐字一致**（`ruff check .` + `ruff format --check .`）。

**组织方式**: 按用户故事分组（US1 执行器与预演渲染 → US2 五评估器 → US3 回放/做梦/校准接入）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [X] T801 创建 agents/storyboard/ 包骨架与 configs/movie.yaml 追加 storyboard 段（exploration_per_round_usd、boards_per_round=3、景别档位枚举与序、机位档位含 side、运动档位、shot_grammar 规则 {jump_limit, same_size_limit}、axis_rules {require_transition_on_cross, allowed_transition_shots}、emotion_vectors 情绪基调向量表、render {fps/尺寸/price_per_shot_usd/编码单线程档}、judge {3 提示词 + 锚点 ShotList 集}）+ evaluator_weights.storyboard（三 gate + alignment 0.5 + script_fit 0.5）
- [X] T802 [P] conftest 夹具扩展：剧本工厂（3 场景 9 行，含 key 行标注 + 情绪 + axis_base）、ShotList 工厂（合法 + 四类非法变体：引用不存在行/场景无镜头/关键行未承接/档位越界）、素材帧夹具、storyboard 临时库夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [X] T803 迁移测试 tests/unit/test_migration_0007.py（先写：storyboard_render_jobs schema、唯一键 (round_id, shotlist_hash)、actual ≤ estimated CHECK、GRANT 纪律源码断言）
- [X] T804 实现迁移 ops/migrations/versions/0007_storyboard_render_jobs.py + agents/storyboard/db.py 表定义（0006 同模式）
- [X] T805 [P] 剧本模型测试 tests/unit/test_storyboard_script.py（ScriptSegment 校验：行 id 唯一/场景有序/关键行标注合法/情绪取值；轴向基准字段）
- [X] T806 [P] 实现 agents/storyboard/script.py
- [X] T807 [P] ShotList 测试 tests/unit/test_shotlist.py（规范化 JSON 键序稳定、schema_version 字段、三层校验：引用行存在/场景承接含 key 行逐条/档位枚举——四类非法各拒绝；**alternatives 备选数存在性与取值域（≥1 整数）断言**）
- [X] T808 [P] 实现 agents/storyboard/shotlist.py
- [X] T809 [P] 分镜卡渲染测试 tests/unit/test_storyboard_board_render.py（同 ShotList 两次 mp4 逐字节一致、单线程编码参数断言、分镜卡帧数与镜头数一致、帧由同一函数产出可供评估读像素、情绪色板注入可测）
- [X] T810 [P] 实现 agents/storyboard/board_render.py（分镜卡帧生成 + 拼接 → mp4；frame 产出函数同时服务评估器输入）
- [X] T811 [P] 配置测试 tests/unit/test_storyboard_config.py（storyboard 段解析、规则库缺失即报错、价目缺失即报错、情绪向量表、judge 锚点集解析）
- [X] T812 [P] 实现 agents/storyboard/config.py
- [X] T813 [P] 摘要函数测试 tests/unit/test_storyboard_summary.py（ShotList × 剧本 → 结构化文本确定性、重算逐字节一致、摘要函数哈希稳定）
- [X] T814 [P] 实现 agents/storyboard/summary.py

**检查点**: 迁移/剧本/ShotList/渲染/配置/摘要六件套单测通过——用户故事可开始

---

## 阶段 3：用户故事 1 - 分镜决策执行与动态预演渲染（优先级：P1）🎯 MVP

**目标**: 预演渲染适配器双实现 + 探索执行器（三层校验前置/预算/幂等/两段式/双键观测）

**独立测试**: 模拟渲染器跑一轮：逐字节复现、非法 ShotList 四类执行前拒绝、预算拒绝、幂等重建（评估器桩注入）

### 用户故事 1 的测试（先写，确认失败后再实现）

- [X] T815 [P] [US1] 预演契约测试 tests/contract/test_storyboard_platform_contract.py（C10~C13：estimate ≥ actual、mp4 可探测时长/帧率符合配置、元数据键齐全（景别序列/帧哈希/音轨标记）、错误分型、模拟全过/真实 skip）
- [X] T816 [P] [US1] 执行器测试 tests/unit/test_storyboard_loop.py（C1/C2 全场景：四类非法执行前拒绝 0 渲染 0 成本、一轮 3 组落树、预算超界拒绝已执行入账、同 round_id 重建 0 重复、渲染失败成本照计、剧本不足预检拒绝注明、双键观测（shotlist + gen_params）、freeze_round_tree 终态门禁；评估器桩注入）

### 用户故事 1 的实现

- [X] T817 [US1] 实现 agents/storyboard/platform/base.py（协议 + 错误分型 + RenderedAnimatic）+ simulated.py（确定性模拟渲染器，用 T810；estimated ≥ actual）
- [X] T818 [P] [US1] 实现 agents/storyboard/platform/http_real.py（STORYBOARD_RENDER_* 环境变量骨架，无凭证 skip）
- [X] T819 [US1] 实现 agents/storyboard/loop.py（run_storyboard_round：三层校验前置 → 预算门禁 → 渲染 → 内容寻址 → 评估器协议注入 → quantize → 一次性 INSERT → 双键观测 + freeze_round_tree；依赖 T804、T808、T812、T817）

**检查点**: 一轮分镜（模拟渲染器）预演落树 + 幂等 + 非法拒绝成立——MVP 成立

---

## 阶段 4：用户故事 2 - 五评估器与合成评分（优先级：P2）

**目标**: 三 gate（景别语法/覆盖率/轴规则）+ alignment proxy（读帧）+ judge.script_fit，接入执行器替换桩

**独立测试**: 分镜属性注入夹具逐评估器断言；gate 短路不跑 judge；重算逐位一致

### 用户故事 2 的测试（先写，确认失败后再实现）

- [X] T820 [P] [US2] tests/unit/test_storyboard_rules.py（C4~C6：景别跳跃超限判 0、同景别连续超限判 0、场景无镜头判 0、key 行未承接判 0、合并台词手法通过、侧别硬跳无过渡判 0、带过渡镜头通过、必覆盖清单空降级注明；**边界两条：单场景行数超镜头数上限——ShotList 仍合法且缺口由 coverage 门禁暴露（不静默截断）；景别规则与轴规则冲突——覆盖率门禁优先且 diagnostics 含冲突说明（不自动豁免）**）
- [X] T821 [P] [US2] tests/unit/test_storyboard_alignment.py（C7：读预演帧像素（帧哈希与渲染元数据一致）、对齐 vs 背离分差显著、误差 <1e-6、同预演重评估逐位一致、情绪缺失"不适用"注明）
- [X] T822 [P] [US2] tests/unit/test_storyboard_judge.py（C8：摘要成对比较、3 提示词投票、平局 0.5 如实记录、网关计费入账、Mock 重跑逐位一致、版本号含提示词/锚点/摘要三段哈希）
- [X] T823 [P] [US2] tests/unit/test_storyboard_composite.py（C9：gate 违规短路——judge 未调用网关计数不增、适用分量归一合成、quantize 定点；注册元数据断言（cost_per_call/deterministic/kind）+ 与 004/006/007 既有评估器对比样本——宪章测试纪律三件套）

### 用户故事 2 的实现

- [X] T824 [US2] 实现 agents/storyboard/evaluators/shot_grammar.py + coverage.py + axis_rule.py（三 gate；规则库与校验同源）
- [X] T825 [US2] 实现 agents/storyboard/evaluators/alignment.py（读 T810 的帧产出函数 → 确定性特征向量 → 余弦映射）
- [X] T826 [US2] 实现 agents/storyboard/evaluators/script_fit.py（judge：摘要 × 3 提示词 × 锚点集；三段哈希版本号；依赖 T814）
- [X] T827 [US2] 执行器接线真实五评估器（evaluator_weights.storyboard + composite + quantize 替换桩，gate 短路纪律；依赖 T819、T824~T826）

**检查点**: 五评估器全绿；节点 eval_breakdown 五分量齐全；gate 短路省 LLM 成本可断言

---

## 阶段 5：用户故事 3 - 回放接入、无偏性验收与做梦进化（优先级：P3）

**目标**: 分镜树入池 + τ≥0.95 发布阻塞 + 做梦首轮基线 + 010 盲评纳入 + schema 稳定性契约

**独立测试**: 无偏性对照（含注入拒绝）；回放零渲染零网关审计；盲评 storyboard 正常；schema 快照断言

### 用户故事 3 的测试（先写，确认失败后再实现）

- [X] T828 [P] [US3] 无偏性测试 tests/unbiasedness/test_storyboard_unbiased.py（C15：分镜夹具池回放 vs 真实重跑（模拟渲染重执行 + 五评估器重算）τ ≥ 0.95；注入偏差 ≥3 形态 100% 拒绝）
- [X] T829 [P] [US3] 回放与交接测试 tests/unit/test_storyboard_replay.py（C14/C16/C17：双键规范化精确匹配、UNKNOWN 语义、回放全程 render + 网关调用计数 0 审计、分树最近树只做 validation、010 build_blind_list(agent_id="storyboard") 正常产出、dreaming/010 无 storyboard 特判静态证明、ShotList schema 快照断言（字段名/枚举值与文档一致））

### 用户故事 3 的实现

- [X] T830 [US3] champion 策略 policies/history/storyboard/{version}.py（手工首版：剧本 + 规则约束 → ShotList 网格贪心，OptimalPolicy.solve() 形态）+ meta.json 谱系根 + 分镜模拟器池接线（依赖 T827）
- [X] T831 [US3] 做梦一轮接入验证（agent_id="storyboard" 演示档 M=8 全流程 + 首轮基线落盘；零改动预期；验证后回填 quickstart 做梦命令）

**检查点**: τ ≥ 0.95 通过 + 首轮基线落盘——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T832 集成测试 tests/integration/test_storyboard_pg.py（真实 PG：0007 迁移执行、唯一键幂等重建、两段式全链路、成本对账）
- [ ] T833 [P] 实现端到端演示 ops/demo_storyboard_loop.py（quickstart 六步；断言退出码 0、逐字节一致、gate 短路、τ 达标）
- [ ] T834 运行 quickstart.md 全部验证步骤并记录（验证记录回填；覆盖率 ≥85% 复核；**命令与 ci.yml 逐字一致**）
- [ ] T835 [P] 更新 README.md（分镜闭环用法）与 docs/二期立项书.md 里程碑表（F3 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T803→T804 一链；T805→T806、T807→T808、T809→T810、T811→T812、T813→T814 五链并行（T810 为关键路径——US1 渲染与 US2 alignment 都依赖）
- **用户故事（阶段 3+）**: US1 依赖基础（桩注入保持独立）；US2 依赖 US1 执行器（T827 接线）；US3 依赖 US2 完整打分链路
- **打磨（阶段 6）**: T832/T833 依赖全部故事；T835 可在基础完成后开始

### 并行机会

- 阶段 2：五条测试/实现链并行
- US1：T815 与 T816 并行；T818 与 T817 并行
- US2：T820/T821/T822/T823 四测试并行；US3：T828/T829 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：ShotList 校验 + 预演执行器（评估器桩）落树
3. **停下并验证**：逐字节复现、非法拒绝、幂等重建、双键观测正确

### 增量交付

1. 搭建 + 基础 → 迁移/剧本/ShotList/渲染/配置/摘要就绪
2. US1 → 执行器与预演渲染（MVP）
3. US2 → 五评估器接线（信号源就位）
4. US3 → 回放/无偏性/做梦 + schema 契约（里程碑验收线）
5. 阶段 6 → 集成/demo/文档

---

## 备注

- 澄清决议落点：coverage 场景级 + 必覆盖清单（T805/T806、T807/T808、T820）；情绪对齐读帧（T809/T810、T821、T825——帧产出函数同时服务渲染与评估，禁止两套帧）
- US1 评估器桩注入是既有模式（006/007 同款），T827 替换真实评估器
- 双键观测（`shotlist` + `gen_params`）直接复用 007 结论（002 的 GEN_PARAMS_KEY 固定读 gen_params），T819 落地、T829 断言
- dreaming/010 零改动预期；缺口按"最小泛化 + 注明"，不得为分镜特化 dreaming 代码（原则五）
- 下游接线（视觉线生成参数建议、剪辑线候选镜头库）不在本特性：T829 的 schema 快照断言只保证 ShotList 契约稳定
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
