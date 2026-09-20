# 实现计划：剧本 Agent 降级模式（记录-回放 + 人工改策略）

**分支**: `009-script-agent-degraded` | **日期**: 2026-09-20 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/009-script-agent-degraded/spec.md` 的功能规格说明（含 2026-09-20 澄清会话两条决议）

## 概要

交付 `agents/screenplay/` 包（agent_id = `screenplay`）：分阶段产出执行器（outline→scenes→
script，各自独立节点与评估；LLM 经网关生成并缓存收敛非确定性）、七评估器（节拍结构/
页数换算/场景角色/对白动作比例四门禁 + 实体一致性/时间线冲突两代理 + `judge.dramatic_tension`
**仅大纲阶段**，三段哈希版本号）、**降级模式治理**（dreaming 配置化拒绝为 screenplay 生成
候选——宪章原则六的工程落点；人工策略提交通道复用 002 静态检查与 005 谱系布局；
回放对比报告复用 dreaming/reward 的 pareto_auc 口径；人工采纳才动部署指针）、
**升级判据材料**（阈值配置化 + 系统自动判定达标/不达标 + 推翻留痕）、010 周校准纳入、
与 008 分镜的 `export_segment` schema 对接。**零新增第三方依赖**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——既有 LLM 网关（缓存/重试/计费，Mock 后端确定性）、blake3、
pyyaml、SQLAlchemy Core；静态检查复用 002；pareto_auc 复用 `dreaming/reward.py`；
定点归一复用 `core/evaluators/quantize.py`；台账/信度复用 010 的 `core/calibration`

**存储**: DB 新运营表 `screenplay_jobs`（迁移 0008，唯一键 `(round_id, stage, params_hash)`）；
剧本工件（含结构化标记）内容寻址入对象存储；节点走 001 immutable 树；策略版本走
`policies/history/screenplay/`（文件化谱系）；判据材料文件化快照

**测试**: pytest；结构化剧本夹具（节拍缺失/页数越界/幽灵角色/比例失衡/实体冲突/时间线
矛盾/张力高低）；禁用自动进化的拒绝语义契约；回放对比与采纳门禁契约

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `agents/screenplay/` 新业务包（→ core 单向依赖，第五个 Agent 闭环；
**本特性是唯一不含自动进化的闭环**）

**性能目标**: 一轮三阶段产出（Mock 网关）< 30 秒；回放对比（≥2 树）< 1 分钟

**约束**: 回放零 LLM（原则三审计）；评估器版本冻结（judge 三段哈希）；FAILED 成本照计
（原则二）；**禁止自动进化**（原则六，配置化 + 显式拒绝 + 审计断言）；阈值/节拍表/
比例区间/权重全配置化（原则五）；覆盖率 ≥85%

**规模/范围**: 1 个 agents 子包（约 13 模块）+ 1 张运营表 + CLI + demo + 人工策略首版 +
dreaming 侧拒绝语义（最小改动）；不含真实 LLM 凭证、开发 Agent 降级实现（骨架复用对象，
不作承诺）、自动进化升级（须另立决议）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 七评估器 deterministic + 实现哈希；judge 三段哈希；定点归一 | ✅ 满足 |
| 原则二：节点不可变与成本 | 分阶段两段式落盘；FAILED 成本照计；工件内容寻址；策略谱系文件化 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 生成经网关（缓存收敛）；回放零 LLM 审计断言；价目缺失即报错 | ✅ 满足 |
| 原则四：沙箱与前缀不可泄露 | 人工策略候选回放走 002 沙箱（复用，无新路径） | ✅ 满足 |
| 原则五：单向依赖与配置化 | `agents/screenplay → core`；拒绝名单/阈值/节拍表/比例区间全配置 | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | **本特性核心**：禁止自动进化（配置化 + 显式拒绝 + 审计断言）；人工采纳门禁；判据不达标如实标注且推翻留痕；纳入 010 盲评 | ✅ 满足 |
| 测试纪律 | TDD；新增评估器三件套；拒绝语义双向断言；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/009-script-agent-degraded/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── screenplay-loop.md              # 分阶段产出与零 LLM 回放（C1~C3）
│   ├── screenplay-evaluators.md        # 七评估器与合成（C4~C11）
│   ├── screenplay-degraded.md          # 禁用自动进化/提交通道/对比/采纳（C12~C14）
│   └── screenplay-upgrade-evidence.md  # 升级判据/周校准/schema 对接（C15~C17）
└── tasks.md                            # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
agents/screenplay/
├── config.py              # ScreenplayConfig（目标时长/页数容差/lines_per_page/比例区间/节拍表/别名表/判据阈值/judge）
├── db.py                  # screenplay_jobs 运营表定义
├── artifact.py            # ScriptArtifact（三段 + 结构化标记）与解析
├── export_segment.py      # → 008 ScriptSegment 导出（schema 对接）
├── summary.py             # 大纲结构化摘要（judge 输入，哈希入版本）
├── evaluators/
│   ├── beat_structure.py / page_minutes.py / scene_character.py / dialogue_action_ratio.py
│   ├── entity_consistency.py / timeline_conflict.py
│   ├── dramatic_tension.py  # judge（仅 outline 阶段）
│   └── composite.py
├── policy_versions.py     # 人工策略提交（版本化 + 002 静态检查 + 谱系 meta）
├── sandbox_compare.py     # 回放对比报告（pareto_auc 复用）
├── adoption.py            # 采纳/拒绝留痕 + 部署指针
├── upgrade_evidence.py    # 升级判据材料（阈值快照 + 自动结论 + 推翻留痕）
└── loop.py                # 分阶段产出执行器（网关/幂等/两段式）
dreaming/config.py 或 pipeline.py  # no_auto_evolve_agents 配置 + AutoEvolutionForbiddenError 拒绝语义
ops/migrations/versions/0008_screenplay_jobs.py
ops/screenplay.py          # CLI：produce / submit / compare / adopt / reject / evidence
ops/demo_screenplay_loop.py
configs/movie.yaml         # 追加 screenplay 段 + evaluator_weights.screenplay + dreaming.no_auto_evolve_agents
policies/history/screenplay/  # 人工策略首版 + meta.json
tests/unit/test_screenplay_*.py；tests/contract/test_screenplay_*.py；
tests/integration/test_screenplay_pg.py
```

