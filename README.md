# CineFlow：影视全 Agents 自我进化生产系统

L1 基建第一批交付：**发现树（core/tree）+ 评估器框架（core/evaluators）**。

- 发现树：探索尝试以不可变节点落盘（frozen dataclass + PostgreSQL 触发器 +
  应用账号权限回收双保险），工件以 BLAKE3 内容寻址存储天然去重，谱系按
  （项目, Agent, 策略版本）三维可查；
- 评估器框架：评估器以 `evaluator_id@version` 全局唯一注册（版本冻结、
  非确定性拒绝、人类锚点例外），合成评分实现硬规则门禁 + 加权求和，
  权重来自形态配置（`configs/*.yaml`）并随树冻结快照。

工程约定以项目宪章 `.specify/memory/constitution.md` 为最高标准
（原则一/二不可协商：评估器版本冻结、节点 immutable + 成本必入账）。

## 目录结构

```text
core/
├── tree/             # 发现树：models / store / db / artifacts / errors
└── evaluators/       # 评估器：base / registry / composite / weights / errors
configs/
└── movie.yaml        # 形态配置示例（评估器权重，切换形态零代码改动）
tests/
├── stubs.py          # 桩评估器唯一定义来源
├── unit/             # SQLite 内存库 + 本地工件存储，全离线
└── integration/      # Docker 化 PostgreSQL + MinIO（无 Docker 自动跳过）
ops/
├── dev.compose.yml   # 本地开发依赖（PostgreSQL 16 + MinIO）
├── alembic.ini       # 迁移配置（DSN 走环境变量 CINEFLOW_PG_DSN）
├── migrations/       # Alembic 迁移（首个迁移含 immutable 触发器 + REVOKE）
├── audit_immutable.py  # immutable 审计（随机抽样复算 score，门禁脚本）
└── demo_tree_eval.py   # 端到端演示（六步场景，输出 JSON 报告）
specs/001-tree-evaluators/  # 本特性的 spec-kit 设计文档（spec/plan/tasks/contracts）
```

## 开发环境搭建

```bash
uv sync   # 安装依赖（Python 3.11 由 .python-version 锁定）
```

## 测试

```bash
# 单元测试（离线，SQLite 内存库）
uv run pytest tests/unit

# 单元测试 + 覆盖率门禁（宪章里程碑：core ≥ 85%）
uv run pytest tests/unit --cov=core --cov-report=term-missing --cov-fail-under=85

# 集成测试（需 Docker；不可达时自动跳过）
docker compose -f ops/dev.compose.yml up -d
uv run alembic -c ops/alembic.ini upgrade head
uv run pytest tests/integration -m integration
```

注意：`ops/dev.compose.yml` 中的账号密码**仅用于本地开发**，不得用于任何共享环境。

## 快速演示

```bash
uv run python ops/demo_tree_eval.py
```

依次演示：immutable 拒绝与一致性哈希、三维谱系查询、失败节点成本入账、
注册校验（重复/非确定性拒绝 + 人类锚点放行）、合成评分（gate/加权）、
配置快照冻结；输出 JSON 报告，全部通过时退出码 0。
演示用 SQLite 内存库，生产切 PostgreSQL/MinIO 只需更换 DSN 与 ArtifactStore 实现。

## immutable 审计（每日门禁）

```bash
uv run python ops/audit_immutable.py   # 默认读 CINEFLOW_PG_DSN，可用 --dsn 覆盖
```

随机抽 100 个历史节点（不足则全量）按冻结快照复算 score 比对，
任一不一致即非零退出并输出 JSON 差异明细。

## spec-kit 工作流

本特性由 spec-kit 驱动，设计文档见 `specs/001-tree-evaluators/`：

- `spec.md`：用户故事与功能需求（US1 树存储 / US2 注册冻结 / US3 合成评分）
- `plan.md` / `research.md` / `data-model.md`：技术计划、选型决策、数据模型
- `contracts/`：TreeStore / ArtifactStore / 评估器注册中心接口契约
- `tasks.md`：任务分解（T001–T036 全部完成）
- `quickstart.md`：端到端验证指南
