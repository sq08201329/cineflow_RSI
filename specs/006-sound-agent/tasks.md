# 任务列表：声音 Agent 闭环（配音/音效/配乐 + 策略自动进化）

**输入**: 来自 `specs/006-sound-agent/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（原则一/二/三/五/六均相关）；功能 001-005、010 已交付；规格含 2026-09-20 澄清会话两条决议（同步仅时序元数据、适配器三类型拆分）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；无偏性 τ≥0.95 为发布阻塞；适配器契约套件三类型 × 双实现。

**组织方式**: 按用户故事分组（US1 执行器与适配器 → US2 四评估器 → US3 回放/做梦/校准接入）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T601 创建 agents/sound/ 包骨架与 configs/movie.yaml 追加 sound 段（exploration_per_round_usd=300、clips_per_round=4、loudness 分档 {dialogue: -27±2, sfx: -30±3, music: -25±3}、av_sync_threshold_ms=120、sample_rate=16000、prices 按类型、simulated_gen 参数）+ evaluator_weights.sound（双 gate + asr 0.5 + emotion 0.5）
- [x] T602 [P] conftest 夹具扩展：TimingSheet 工厂（合法/重叠/越界）、声学属性可控的参数工厂（loudness_gain/event_times/cer_injected/情绪向量）、sound 临时库与数据目录夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T603 迁移测试 tests/unit/test_migration_0005.py（先写：sound_gen_jobs schema、唯一键 (round_id, params_hash)、gen_type 枚举约束、GRANT 纪律源码断言）
- [x] T604 实现迁移 ops/migrations/versions/0005_sound_gen_jobs.py（0004 同模式自包含 DDL + GRANT；依赖 T603 失败确认）
- [x] T605 [P] TimingSheet 测试 tests/unit/test_sound_timing.py（合法构造、重叠拒绝、越界拒绝、start≥end 拒绝）
- [x] T606 [P] 实现 agents/sound/timing.py
- [x] T607 [P] 音频合成测试 tests/unit/test_sound_audio.py（同参数 wav 逐字节一致、采样率/声道符合配置、属性注入标记随元数据落盘、响度增益可测）
- [x] T608 [P] 实现 agents/sound/audio.py（numpy 正弦叠加 + 标准库 wave PCM16）
- [x] T609 [P] 配置测试 tests/unit/test_sound_config.py（sound 段解析、价目缺失即报错、响度分档、权重节引用）
- [x] T610 [P] 实现 agents/sound/config.py

**检查点**: 迁移/TimingSheet/音频合成/配置四件套单测通过——用户故事可开始

---

## 阶段 3：用户故事 1 - 声音探索执行与确定性模拟生成（优先级：P1）🎯 MVP

**目标**: 三类型适配器（模拟 + 真实骨架）+ 探索执行器（预算分账/幂等/两段式落盘）

**独立测试**: 模拟器跑一轮：逐字节复现、预算拒绝、幂等重建、成本分账逐断言（评估器用桩注入，不接 US2 真实评估器）

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T611 [P] [US1] 适配器契约测试 tests/contract/test_sound_platform_contract.py（C9~C12：estimate ≤ actual、wav 可解析、元数据键齐全、错误分型、模拟全过/真实无凭证 skip——三类型 × 双实现同构）
- [x] T612 [P] [US1] 执行器测试 tests/unit/test_sound_loop.py（C1 场景 1~5：一轮 4 组参数落树分账齐全、超界拒绝已执行入账、同 round_id 重建 0 重复扣费、失败 job 成本照计入账、TimingSheet 非法执行前拒绝 0 调用；评估器以桩注入）

### 用户故事 1 的实现

- [x] T613 [US1] 实现 agents/sound/platform/base.py（协议 + 错误分型 + GeneratedAudio）+ simulated.py（TTS/SFX/Music 三模拟器；依赖 T608）
- [x] T614 [P] [US1] 实现 agents/sound/platform/http_real.py（三真实骨架，SOUND_TTS_*/SFX_*/MUSIC_* 环境变量，无凭证 skip 语义）
- [x] T615 [US1] 实现 agents/sound/loop.py（预算门禁申请前校验 + 事务复核、按 gen_type 分账、幂等派生 + 唯一键重建、评估器协议注入、两段式落盘；依赖 T604、T606、T610、T613）

**检查点**: 一轮探索（模拟器）工件落树 + 分账 + 幂等成立——MVP 成立

---

## 阶段 4：用户故事 2 - 四评估器与合成评分（优先级：P2）

**目标**: 双 gate（响度/同步）+ 双 proxy（ASR/情绪）+ 合成定点归一，接入执行器替换桩

**独立测试**: 注入声学属性夹具逐评估器断言；gate 短路与归一合成；重算逐位一致

### 用户故事 2 的测试（先写，确认失败后再实现）

- [x] T616 [P] [US2] tests/unit/test_sound_rules.py（C4/C5：响度分档合规/违规、静音不适用注明、同步 80ms 过/200ms gate 判 0、纯音乐不适用注明）
- [x] T617 [P] [US2] tests/unit/test_sound_proxies.py（C6/C7：CER 映射误差 <1e-6、同工件重评估逐位一致、非 TTS 跳过注明、情绪匹配 vs 背离分差、低置信标注）
- [x] T618 [P] [US2] tests/unit/test_sound_composite.py（C8：gate 违规总分 0 短路、适用分量归一合成、quantize 6 位定点、版本元信息含实现哈希；宪章测试纪律：四评估器注册元数据断言——cost_per_call ≥ 0 显式存在、deterministic=True、kind 正确；与既有评估器（004 视觉系）的对比样本夹具——同工件经新旧评估器各评一次，得分域与 diagnostics 键结构一致）

### 用户故事 2 的实现

- [x] T619 [US2] 实现 agents/sound/evaluators/loudness.py + av_sync.py（简化 BS.1770 纯 numpy；依赖 T608 元数据）
- [x] T620 [US2] 实现 agents/sound/evaluators/asr.py + emotion.py（确定性代理；实现哈希入版本号，004 _versioning 同款）
- [x] T621 [US2] 执行器接线真实评估器（loop.py 以 evaluator_weights.sound + composite_score_versioned + quantize 替换桩；依赖 T615、T619、T620）

**检查点**: 四评估器全绿；执行器产出节点 eval_breakdown 四分量齐全

---

## 阶段 5：用户故事 3 - 回放接入、无偏性验收与做梦进化（优先级：P3）

**目标**: 声音树入池 + τ≥0.95 发布阻塞 + 做梦首轮基线 + 010 周校准纳入（里程碑验收线）

**独立测试**: 无偏性对照（含注入偏差拒绝）；回放零生成审计；盲评清单对 sound 正常产出

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T622 [P] [US3] 无偏性测试 tests/unbiasedness/test_sound_unbiased.py（C14：声音夹具池回放 vs 真实重跑 τ ≥ 0.95；注入偏差 100% 拒绝——复用 core/replay/unbiasedness.py 口径）
- [ ] T623 [P] [US3] 回放与周校准接入测试 tests/unit/test_sound_replay.py（C13/C16：observed/probe 规范化精确匹配、UNKNOWN 语义、回放全程 generate 调用计数 0 审计、010 build_blind_list(agent_id="sound") 正常产出不触发 promo 特判；FR-011 分树断言：声音池按时间分 train/validation、最近树永远只做 validation——复用 005 分树口径）

### 用户故事 3 的实现

- [ ] T624 [US3] champion 策略 policies/history/sound/（手工策略首版 + meta.json 父子谱系，005 文件化惯例）+ 声音模拟器池接线（依赖 T621）
- [ ] T625 [US3] 做梦一轮接入验证（agent_id="sound" 演示档 M=8：候选静态检查 → 沙箱回放 → reward 排名 → 首轮基线落盘；零改动验证，若 dreaming 泛化有缺口则补最小泛化并注明；验证后回填 quickstart.md 的做梦命令为实际形态）

**检查点**: τ ≥ 0.95 通过 + 首轮进化基线落盘——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T626 集成测试 tests/integration/test_sound_pg.py（真实 PG：0005 迁移执行、唯一键冲突幂等重建、两段式落盘全链路、分账合计对账）
- [ ] T627 [P] 实现端到端演示 ops/demo_sound_loop.py（quickstart 六步；断言退出码 0、分账齐全、重算一致）
- [ ] T628 运行 quickstart.md 全部验证步骤并记录结果（含覆盖率 ≥85% 复核：core+agents+dreaming 口径）
- [ ] T629 [P] 更新 README.md（声音闭环用法）与 docs/二期立项书.md 里程碑表（F1 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T603→T604 一链；T605→T606、T607→T608、T609→T610 三链并行——阻塞所有用户故事
- **用户故事（阶段 3+）**: US1 依赖基础（评估器桩注入保持独立）；US2 依赖 US1 的执行器（T621 接线替换桩）；US3 依赖 US2 的完整打分链路（τ 需要真实得分）
- **打磨（阶段 6）**: T626/T627 依赖全部故事；T629 可在基础完成后开始

### 并行机会

- 阶段 2：四条测试/实现链并行
- US1：T611 与 T612 并行起步；T614 与 T613 并行
- US2：T616/T617/T618 三测试并行；US3：T622/T623 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：三类型模拟器 + 执行器（评估器桩）工件落树
3. **停下并验证**：逐字节复现、预算分账、幂等重建正确

### 增量交付

1. 搭建 + 基础 → 迁移/时序/音频/配置就绪
2. US1 → 执行器与适配器（MVP）
3. US2 → 四评估器接线（信号源就位）
4. US3 → 回放/无偏性/做梦（里程碑验收线）
5. 阶段 6 → 集成/demo/文档

---

## 备注

- 澄清决议落点：同步仅时序元数据（T605/T606、T616、T619——无视觉工件依赖）；适配器三类型拆分 + 成本分账（T611、T613~T615）
- US1 的"评估器桩注入"是保持故事独立性的显式取舍：loop.py 面向评估器协议编程，T612 用桩，T621 替换真实评估器——两故事各自可独立验证
- 做梦层与 010 预期零改动（005/010 泛化已成立）；T625 若发现泛化缺口，按"最小泛化 + 注明"处理，不得为声音特化 dreaming 代码（原则五）
- 模拟 ASR/情绪代理的确定性口径见 research 决策 4/9：实现哈希即版本，真实服务替换 = 升版本
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
