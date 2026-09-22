# 实现计划：LLM 模型档案与角色路由（多厂商 / 分角色 / 价目随配置冻结）

**分支**: `016-llm-model-routing` | **日期**: 2026-09-22 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/016-llm-model-routing/spec.md` 的功能规格说明（含 2026-09-22 澄清会话两条决议）

## 概要

把 LLM 接入从"一对全局环境变量只能连一家"升级为：**配置化的模型档案**（端点 / 凭证
环境变量名 / 价目 / 价目口径备注）+ **角色路由**（固定枚举角色 → 档案，单层，未映射
枚举内角色回落显式默认档案）+ **价目随配置快照冻结**（改价不污染历史成本）。同时
消除凭证假阳性：凭证只按档案声明的变量名读取，`HttpBackend` 不再隐式读 `OPENAI_*`；
核查器 / 冒烟器 / 升级清单 / 机检锁以配置为权威同步。**核心纪律：协议与路由进代码，
价目与端点进配置**——价目内置即历史成本不可复现（原则一）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——stdlib + 既有网关与配置栈

**存储**: 无新 DB 表；档案快照并入各 Agent 的 `config_snapshot["llm_profiles"]`（随树冻结）

**测试**: pytest；解析/校验/迁移矩阵（缺项即报错）、路由与回落、成本折算与分解、
快照冻结（改价不漂移）、凭证中立（无关 `OPENAI_*` 不参与）、既有网关测试全绿（单档案等价）

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `core/llm_gateway/` 扩展（业务无关）+ 配置与 ops 工具同步

**性能目标**: 档案解析与路由为初始化期开销（<10ms）；每次调用仅多一次字典查找

**约束**: 路由必须在网关内（原则三）；价目随快照冻结（原则一）；全配置化（原则五）；
记账 ≠ 账单与口径局限如实标注（原则六）；既有单档案行为与全部既有测试不变

**规模/范围**: 2 个网关模块（profiles/routing）+ gateway 改造 + 配置段 + 三个 ops/清单
同步；不含非 OpenAI 兼容协议、两维价目（峰谷/缓存命中）、运行时比价路由

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：版本冻结 | **价目随 `config_snapshot["llm_profiles"]` 冻结**；改价只影响新节点（可审计复算） | ✅ 满足 |
| 原则二：不可变与成本 | 历史节点成本口径不变；`CostRecord` 口径不动（分解数据在报告层） | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 路由在网关内；业务代码零厂商/端点字面量（静态断言）；计费/缓存/重试语义不变 | ✅ 满足 |
| 原则四：沙箱与前缀 | 不涉及策略执行 | ✅ 满足 |
| 原则五：单向依赖与配置化 | 档案/映射/默认档案全走 `configs/*.yaml`；形态差异只在取值 | ✅ 满足 |
| 原则六：诚实边界 | 记账 ≠ 账单与价目口径局限如实标注；缺项即报错；零价目须显式声明"零边际成本" | ✅ 满足 |
| 测试纪律 | TDD；缺项/枚举外/迁移/冻结可证伪断言；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/016-llm-model-routing/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── profiles.md         # 档案解析/校验/默认与迁移（C1~C3）
│   ├── gateway-routing.md  # 路由/后端选择/成本折算分解/快照（C4~C7）
│   └── readiness-sync.md   # 就绪核查器/冒烟器/清单锁（C8~C10）
└── tasks.md                # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/llm_gateway/
├── profiles.py     # ModelProfile / RoleRouting / ProfileSnapshot 解析与校验 + 旧配置迁移
├── routing.py      # 角色 → 档案解析（单层、枚举校验、默认回落）
└── gateway.py      # 按角色取档案 → 后端与价目；BackendResult 带 role/profile_id；cost_breakdown()
core/llm_gateway/backends/http.py   # 构造入参化（端点/密钥由路由层注入，不隐式读 OPENAI_*）
configs/movie.yaml / shortdrama.yaml  # 新增 llm 段（profiles + roles + default_profile）
ops/check_credentials.py  # 读配置档案生成就绪矩阵（标注档案/用途/旧变量名）
ops/smoke_llm.py          # --profile（--model 兼容）；--round 改写角色映射
docs/pilot-upgrade-manifest.json  # 变量与档案登记；机检锁以配置为权威
tests/unit/test_llm_profiles.py / test_llm_routing.py / test_gateway_cost_breakdown.py
tests/contract/test_llm_profile_contracts.py
```

**结构决策**: 档案与路由属网关层（原则三：LLM 调用唯一入口）；价目与端点声明属配置
（原则一/五）；核查器/冒烟器/清单改为"以配置为权威"（避免第三份真相）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 落点 = `core/llm_gateway/`（profiles/routing + gateway 改造）
2. 角色固定枚举（按既有调用点盘点：generation/judge/dreaming_candidates/copywriting）
3. 快照冻结"档案集合"（含价目与备注，不含密钥）
4. 单档案自动认定默认；多档案强制显式；映射单层
5. 旧扁平配置自动映射为单档案，映射规则明示（不静默兼容）
6. 凭证只按档案声明读取；`HttpBackend` 构造入参化（消除假阳性）
7. 成本分解在网关累积、报告层呈现；`CostRecord` 口径不变
8. 核查器/冒烟器/清单以配置为权威 + 机检锁
9. **不做**：价目内置、运行时比价路由、两维价目（写进规格防回潮）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：ModelProfile/RoleRouting/RouteDecision/ProfileSnapshot/
  CostBreakdown/CredentialItem + `llm` 配置 schema + 时序
- [contracts/profiles.md](contracts/profiles.md)（C1~C3）、
  [gateway-routing.md](contracts/gateway-routing.md)（C4~C7）、
  [readiness-sync.md](contracts/readiness-sync.md)（C8~C10）
- [quickstart.md](quickstart.md)：验证命令 + 六步流程 + 验收口径

## 宪章复核（阶段 1 后）

价目冻结（C7 断言"改价不漂移"）落进契约；路由只在网关（C4/C5 + 静态断言）；
缺项与枚举外 100% 报错（C1~C3）；记账非账单与口径局限进报告（C6/C8）。
**无新增违规，门禁通过。**
