# 任务列表：剧本 Agent 降级模式（记录-回放 + 人工改策略）

**输入**: 来自 `specs/009-script-agent-degraded/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（**原则六为核心**：禁止强行自动进化）；功能 001-008、010 已交付；规格含 2026-09-20 澄清两条（判据阈值自动判定、策略=代码/调参走 configs）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%；**本特性的核心门禁是"禁止自动进化"的拒绝语义**（配置默认值断言 + 拒绝行为断言 + 审计断言三重）；本地验证命令与 ci.yml 逐字一致（`ruff check .` + `ruff format --check .`）。

**组织方式**: 按用户故事分组（US1 分阶段记录 → US2 七评估器 → US3 降级治理与判据）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [X] T901 创建 agents/screenplay/ 包骨架与 configs/movie.yaml 追加 screenplay 段（target_duration_min、page_tolerance、lines_per_page、dialogue_action_ratio 区间、beat_sheet 节拍表、character_aliases 别名表、upgrade_criteria 判据阈值 {judge_r_target, min_samples, drift_band, gate_violation_max}、judge {提示词 + 锚点大纲集}）+ evaluator_weights.screenplay（四 gate + 两 proxy 0.5/0.5 + judge 0.5）+ dreaming.no_auto_evolve_agents: [screenplay, dev]
- [X] T902 [P] conftest 夹具扩展：ScriptArtifact 工厂（三段 + 结构化标记：节拍清单/场景头/角色表/行）+ 七类缺陷变体（节拍缺失/页数越界/幽灵角色/地点不一致/比例失衡/同名异写/时间线矛盾）、人工策略源码夹具、screenplay 临时库与数据目录夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [X] T903 迁移测试 tests/unit/test_migration_0008.py（先写：screenplay_jobs schema、唯一键 (round_id, stage, params_hash)、stage CHECK 枚举、actual ≤ estimated、GRANT 纪律源码断言）
- [X] T904 实现迁移 ops/migrations/versions/0008_screenplay_jobs.py + agents/screenplay/db.py（0007 同模式）
- [X] T905 [P] 工件模型测试 tests/unit/test_screenplay_artifact.py（三段构造与解析、结构化标记完整性校验、内容寻址、缺失标记拒绝）
- [X] T906 [P] 实现 agents/screenplay/artifact.py
- [X] T907 [P] 配置测试 tests/unit/test_screenplay_config.py（screenplay 段解析、节拍表/别名表/判据阈值缺失即报错、比例区间、judge 锚点集）
- [X] T908 [P] 实现 agents/screenplay/config.py
- [X] T909 [P] 摘要函数测试 tests/unit/test_screenplay_summary.py（大纲结构化摘要确定性、重算逐字节一致、摘要函数哈希稳定）
- [X] T910 [P] 实现 agents/screenplay/summary.py
- [X] T911 [P] 导出对接测试 tests/unit/test_screenplay_export.py（export_segment → 008 ScriptSegment 字段一致；**双向 schema 快照断言**——本侧导出与 008 侧输入字段/枚举锁定，防漂移）
- [X] T912 [P] 实现 agents/screenplay/export_segment.py（→ 008 `validate_script` 可通过）

**检查点**: 迁移/工件/配置/摘要/导出五件套单测通过——用户故事可开始

---

## 阶段 3：用户故事 1 - 分阶段产出与全量记录（优先级：P1）🎯 MVP

**目标**: outline→scenes→script 三阶段产出经网关生成、成本入账、节点带 policy_version 落树

**独立测试**: Mock 网关 + 人工策略跑一轮三阶段：节点齐备、幂等、失败成本照计、版本可回溯（评估器桩注入）

### 用户故事 1 的测试（先写，确认失败后再实现）

- [ ] T913 [P] [US1] tests/unit/test_screenplay_loop.py（C1 场景 1~4：三阶段 3 节点 stage 齐备 + policy_version = 人工版本、同 round_id 幂等重建 0 重复、网关失败 FAILED + 成本照计、节点可回溯策略版本；网关缓存键与响应哈希落盘断言；评估器桩注入）

### 用户故事 1 的实现

- [ ] T914 [US1] 实现 agents/screenplay/loop.py（run_screenplay_round：分阶段 → 网关生成（缓存键/响应哈希）→ 评估器协议注入 → quantize → 一次性 INSERT → ScreenplayRoundResult；依赖 T904、T906、T908）
- [ ] T915 [US1] ops/screenplay.py CLI 的 produce 子命令（风格对齐 ops/calibrate.py）

**检查点**: 一轮三阶段产出落树 + 幂等 + 版本回溯成立——MVP 成立

---

## 阶段 4：用户故事 2 - 七评估器与合成评分（优先级：P2）

**目标**: 四门禁 + 两代理 + judge（仅大纲），接入执行器替换桩

**独立测试**: 七类缺陷变体逐评估器断言；judge 阶段适用范围；gate 短路；重算一致

### 用户故事 2 的测试（先写，确认失败后再实现）

- [ ] T916 [P] [US2] tests/unit/test_screenplay_rules.py（C4~C7：节拍缺失判 0、页数越界判 0、幽灵角色/地点不一致判 0、比例失衡判 0、合法工件全过）
- [ ] T917 [P] [US2] tests/unit/test_screenplay_proxies.py（C8/C9：同名异写/未登记别名扣分并诊断、正确写法满分、时间线矛盾命中并逐条诊断、无冲突满分）
- [ ] T918 [P] [US2] tests/unit/test_screenplay_judge.py（C10：**仅 outline 阶段参与**——scenes/script 阶段"不适用"且合成按适用分量归一；3 judge 投票；平局 0.5；网关计费；Mock 重跑逐位一致；版本号含提示词/锚点/摘要三段哈希）
- [ ] T919 [P] [US2] tests/unit/test_screenplay_composite.py（C11：gate 违规短路——judge 未调用网关计数不增、适用分量归一、quantize 定点；注册元数据断言（cost_per_call/deterministic/kind）+ 与 004/006/007/008 既有评估器对比样本——宪章测试纪律三件套）

### 用户故事 2 的实现

- [ ] T920 [US2] 实现 agents/screenplay/evaluators/beat_structure.py + page_minutes.py + scene_character.py + dialogue_action_ratio.py（四 gate，依赖 T906）
- [ ] T921 [US2] 实现 agents/screenplay/evaluators/entity_consistency.py + timeline_conflict.py
- [ ] T922 [US2] 实现 agents/screenplay/evaluators/dramatic_tension.py（judge，仅 outline；三段哈希版本号；依赖 T910）
- [ ] T923 [US2] 实现 agents/screenplay/evaluators/composite.py + loop.py 接线真实七评估器（stage 条件适用语义；gate 短路；依赖 T914、T920~T922）

**检查点**: 七评估器全绿；judge 阶段适用范围成立；节点 eval_breakdown 分量齐全

---

## 阶段 5：用户故事 3 - 降级治理、回放沙盘与升级判据（优先级：P3）

**目标**: 禁止自动进化三重保证 + 人工提交通道 + 回放对比 + 采纳门禁 + 判据材料 + 010 接入（里程碑验收线）

**独立测试**: 拒绝语义契约；提交三路径；对比与采纳四场景；判据四场景；盲评 screenplay 产出

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T924 [P] [US3] tests/contract/test_screenplay_no_auto_evolve.py（C12：`run_dream_round(agent_id="screenplay")` 立即拒绝且 0 候选 0 计费、默认配置含 screenplay 与 dev（防配置漂移）、其他 Agent 不受影响回归、审计断言"为 screenplay 生成候选次数恒 0"）
- [ ] T925 [P] [US3] tests/unit/test_screenplay_policy_versions.py（C13：合法策略版本化落盘含 parent_version/提交人、违规策略拒绝且历史无新增、同源码重复提交幂等同版本、草稿不入历史、configs 调参不产生策略版本）
- [ ] T926 [P] [US3] tests/unit/test_screenplay_compare_adopt.py（C14：**断言 observed/probe 规范化精确匹配（参数键序乱排仍命中）与观测投影白名单**、UNKNOWN 树记 0 并提示扩大记录、新版本优/劣两路径报告呈现、未采纳指针不变（机检）、采纳后指针更新 + 记录落盘、拒绝留痕理由非空、回放零 LLM 审计）
- [ ] T927 [P] [US3] tests/unit/test_screenplay_evidence.py（C15：达标 → meets、样本不足 → below + "样本不足"标注、负相关 → below + 告警、人推翻 → 留痕且系统结论字段逐字节不变、阈值缺失即报错）
- [ ] T932 [P] [US3] tests/contract/test_screenplay_calibration.py（C16：build_blind_list(agent_id="screenplay") 产出**大纲阶段** top-k；信度报告含 screenplay judge 条目（相关系数/样本量/达标标记）；promo 特判回归不破）

### 用户故事 3 的实现

- [ ] T928 [US3] dreaming 侧拒绝语义（config 增 no_auto_evolve_agents 解析 + `AutoEvolutionForbiddenError` + 候选生成前检查；最小改动，不特化其他逻辑）
- [ ] T929 [US3] 实现 agents/screenplay/policy_versions.py + sandbox_compare.py + adoption.py（复用 002 静态检查、005 谱系 meta 布局、dreaming/reward 的 pareto_auc）
- [ ] T930 [US3] 实现 agents/screenplay/upgrade_evidence.py（阈值快照 + 自动结论 + 推翻留痕，快照不可变）
- [ ] T931 [US3] ops/screenplay.py CLI 的 submit / compare / adopt / reject / evidence 子命令 + 人工策略首版 policies/history/screenplay/{version}.py + meta.json（parent_version=null）

**检查点**: 禁止自动进化三重保证成立 + 对比/采纳/判据全通 + 010 接入——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T933 集成测试 tests/integration/test_screenplay_pg.py（真实 PG：0008 迁移执行、唯一键幂等、分阶段两段式全链路、成本对账）
- [ ] T934 [P] 实现端到端演示 ops/demo_screenplay_loop.py（quickstart 六步；断言退出码 0、拒绝语义、采纳门禁、判据结论）
- [ ] T935 运行 quickstart.md 全部验证步骤并记录（验证记录回填；覆盖率 ≥85% 复核；命令与 ci.yml 逐字一致）
- [ ] T937 [P] [US3] tests/unbiasedness/test_screenplay_unbiased.py（**宪章门禁 FR-013/SC-008**：剧本夹具池回放打分 vs 真实重跑（网关缓存命中下重执行 + 七评估器重算）得分序列 Kendall τ ≥ 0.95；注入偏差 ≥3 形态 100% 拒绝；未达标时对比报告拒绝产出断言；参照 tests/unbiasedness/test_storyboard_unbiased.py）
- [ ] T936 [P] 更新 README.md（剧本降级模式用法 + 禁止自动进化的说明）与 docs/二期立项书.md 里程碑表（F4 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T903→T904 一链；T905→T906、T907→T908、T909→T910、T911→T912 四链并行
- **用户故事（阶段 3+）**: US1 依赖基础（桩注入）；US2 依赖 US1 执行器（T923 接线）；US3 依赖 US2 完整打分链路（判据信度需要真实得分）与 US1 产出
- **打磨（阶段 6）**: T933/T934 依赖全部故事；T936 可在基础完成后开始

### 并行机会

- 阶段 2：四条测试/实现链并行
- US1：T913 单链（执行器测试与实现紧耦合）
- US2：T916/T917/T918/T919 四测试并行；US3：T924/T925/T926/T927/T932/T937 六测试并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：三阶段产出落树（评估器桩）— 记录链路成立
3. **停下并验证**：stage 齐备、幂等、版本回溯正确

### 增量交付

1. 搭建 + 基础 → 迁移/工件/配置/摘要/导出就绪
2. US1 → 分阶段记录（MVP）
3. US2 → 七评估器接线（信号源与信度数据就位）
4. US3 → 降级治理 + 沙盘 + 判据 + 010 接入（里程碑验收线）
5. 阶段 6 → 集成/demo/文档

---

## 备注

- 宪章原则六落点（本特性核心）：T924 三重断言（默认配置/拒绝行为/审计）+ T928 实现 + T931 CLI 无任何"自动采纳"路径 + T934 demo 显式演示拒绝
- **宪章门禁落点（FR-013/SC-008，analyze 补齐）**：T937 无偏性验收（新增 Agent 发布阻塞，未达标不得产出对比报告）；T926 补 FR-007 的匹配与投影断言
- 澄清决议落点：判据阈值自动判定（T927/T930）；策略=代码、调参走 configs（T925/T929）
- judge 阶段适用范围（仅 outline）在 T918 双向断言（outline 参与 / scenes+script 不适用），T923 接线时保持
- US1 评估器桩注入是既有模式（006/007/008 同款），T923 替换真实评估器
- 008 schema 对接（T911/T912）是双向锁定：本侧导出 + 008 侧输入字段，任一漂移即红
- 升级为自动进化**不在本特性**：T930 只产出判据与"不达标"标注，不做任何升级动作；升级须另立决议并修订宪章
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
