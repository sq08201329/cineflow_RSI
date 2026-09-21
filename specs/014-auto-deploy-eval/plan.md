# 实现计划：策略部署评估自动化（证据门槛 + 影子模式 + 人工事后抽检）

**分支**: `014-auto-deploy-eval` | **日期**: 2026-09-21 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/014-auto-deploy-eval/spec.md` 的功能规格说明（含 2026-09-21 澄清会话三条决议）

## 概要

交付 `core/deployment/` 包：证据包收集（**前置 = 无偏性验收**；三要件 = reward 对比 /
validation 排名 / 漂移 verdict）、门槛判定（**缺证据即拦截** + 禁止名单优先）、模式状态机
（`manual`/`shadow`/`auto` + 影子期区间累计 + 重标定标记）、影子模式与对照报告
（含差异分类与误入率口径）、自动部署执行（指针一致性检测 + 定点改写 + 谱系 `source=auto`）、
渐进抽检与否决回滚（**三件事一个逻辑事务**）、部署后漂移的回滚评估。**零新增第三方依赖，
零新 DB 表**（文件化留痕 + 指针定点改写复用 `core/yaml_edit`）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——blake3/json（留痕与哈希）、`core/yaml_edit`（指针定点改写，
005 approve 同款）、既有 005/011/012 模块（口径复用）

**存储**: 无新 DB 表；`deployment/` 数据目录（mode/evidence/shadow/deploys/spot_checks/rollbacks，
git 版本化，只增不改）；部署指针 = `configs/movie.yaml` 的
`deployment.{agent}.current_policy_version`（定点改写，注释保留）

**测试**: pytest；门槛判定组合矩阵（全满足/单要件不满足/证据缺失/禁止名单）；影子门禁
（时长/候选数缺口拒绝）；三件事回滚（含失败路径保持人工）；误入率重算一致性

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `core/deployment/` 新子包（业务无关部署机制）+ dreaming 收口处一处接线

**性能目标**: 单次判定（含池化回放结果读取）< 1 秒（读既有产物，不重跑回放）

**约束**: 门槛三要件 + 前置（宪章原则六）；缺证据即拦截；禁止名单优先；影子期未满
禁开 auto；抽检否决三件事同时生效；重标定期间 auto 一律拒绝；指针不一致即拒绝（防绕过）；
覆盖率和口径可重算；覆盖率 ≥85%

**规模/范围**: 6 个 core 模块 + 一处 dreaming 接线 + CLI + demo；不含真实发布系统对接、
真实 2 周影子期的运行（运营）、抽检的 Web 操作面（看板只读）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：版本冻结 | 部署是版本切换（指针改写）；证据快照引用评估器/策略版本；历史节点零修改 | ✅ 满足 |
| 原则二：不可变与谱系 | 部署/回滚/抽检/影子事件全留痕（只增不改）；谱系 `source=auto` | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 判定只读既有产物（不重跑回放/生成）；无新昂贵路径 | ✅ 满足 |
| 原则四：沙箱与前缀 | 不涉及策略执行；无新信息面 | ✅ 满足 |
| 原则五：单向依赖与配置化 | core/deployment（业务无关）；阈值/影子下限/抽检策略全 configs | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | **本特性核心**：证据门槛（缺证据即拦截）+ 禁止名单 + 影子前置 + 抽检否决回滚 + 误入率可度量（口径必须可被证伪） | ✅ 满足 |
| 测试纪律 | TDD；组合矩阵与失败路径断言；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/014-auto-deploy-eval/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── evidence-gate.md          # 证据包与门槛判定（C1~C3）
│   ├── shadow-mode.md            # 模式状态机/影子/对照报告（C4~C5）
│   └── auto-deploy-spotcheck.md  # 自动部署/抽检/回滚（C6~C10）
└── tasks.md                      # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/deployment/
├── evidence.py        # EvidenceBundle（前置无偏性 + 三要件，口径复用 005/011/012）
├── gate.py            # GateVerdict（优先级：forbidden > insufficient > blocked > eligible）
├── mode.py            # DeployModeState（模式 + 影子区间累计 + 重标定标记）
├── shadow.py          # ShadowEvent / ShadowReport / 误入率重算
├── auto_deploy.py     # evaluate_candidate 唯一入口 + auto_deploy + 指针一致性检测
└── spot_check.py      # 渐进抽检 + veto_and_rollback（三件事一个逻辑事务）+ 漂移回滚评估
deployment/            # 数据目录（mode/evidence/shadow/deploys/spot_checks/rollbacks，git 版本化）
dreaming/pipeline.py   # 轮次收口后一处接线（evaluate_candidate）
configs/movie.yaml     # deployment 段扩展（mode_default/gate/shadow/spot_check）
ops/deploy.py          # CLI：mode / evaluate / shadow-report / spot-check / veto / assess-drift
ops/demo_deploy_gate.py # 端到端演示（六步）
tests/unit/test_deployment_*.py；tests/contract/test_deployment_contracts.py
```

**结构决策**: 部署机制是全线基建（core/deployment，原则五）；触发收敛为**唯一入口**
`evaluate_candidate`（一处接线，三种模式行为）；留痕文件化（005/010/012 惯例）；
指针改写复用 `core/yaml_edit`（005 approve 同款，注释保留）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 存放 = core/deployment（业务无关）+ dreaming 一处接线
2. 证据包前置 = 无偏性验收（澄清 Q1）；三要件口径复用 005/011/012
3. 缺证据 = `insufficient_evidence`（非异常）+ 禁止名单优先级最高
4. 影子计时 = 模式区间累计 + 双下限（14 天 / 候选数）+ 重标定标记
5. 唯一入口三种行为（澄清 Q3）
6. 自动部署：指针一致性检测（防绕过）+ 定点改写 + 谱系 source=auto
7. 渐进抽检（澄清 Q2）+ 否决回滚三件事（失败即显式报错保持人工）
8. 误入率可重算（SC-007 机检）
9. 无 judge 的 Agent 默认保守（`allow_without_judge=false`）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：10 个 frozen 模型 + 文件 schema + 模式/判定状态机 + 配置段
- [contracts/evidence-gate.md](contracts/evidence-gate.md)（C1~C3）、
  [shadow-mode.md](contracts/shadow-mode.md)（C4~C5）、
  [auto-deploy-spotcheck.md](contracts/auto-deploy-spotcheck.md)（C6~C10）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

原则六新条款逐条落进契约：证据门槛（C2 优先级与缺证据拦截）、影子前置（C4 门禁）、
抽检否决回滚（C9 三件事与失败路径）、误入率可度量（C5 重算）；部署与回滚全留痕
（原则二）；历史节点零修改（原则一）。**无新增违规，门禁通过。**
