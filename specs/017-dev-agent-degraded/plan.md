# 实现计划：开发 Agent 降级模式（选题/IP 评估 —— 记录-回放 + 人工策略 + 回放沙盘）

**分支**: `017-dev-agent-degraded` | **日期**: 2026-09-23 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/017-dev-agent-degraded/spec.md` 的功能规格说明（含 2026-09-23 澄清会话五条决议）

## 概要

把七 Agent 链的链首——**开发 Agent（选题 / IP 评估 / 立项组合）**以降级模式接入：人工编写的
**题材方向探索策略**驱动一轮产出，产出**立项组合工件**（多个推荐选题 + 组合内"本轮进入生产"
标记）→ 全量落发现树（记录）→ 硬规则二门禁 + 两个**模拟代理信号**（历史同类型票房回归、
舆情热度）打分 → 人工改策略经静态检查后在**回放沙盘**上与现部署版本零成本对比 → 人工采纳
才更新部署指针。**禁止自动进化**（配置名单 + 显式拒绝 + 审计断言三重机检常驻）。

技术主线不是"再写一个 Agent"，而是**把 009 的降级骨架提炼为可复用件**：`core/degraded/`
承接与业务无关的降级机制（人工策略版本化与静态检查接线、回放对比、采纳留痕、判据材料），
`agents/screenplay/` 与 `agents/dev/` 各自只保留业务件（工件 schema、评估器、匹配键、轮次循环）。
同时修掉一处既有缺陷：判据材料路径 `calibration/upgrade-events/{period}.json` **不含 agent id**，
第二个降级 Agent 写入同一 ISO 周会互相覆盖。

**诚实边界（本特性最核心的工程对象）**：开发 Agent 无 judge 层、无人类锚点（010 明确排除），
其判据材料**必然**只能给出"证据来源缺失"。本特性不补齐信号（不做伪 judge、不擅改 010），
而是把"信号有多弱"做成**可机读、可审计、可比对**的形态——全量声明判据阈值、逐项标注
"实测值 / 无法评价（来源缺失）"、系统结论恒不为"达标"。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增运行时依赖——stdlib + 既有 `core/{tree,replay,sandbox,evaluators,llm_gateway,calibration,deployment,yaml_edit}` 与 `policies/{base,versioning,static_check}`

**存储**: 新增运营表 `dev_jobs`（迁移 `0010`，可变中间态）；发现树节点仍一次性 INSERT（001 纪律）；
立项组合工件内容寻址入对象存储；判据材料/对比报告/采纳记录为 append-only JSON 文件

**测试**: pytest（TDD）；`tests/unit/test_dev_*.py`、`tests/unit/test_dev_core_degraded_purity.py`、
`tests/contract/{test_dev_contracts,test_dev_no_auto_evolve}.py`、
`tests/unbiasedness/test_dev_unbiased.py`；断言含"改配置即新评估器版本"、"回放零 LLM"、
"证据来源缺失逐项标注"、"低于最小树数拒绝产出"、静态检查与形态零分支、
**策略执行超时与"无环境对象"守护**（见前置裁决节）

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `core/degraded/`（新，业务无关）+ `agents/dev/`（新，业务相关）+ 配置/迁移/ops 同步

**性能目标**: **不设性能门禁**——本环节无昂贵动作（纯分析 + 文本产出），单轮产出为文本级；
回放对比零 LLM、零生成，代价只有池化与打分

**约束**: 单份判据材料 schema（与 009 同构）；回放零 LLM 与 `max_generation_calls=0`（原则三）；
评估器确定性 + 实现哈希入版本号（原则一）；`core/` 保持业务无关、依赖单向（原则五）；
形态差异只走 `configs/*.yaml`（原则五）；010 规格**不改**（本特性既定选择）；
**策略执行的隔离口径依宪章 v2.0.0 原则四例外条款**（见"前置裁决"节的两项落地义务）

**规模/范围**: 新包 `core/degraded/`（4 模块）+ 新包 `agents/dev/`（约 13 模块）+ 009 四处薄化改造
+ 2 个配置文件段 + 1 个迁移 + CLI/演示 + 测试；**不含**：真实票房/舆情数据接入、judge 层、
人类锚点、010 接入、pilot 阶段插入与下游字段级交接（G2）

## 前置裁决（已裁决：路径 (b) · 宪章 v2.0.0）

**D1：回放对比路径的策略执行隔离（宪章原则四）**

事实（勘查结论）：宪章原则四第一、二条为不可协商的**必须**——"策略代码**必须**在独立容器
（Docker + gVisor）中执行"、"策略与模拟器之间**必须**通过 IPC 交互，**禁止**同进程传递对象"。
而 009 交付的降级回放对比路径在**同进程**执行人工策略：`agents/screenplay/sandbox_compare.py:112
_instantiate` 直接 `exec(compile(source, "<policy>", "exec"))`，全程不触 `core/sandbox/`
（该包提供 `run_policy` + IPC 协议 + `IOHandler`）。模块名 `sandbox_compare` 与实际行为不符，
属**意图漂移**。

关键补充事实（决定了裁决的形态）：该路径**不向策略交付任何环境对象**——`replay_policy`
只调用 `policy.plan(inputs, config)` 取结构键，`probe` 由**宿主**代为执行
（`sandbox_compare.py:169`），策略既不 `observed()` 也不 `probe()`，未揭示状态 (`_latent`)
对其天然不可达。即冲突**只在容器隔离**，不涉及信息隔离。

**裁决：路径 (b) 已落地**——宪章升 **v2.0.0**（MAJOR），原则四新增"人工编写的降级模式策略"
显式例外条款：替代约束为静态检查 + **执行超时** + 无对象存储凭证/不经网关/不触生成；做梦层
生成或参与自动排名与自动部署的策略**不适用**。理由与迁移计划见
`.specify/memory/constitution.md` 的同步影响报告。

**因此本特性新增两项必须落地的义务**（009 现行亦缺，改造时一并补齐）：

| # | 义务 | 落点 |
| --- | --- | --- |
| ① | 回放对比的策略执行**必须**带执行超时（静态检查不禁循环，防策略内死循环占用宿主；009 现行无超时） | `core/degraded/compare.py` + `agents/dev/sandbox_compare.py`；契约 C14；测试 T1707 |
| ② | **必须**有断言守护"**不向策略交付任何环境对象**"——策略仅 `plan(inputs, config)`，既不 `observed()` 也不 `probe()`，探测由宿主代执行（防后续改动把模拟器或 observation 通道引入策略） | 同上 |

被否决的路径 (a)（沙箱化 + IPC）不采用：该路径本就不存在对象传递，为一个没有信息通道的纯
规划调用引入容器生命周期与 IPC，成本与收益不成比例。

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器确定性与版本冻结 | 四评估器 `deterministic=True`；实现哈希 + 模拟数据源参数入版本号；权重与阈值随树 `config_snapshot` 冻结；改配置即新版本、历史节点不重算 | ✅ 满足 |
| 原则二：节点不可变与全量谱系 | `dev_jobs` 仅承载可变中间态（pending→…→inserted / failed）；节点一次 INSERT；FAILED 节点成本照常入账；谱系走既有 `(policy_version → tree_ids)` | ✅ 满足 |
| 原则三：昂贵动作仅限线上探索 | 回放路径 `max_generation_calls=0`、零 LLM（审计断言）；全部 LLM 经网关计费；无真实渠道调用（真实数据属 G3，届时受预算门禁 + 账单对账） | ✅ 满足 |
| 原则四：沙箱隔离与前缀不可泄露 | 依 **v2.0.0 例外条款**：回放对比的策略为 `plan(inputs, config)` 纯规划形态、不交互模拟器，容器隔离不适用；替代约束（静态检查 + 执行超时 + 无凭证/不经网关）与"无环境对象"断言按前置裁决节的两项义务落地 | ✅ 满足（**附两项落地义务**） |
| 原则五：单向依赖与形态配置化 | 通用件下沉 `core/degraded/`（业务无关，参数化为 agent_id + 可调用）；业务件留 `agents/*`；`dev` 段与权重全配置化；**零形态分支**（静态断言） | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 开发 Agent 不做自动进化（三重机检常驻）；**不引入伪 judge、不引入锚点、不擅改 010**；判据材料逐项标注"无法评价（来源缺失）"、结论恒非"达标"；人工推翻必留痕 | ✅ 满足 |
| 治理：复杂度必须被论证 | 新增 `core/degraded/` 与"判据项提供者"抽象均写入复杂度跟踪表，逐条对照被否决的更简方案 | ✅ 满足 |
| 测试纪律 | TDD（测试任务与实现任务分列）；缺项即报错类断言；覆盖率 ≥85%（口径不降，含 web）；无偏性验收（新增 Agent 发布阻塞） | ✅ 满足 |

**门禁通过。** 原则四的满足**以两项落地义务为前提**（执行超时 + "无环境对象"断言，见上节）——
未落实即视为未通过。新增抽象按治理规则在"复杂度跟踪"表中逐条论证。

## 项目结构

### 文档（此功能）

```text
specs/017-dev-agent-degraded/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── core-degraded.md          # 通用件抽取与 009 行为等价（C1~C3）
│   ├── dev-artifact.md           # 立项组合工件 schema 与标记规则（C4~C6）
│   ├── dev-evaluators.md         # 二门禁 + 二代理 + 合成 + 模拟数据源标注（C7~C10）
│   ├── dev-loop-degraded.md      # 轮次循环/幂等/成本 + 策略通道/最小池门槛/三重机检（C11~C15）
│   └── dev-upgrade-evidence.md   # 全量阈值 + 逐项可评价性 + 覆盖与留痕（C16~C18）
└── tasks.md                      # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/degraded/                    # 新：与业务无关的降级机制（抽取自 009）
├── policy.py                     # 人工策略提交：版本化 + 静态检查接线 + meta（含 no_auto_evolve 审计）
├── compare.py                    # 回放对比引擎：池化 + 匹配键注入 + 最小池门槛 + append-only 报告
│                                 # + 例外条款两项义务（执行超时 / 无环境对象断言）
├── adoption.py                   # 采纳/拒绝留痕 + 部署指针定点改写（复用 core/yaml_edit）
└── evidence.py                   # 判据材料：全量阈值快照 + 判据项逐项可评价性 + 系统结论不可改写

agents/dev/                       # 新：开发 Agent（业务件）
├── __init__.py                   # 包说明（降级模式：记录-回放 + 人工策略 + 回放沙盘）
├── artifact.py                   # TopicSlate / SlateEntry（schema 版本化 + canonical JSON + 内容寻址）
├── config.py                     # dev 段解析与校验（组合区间/标记区间/最小树数/阈值/模拟源参数/生成档）
├── signals.py                    # 确定性模拟数据源（同类型票房回归 + 舆情热度）+ 来源标注
├── loop.py                       # run_dev_round + slate_match_key（只含策略可复现结构键）
├── db.py                         # dev_jobs 运营表 schema（迁移 0010）
├── policy_versions.py            # 薄适配：绑定 agent_id="dev" 与策略接口
├── sandbox_compare.py            # 薄适配：绑定 slate_match_key 与最小树数门槛 + 落实例外两项义务
├── adoption.py                   # 薄适配：绑定 dev/adoptions 目录
├── upgrade_evidence.py           # 薄适配：登记"无 judge / 无锚点"两类来源缺失
├── export_slate.py               # 立项组合导出（供 G2 交接取数，本特性只保证可导出）
└── evaluators/
    ├── _versioning.py            # 实现哈希（按包复制，不可跨包导入——沿用 004~009 口径）
    ├── slate_structure.py        # rule.slate_structure（gate）
    ├── slate_combination.py      # rule.slate_combination（gate，组合层核心）
    ├── genre_regression.py       # proxy.genre_regression（模拟票房回归）
    ├── buzz_heat.py              # proxy.buzz_heat（模拟舆情热度）
    └── composite.py              # gate 短路 + 适用权重归一 + quantize 定点 6 位

agents/screenplay/                # 既有：四处薄化改造（公共 API 与默认目录不变，行为等价）
├── policy_versions.py / sandbox_compare.py / adoption.py / upgrade_evidence.py
core/yaml_edit.py                 # 既有：部署指针定点改写（复用，不改）
configs/movie.yaml / shortdrama.yaml  # 新增 dev 段 + evaluator_weights.dev
ops/migrations/versions/0010_dev_jobs.py
ops/dev.py                        # CLI：produce / submit / compare / adopt|reject / evidence
ops/demo_dev_loop.py              # 离线六步演示（退出码语义同 009）
policies/history/dev/             # 人工策略首版（引导树用）
ops/screenplay.py / README.md     # 008/009 的判据材料路径字面量同步（按 agent 分目录）
tests/unit/test_dev_*.py / test_dev_core_degraded_purity.py   # 含 core/degraded 纯净性断言
tests/unit/test_config_integrity.py  # 登记 dev 加载器 / 权重 / 缺项样例（CONFIG_CLASSES 等字面量清单）
tests/contract/test_dev_contracts.py / test_dev_no_auto_evolve.py
tests/unbiasedness/test_dev_unbiased.py
```

**结构决策**: 判据材料与回放对比机制**不是**某个 Agent 的业务——它们是"降级模式纪律"的
载体（append-only、零 LLM、人工采纳才改指针），第二个降级 Agent 一出现，复制即产生两份会
漂移的治理实现。故下沉 `core/degraded/`（业务无关、参数化为 `agent_id` + 可调用），precedent
为既有的 `core/deployment/`（跨 Agent 的部署门禁与禁止名单同名册）。业务件（工件 schema、
评估器、匹配键、配置解析）留在 `agents/dev/`；009 侧改造保持公共 API 与默认目录不变。
`tests/unit/test_config_integrity.py` 的字面量清单（`CONFIG_CLASSES` / `WEIGHT_AGENTS` /
`REQUIRED_PATHS`）必须同步登记 `dev`，否则新加载器逃逸配置完整性门禁。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 通用件落点 = `core/degraded/`（4 模块）；被否决的替代方案（复制进 `agents/dev/`）写入跟踪表
2. 参数化方式：`agent_id` + 匹配键函数 + 评估器装配 + 判据项提供者，**不用**继承或注册表魔法
3. 判据材料引入"**判据项**"抽象：每项 = 键 + 阈值键 + 提供者可调用；无提供者即"无法评价（来源缺失）"
4. 证据目录改为按 agent 分目录（`calibration/upgrade-events/{agent_id}/{period}.json`），
   009 一并迁移并同步其测试与 quickstart 验证记录
5. 模拟数据源 = 确定性函数 + 夹具（配置参数化），两处标注口径：工件标注 + 报告/材料标注
6. 最小池门槛：可比对树数 < 配置下限即拒绝产出报告（与"无偏性未过不得产出报告"同构）
7. 匹配键只含策略可复现结构键（立项约束 + 组合区间 + 策略版本 + 模型 + 温度 + 输出预算）
8. 形态参数进 `dev` 段：因两形态取值确有差异，`test_form_switch` 的顶层差异键集**显式**加入 `dev`
   （改动范围与理由写入研究记录，不做隐性放宽）
9. 配置完整性门禁的字面量清单（`CONFIG_CLASSES` / `WEIGHT_AGENTS` / `REQUIRED_PATHS`）必须登记 `dev`
10. 回放对比路径的策略执行隔离与原则四冲突（D1）→ **已裁决路径 (b)**：宪章 v2.0.0 新增
    "人工编写的降级模式策略"例外条款，附带两项落地义务（执行超时、"无环境对象"断言）
11. **不做**：真实票房/舆情数据源、judge 层、人类锚点、010 接入、pilot 阶段插入、数据源插拔接口
    （写入规格与本文防回潮）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：`dev_jobs` 表 / TopicSlate / SlateEntry / DevConfig / 判据项 /
  模拟数据源记录 / 状态机 + `dev` 配置 schema 与 `evaluator_weights.dev`
- [contracts/core-degraded.md](contracts/core-degraded.md)（C1~C3）、
  [dev-artifact.md](contracts/dev-artifact.md)（C4~C6）、
  [dev-evaluators.md](contracts/dev-evaluators.md)（C7~C10）、
  [dev-loop-degraded.md](contracts/dev-loop-degraded.md)（C11~C15）、
  [dev-upgrade-evidence.md](contracts/dev-upgrade-evidence.md)（C16~C18）
- [quickstart.md](quickstart.md)：验证命令 + 六步流程 + 验收口径 + 验证记录回填区

## 复杂度跟踪（新增抽象论证）

| 新增抽象 | 为什么需要 | 为什么不选更简方案 |
| --- | --- | --- |
| `core/degraded/`（4 模块抽取） | 降级模式机制要被两个 Agent 使用；复制即两份会漂移的治理实现（append-only、零 LLM、人工采纳才改指针都在其中） | 复制进 `agents/dev/`（009 式重复）：省一次改造，但从此每处纪律修正要改两遍，且两份实现的分歧无法被任何测试发现 |
| 判据项（evidence item）抽象 | 规格 FR-010 要求"全量声明阈值 + 逐项可评价性"；现有实现把四项判据写死在函数里，无法表达"来源缺失" | 在 dev 侧另写一份判据材料：与 009 的 schema 分叉，违背规格明写的"同一套 schema"，未来补齐锚点还要二次改结构 |
| `dev` 加入 `test_form_switch` 顶层差异键集 | 两形态的选题约束（条目数区间、组合约束、模拟源参数）确实不同，必须由配置承载 | 让两形态取值相同以规避改测试：等于把"形态可配置"变成空话，且第一个真实差异就会撞门 |

## 宪章复核（阶段 1 后）

原则一：四评估器实现哈希 + 数据源参数入版本号，配置快照冻结（C7/C8 断言"改配置即新版本、
历史不重算"）。原则二：`dev_jobs` 只做可变中间态，节点一次 INSERT、FAILED 成本照入（C11/C12）。
原则三：回放零 LLM/零生成（C13），全部调用经网关计费。原则四：依 **v2.0.0 例外条款**成立
（策略为 `plan(inputs, config)` 纯规划、不交互模拟器，容器隔离不适用），两项落地义务
（执行超时、"无环境对象"断言）见"前置裁决"节。原则五：通用件下沉 `core/degraded/` 且业务无关
（C1~C3 的静态断言：`core/degraded/` 不含形态名与 Agent 名），`dev` 段与权重全配置化、零形态
分支（C10）。原则六：三重机检常驻（C14/C15），判据材料"来源缺失"逐项标注且结论不可写为
"达标"（C16~C18）。**门禁通过。**
