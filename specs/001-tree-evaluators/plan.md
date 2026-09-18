# 实现计划：发现树与评估器框架（L1 基建第一批）

**分支**: `001-tree-evaluators` | **日期**: 2026-09-18 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/001-tree-evaluators/spec.md` 的功能规格说明

## 概要

交付 `core/tree`（发现树：节点/树模型、不可变存储、谱系查询）与 `core/evaluators`
（评估器基类、注册中心、合成评分）两个纯 `core/` 模块。存储侧用 PostgreSQL 触发器 +
权限回收双保险强制 immutable，工件以 BLAKE3 内容寻址存入 S3 兼容对象存储；评估器以
`evaluator_id@version` 注册并实施唯一性与确定性校验；合成评分实现硬规则门禁 + 加权求和，
权重与配置快照随树冻结。验收线：单元测试覆盖率 ≥85%。

## 技术背景

**语言/版本**: Python 3.11+（`uv` 管理依赖与虚拟环境）

**主要依赖**: SQLAlchemy 2.0（Core 层）+ psycopg 3 + Alembic（schema 迁移）、boto3
（S3 兼容对象存储）、blake3（内容寻址哈希）、uuid-utils（RFC 9562 uuid7）

**存储**: PostgreSQL（`tree_nodes` / `discovery_trees` 表，触发器拒绝 UPDATE/DELETE）+
S3 兼容对象存储（开发环境 MinIO，key 即工件 BLAKE3 哈希）

**测试**: pytest + pytest-cov；单元测试用 SQLite 内存库 + 桩对象存储；集成测试用
Docker 化的 PostgreSQL + MinIO（CI 必跑）

**目标平台**: Linux 服务器（开发与 CI 同为 Linux）

**项目类型**: 内部库（monorepo 的 `core/` 部分），一期仅 CLI + JSON 报告形态

**性能目标**: 单条线索 3 万节点规模下（真实分支形态，分支因子约 10）：节点追加
p99 < 50ms、`children()` 枚举（单父节点约 10 个子节点）p99 < 100ms、按三维索引过滤树
p99 < 200ms。注：`children()` 契约全量物化返回，病态宽树（单父节点数万子节点）的下限由
行传输与反序列化决定（实测秒级），不构成目标形态；CI 用集成基准守住不退化。

**约束**: 单元测试覆盖率 ≥ 85%（宪章里程碑门禁）；测试禁止依赖真实昂贵调用；
所有持久化路径只 INSERT；`core/` 零业务依赖（宪章原则五）

**规模/范围**: 2 个包（`core/tree`、`core/evaluators`）、15 条 FR、3 个接口契约；
不含具体生产评估器实现与沙箱

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器确定性与版本冻结 | 注册中心拒绝重复注册与非确定性评估器（human 例外）；`eval_breakdown` 缺 `evaluator_id@version` 即拒写；权重/评估器组合快照冻结进树 | ✅ 满足 |
| 原则二：节点不可变与全量谱系 | `frozen=True` dataclass + PG 触发器拒绝 UPDATE/DELETE + 应用账号 `REVOKE`；`CostRecord` 对失败节点照常入账；三维索引 | ✅ 满足 |
| 原则三：昂贵动作仅限线上探索 | 本特性无生成动作、无回放逻辑；所有 LLM 调用不在范围（网关属后续特性） | ✅ 不触及 |
| 原则四：沙箱隔离与前缀不可泄露 | 沙箱不在本特性范围（规格假设已排除） | ✅ 不适用 |
| 原则五：单向依赖与形态配置化 | 仅新增 `core/tree`、`core/evaluators`，无任何业务依赖；权重从 `configs/` 读取 | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 无自动进化逻辑；human 评估器仅注册与校准字段 | ✅ 不触及 |
| 测试纪律（TDD、覆盖率 ≥85%） | 先写失败测试再实现；pytest-cov 报告作为验收 | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/001-tree-evaluators/
├── plan.md              # 本文件
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── quickstart.md        # 阶段 1 输出
├── contracts/           # 阶段 1 输出（3 份接口契约）
│   ├── tree-store.md
│   ├── evaluator-registry.md
│   └── artifact-store.md
└── tasks.md             # 阶段 2 输出（/skill:speckit-tasks 创建）
```

### 源代码（仓库根目录）

按开发文档 §0.2 的 monorepo 结构落地，本特性只创建以下目录：

```text
core/
├── tree/
│   ├── models.py          # NodeStatus / CostRecord / TreeNode / DiscoveryTree（frozen dataclass）
│   ├── store.py           # TreeStore 接口实现（追加/读取/谱系查询）
│   ├── db.py              # SQLAlchemy Core 表定义 + 触发器 DDL
│   └── artifacts.py       # ArtifactStore 内容寻址存取
├── evaluators/
│   ├── base.py            # EvaluatorKind / EvalResult / Evaluator 抽象基类
│   ├── registry.py        # 注册中心（唯一性 + 确定性校验）
│   ├── composite.py       # composite_score（硬规则门禁 + 加权求和）
│   └── weights.py         # 形态配置权重读取（configs/*.yaml → weights dict）
configs/
└── movie.yaml             # 形态配置（评估器权重读取入口的示例配置）
tests/
├── unit/                  # SQLite 内存库 + 桩对象存储
└── integration/           # Docker 化 PostgreSQL + MinIO（CI 必跑）
ops/
└── migrations/            # Alembic 迁移脚本
```

**结构决策**: 采用开发文档既定的 monorepo 布局（选项 1 变体：库型项目）。`agents/`、
`dreaming/`、`policies/` 目录属后续特性，本特性不创建；`configs/movie.yaml` 仅创建
权重读取所需的最小骨架。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. **immutable 强制手段**：PG 触发器 + 应用账号权限回收双保险（宪章要求存储层强制）
2. **数据访问层**：SQLAlchemy 2.0 Core（不用 ORM 会话——append-only 负载不需要）+ Alembic 迁移
3. **uuid7**：`uuid-utils` 包（Python 3.11 无标准库 uuid7）
4. **对象存储抽象**：`ArtifactStore` 接口 + S3/本地两个实现（测试用本地桩）
5. **测试分层**：SQLite 内存（单元）/ Docker PG+MinIO（集成，CI 必跑）

全部技术选型与宪章"技术栈与架构约束"节一致，无待澄清项。

## 阶段 1：设计与契约

产出：

- [data-model.md](data-model.md)：实体、表结构（含触发器 DDL 要点）、校验规则、状态机
- [contracts/tree-store.md](contracts/tree-store.md)：追加/读取/谱系查询契约
- [contracts/evaluator-registry.md](contracts/evaluator-registry.md)：注册、评估、成对比较、合成评分契约
- [contracts/artifact-store.md](contracts/artifact-store.md)：内容寻址存取契约
- [quickstart.md](quickstart.md)：环境搭建与端到端验证场景（映射规格验收场景）

## 宪章复核（阶段 1 后）

设计产物逐条复核：触发器 DDL 落在 `ops/migrations` 首个迁移中（原则二）；注册校验逻辑
在 `registry.py`（原则一）；`composite_score` 的权重来源标注为"外部注入、来自 configs"
（原则五）；无任何向 `agents/` 的新增依赖。**无新增违规，门禁通过。**
