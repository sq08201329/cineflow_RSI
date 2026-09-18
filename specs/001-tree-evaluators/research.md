# 阶段 0 调研：发现树与评估器框架

**日期**: 2026-09-18 | **关联计划**: [plan.md](plan.md)

本特性技术栈已由宪章锁定（Python 3.11+ / uv / PostgreSQL / S3 兼容对象存储 / BLAKE3），
故调研聚焦于栈内的具体实现决策。无遗留 NEEDS CLARIFICATION 项。

## 决策 1：不可变性的存储层强制手段

**决策**: PostgreSQL `BEFORE UPDATE OR DELETE` 触发器 `RAISE EXCEPTION`，叠加应用数据库
账号的 `REVOKE UPDATE, DELETE` 权限；DDL 随首个 Alembic 迁移落库。

**理由**: 宪章原则二要求"存储层强制"。触发器对任何连接路径（应用、psql、运维脚本）一律
生效，是第一防线；`REVOKE` 让正常应用连接连触发器都到不了，两者互补。迁移脚本管理
保证可追溯、可回滚到重建。

**已评估的替代方案**:
- 仅应用层（repository 不写 UPDATE 语句）：自觉而非强制，被否；
- 数据库 event trigger / 只读副本：过重，运维成本高，被否。

## 决策 2：数据访问层形态

**决策**: SQLAlchemy 2.0 **Core**（`Table` + `insert()`/`select()`）+ psycopg 3 驱动；
不使用 ORM Session/声明式映射；schema 演进用 Alembic 管理（脚本存 `ops/migrations/`）。

**理由**: 负载是纯粹的 append-only 写入 + 简单读取，ORM 的身份映射、脏检查全是负资产，
反而给"禁 UPDATE/DELETE"添噪音；Core 层 SQL 语义直白、触发器行为可预期。Alembic 是
Python 生态事实标准的迁移工具。

**已评估的替代方案**: ORM 声明式（过度设计，被否）；裸 psycopg SQL 字符串（重复样板多、
类型转换手写，被否）。

## 决策 3：节点 ID 生成（uuid7）

**决策**: 依赖 `uuid-utils` 包生成 RFC 9562 uuid7。

**理由**: 开发文档要求 node_id 时间有序（uuid7）；Python 3.11 标准库无 uuid7，自实现
有出错面；`uuid-utils` 是 Rust 实现、广泛使用、零配置。

**已评估的替代方案**: 自实现 RFC 9562（重复造轮子）；ULID（偏离文档明确选型）。

## 决策 4：对象存储抽象

**决策**: 定义窄接口 `ArtifactStore`（`put(bytes) -> hash` / `get(hash) -> bytes` /
`exists(hash) -> bool`），两个实现：`S3ArtifactStore`（boto3，key 即 BLAKE3 十六进制哈希）
与 `LocalArtifactStore`（本地目录，单元测试用）。哈希计算统一由 `blake3` 包完成。

**理由**: 宪章要求内容寻址 + S3 兼容；窄接口让单元测试无需起 MinIO，集成测试再用真实
S3 兼容服务验证契约一致性。

**已评估的替代方案**: 直接散调 boto3（泄漏实现、不可测）；minio-py 专用 SDK（S3 兼容面
更窄，boto3 更通用）。

## 决策 5：测试分层与环境

**决策**: 
- 单元测试：SQLite 内存库（触发器同样可建，`RAISE` 语义可用）+ `LocalArtifactStore`，
  全快全离线；
- 集成测试：Docker 化 PostgreSQL 16 + MinIO（compose 由 pytest fixture 拉起），
  验证触发器、`jsonb` 行为、三维索引查询与真实 S3 语义，CI 必跑；
- 覆盖率：`pytest-cov`，`core/tree` 与 `core/evaluators` 合计 ≥85% 作为合并门禁。

**理由**: 宪章门禁要求覆盖率与 immutable 审计可执行；双层设计兼顾开发速度与真实语义验证。
SQLite/PG 差异（jsonb 等）由集成测试兜底，单元层只测行为语义不测方言特性。

**已评估的替代方案**: testcontainers-python（能力等价但与 compose fixture 二选一，
后者更简单）；全部测试都跑 Docker PG（开发循环太慢）。

## 决策 6：JSON 字段的建模

**决策**: `observation_context`、`eval_breakdown`、`config_snapshot`、`eval_breakdown`
诊断明细等半结构化负载用 PostgreSQL `jsonb` 存储；结构化字段（node_id、tree_id、
parent_id、depth、agent_id、policy_version、score、status、成本各分量、created_at）
用强类型列。

**理由**: 半结构化负载的 schema 由评估器/Agent 侧演化，jsonb 避免频繁迁移；谱系与成本
查询是高频路径，必须落在可索引的强类型列上；三维索引 = `(project_id, agent_id,
policy_version)` 复合 btree + `parent_id` 索引。

**已评估的替代方案**: 全 jsonb（查询与约束弱，被否）；拆子表（评估明细逐行一表——
写入放大、读取拼装复杂，一期过度设计）。