**结构决策**: 剧本 = 第五个 Agent 闭环，但**治理面最重**——特有模块集中在降级模式
（`policy_versions` / `sandbox_compare` / `adoption` / `upgrade_evidence`）与
**dreaming 侧的拒绝语义**（配置化名单 + 显式异常）；评估器层是既有模式的复用。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 包名 `agents/screenplay/`、agent_id = screenplay（避免与"脚本"歧义）
2. 分阶段落树；**judge 只作用于大纲阶段**（技术方案原文限定），其他阶段"不适用"
3. 硬规则解析依赖工件的结构化标记（确定性前提）；schema 与 008 输入同源
4. 两代理确定性实现（别名表 + 时间线单调性）
5. 生成的非确定性由**网关缓存**收敛；回放零 LLM
6. 提交通道/对比/采纳复用 002 静态检查、005 谱系布局、dreaming pareto_auc
7. 禁止自动进化 = 配置名单 + 显式拒绝异常 + 默认值断言（原则六可审计）
8. 判据材料阈值配置化 + 自动判定 + 推翻留痕（澄清 Q1）
9. `export_segment` 与 008 双向 schema 快照锁定

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：screenplay_jobs schema、ScriptArtifact/HumanPolicyVersion/
  ReplayComparison/AdoptionRecord/UpgradeEvidence、评估器组合、状态机
- [contracts/screenplay-loop.md](contracts/screenplay-loop.md)（C1~C3）、
  [screenplay-evaluators.md](contracts/screenplay-evaluators.md)（C4~C11）、
  [screenplay-degraded.md](contracts/screenplay-degraded.md)（C12~C14）、
  [screenplay-upgrade-evidence.md](contracts/screenplay-upgrade-evidence.md)（C15~C17）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

原则六的"禁止自动进化"落成可机检的三重保证（配置默认值断言 + 拒绝语义断言 + 审计
断言）；人工采纳门禁与判据诚实标注落进契约场景；回放零 LLM 与成本入账对齐原则二/三。
**无新增违规，门禁通过。**
