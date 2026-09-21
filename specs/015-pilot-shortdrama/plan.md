# 实现计划：短剧形态试水作品（形态配置 + 链式交接 + 端到端可复现样片）

**分支**: `015-pilot-shortdrama` | **日期**: 2026-09-21 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/015-pilot-shortdrama/spec.md` 的功能规格说明（含 2026-09-21 澄清会话三条决议）

## 概要

交付三层：①`core/orchestration/`（**通用轻量 DAG 执行器**：拓扑/环检测、阶段状态机、
断点续跑与输入指纹、账目汇总——零业务概念）；②`agents/pilot/`（**业务链**：四段交接
契约纯映射函数 + 双向快照断言、六阶段定义、试水运行编排、样片包装配）；③
`configs/shortdrama.yaml`（**形态全量配置**，过全部加载器）。产出**自包含样片包**
（成片 + 产物引用 + 清单 + 账目 + 快照 + "模拟生成"标注），同输入同配置逐字节可复现；
形态切换零代码（静态扫描无分支）。澄清决议贯穿：环节内换候选重试（全败才终止）、
样片包交付形态、执行器分层。**零新增第三方依赖，零新 DB 表**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——stdlib（json/hashlib）+ blake3 + pyyaml；各 Agent 既有 loop 与
模拟器；mp4/wav 确定性编码参数复用 004/006/007/008

**存储**: 无新 DB 表；`pilot/runs/`（RunRecord）与 `pilot/packages/{run_id}/`（样片包）
文件化；各阶段落树沿用既有路径

**测试**: pytest；DAG 拓扑与环检测、执行器状态机与断点续跑、四段交接双向快照、
配置完整性（全加载器）、可复现（两次运行逐字节）、成本对账、静态断言（无形态分支 /
执行器零业务概念）

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `core/orchestration/`（业务无关基建）+ `agents/pilot/`（业务侧编排与交接）

**性能目标**: 一次试水运行（全模拟链路、短剧体量 1~3 分钟）< 5 分钟（demo 预算内）

**约束**: 编排自研轻量 DAG（禁 Airflow）；形态差异零代码（静态断言）；交接双向锁定；
断点续跑带输入指纹；样片标注"模拟生成"；不使用版权素材；既有门禁不被削弱；覆盖率 ≥85%

**规模/范围**: 4 个 core 模块 + 4 个 agents/pilot 模块 + 短剧配置 + CLI + 升级路径文档；
不含真实生成凭证接入、真实投放、作品对外发布

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：版本冻结 | 配置指纹与评估器版本随运行记录冻结；样片包记录配置指纹 | ✅ 满足 |
| 原则二：不可变与成本 | 各阶段落树沿用既有路径；账目对账零差异；历史节点零修改 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 编排触发真实执行路径（非回放）；全模拟路径零真实花费；无新昂贵动作 | ✅ 满足 |
| 原则四：沙箱与前缀 | 不涉及策略执行；无新信息面 | ✅ 满足 |
| 原则五：单向依赖与配置化 | **本特性核心**：形态差异全在 `configs/shortdrama.yaml`（静态断言无代码分支）；`core/orchestration` 零业务概念；自研 DAG（禁 Airflow） | ✅ 满足 |
| 原则六：诚实边界 | 样片包与清单**强制标注"模拟生成"**；不使用版权素材；上游不合格下游拒绝（不静默降级）；升级路径文档化 | ✅ 满足 |
| 测试纪律 | TDD；双向快照与静态断言；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/015-pilot-shortdrama/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── orchestration.md   # 通用执行器（C1~C4）
│   ├── handoffs.md        # 四段交接（C5~C9）
│   └── pilot-run.md       # 试水运行与样片包（C10~C13）
└── tasks.md               # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/orchestration/
├── models.py       # StageSpec/StageState/RunRecord（frozen）+ 状态枚举
├── dag.py          # 轻量 DAG：依赖校验、环检测、拓扑序
├── executor.py     # 执行器：阶段状态机、断点续跑、输入指纹校验（零业务概念）
└── ledger.py       # 账目汇总与对账
agents/pilot/
├── handoffs.py     # 四段交接纯映射（script→segment / shotlist→gen_params /
│                   # av→edit_inputs / reel→promo_materials）+ 双向快照断言
├── stages.py       # 六阶段 StageSpec 定义（调用各 Agent 既有 loop 入口）
├── pilot.py        # 试水运行编排（预检 → DAG 执行 → 样片包）
└── package.py      # 样片包装配（manifest 含"模拟生成" + reel + products + cost + state）
configs/shortdrama.yaml   # 形态全量配置（过全部加载器）
pilot/                    # 数据目录（runs/ 与 packages/，git 版本化）
ops/pilot.py              # CLI：run / resume / inspect
docs/二期升级路径-真实生成与投放.md  # B/C 凭证清单、预算口径、切换方式（+ 结构化清单）
tests/unit/test_orchestration_*.py / test_pilot_*.py / test_config_integrity.py
tests/contract/test_pilot_contracts.py
```

**结构决策**: 执行器与业务链**分层**（澄清 Q3）——通用执行器入 core（可复用、零业务
概念、静态断言），链定义与交接契约入 `agents/pilot/`（业务侧）；短剧配置是"零代码
切换"的唯一形态载体。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 分层：core/orchestration + agents/pilot（澄清 Q3）
2. 短剧配置必须过全部加载器（配置完整性测试 = 零代码切换的真实检验）
3. 四段交接 = 纯映射函数 + 双向快照断言
4. 环节内换候选重试、全败才终止（澄清 Q1）
5. 断点续跑 = 文件化 RunRecord + 输入指纹（素材 + 配置）
6. 样片包 = 自包含目录（澄清 Q2）
7. 可复现：固定种子 + 单线程确定性编码（既有参数）
8. 零代码形态分支的静态断言
9. 升级路径文档 + 结构化清单（机检字段）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：FormConfig/StageSpec/StageState/RunRecord/HandoffContract/
  PilotPackage/PilotCostLedger/UpgradePath + 文件 schema + 状态机
- [contracts/orchestration.md](contracts/orchestration.md)（C1~C4）、
  [handoffs.md](contracts/handoffs.md)（C5~C9）、
  [pilot-run.md](contracts/pilot-run.md)（C10~C13）
- [quickstart.md](quickstart.md)：验证命令 + 六步流程 + 里程碑验收口径

## 宪章复核（阶段 1 后）

原则五的三条（形态全配置、自研 DAG、执行器零业务概念）各有静态断言承载；原则六的
诚实边界（"模拟生成"标注、不用版权素材、拒绝语义）落进契约与清单；原则二的成本对账
零差异为机检项。**无新增违规，门禁通过。**
