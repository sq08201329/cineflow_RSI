"""SQLAlchemy 2.0 Core 表定义与不可变触发器（对齐 ops/migrations 首个迁移 DDL）。

单元测试用 SQLite 内存库，故 JSON 列使用可移植类型（JSON + postgresql.JSONB variant）；
`create_schema` 为双方言建表并安装触发器：
- SQLite：SELECT RAISE(ABORT, ...)
- PostgreSQL：plpgsql RAISE EXCEPTION（正式 DDL 含 REVOKE，见 Alembic 迁移）
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Connection,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

metadata = MetaData()


def _jsonb():
    """JSON + postgresql.JSONB variant：SQLite 存文本，PG 落 jsonb。"""
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


discovery_trees = Table(
    "discovery_trees",
    metadata,
    Column("tree_id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("agent_id", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("root_id", Text, nullable=False),
    Column("node_ids", _jsonb(), nullable=False),
    Column("config_snapshot", _jsonb(), nullable=False),
    # 三维谱系过滤索引：(项目, Agent, 策略版本)
    Index("ix_discovery_trees_lineage", "project_id", "agent_id", "policy_version"),
)

tree_nodes = Table(
    "tree_nodes",
    metadata,
    Column("node_id", Text, primary_key=True),
    Column("tree_id", Text, ForeignKey("discovery_trees.tree_id"), nullable=False),
    Column("parent_id", Text, ForeignKey("tree_nodes.node_id"), nullable=True),
    Column("depth", Integer, CheckConstraint("depth >= 0"), nullable=False),
    Column("agent_id", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("prompt", Text, nullable=False, server_default=""),
    Column("observation_context", _jsonb(), nullable=False),
    Column("artifact_hash", Text, CheckConstraint("length(artifact_hash) = 64"), nullable=False),
    Column("eval_breakdown", _jsonb(), nullable=False),
    Column("score", Float, CheckConstraint("score >= 0 AND score <= 1"), nullable=True),
    # 存储层只允许终态：planned 属内存态，禁止落盘
    Column("status", Text, CheckConstraint("status IN ('evaluated', 'failed')"), nullable=False),
    Column("cost", _jsonb(), nullable=False),
    Column("created_at", Float, nullable=False),
    Index("ix_tree_nodes_tree_parent", "tree_id", "parent_id"),
    Index("ix_tree_nodes_agent_policy", "agent_id", "policy_version"),
)

_IMMUTABLE_MESSAGE = "tree data is immutable: {op} on {table} not allowed"

_IMMUTABLE_TABLES = ("tree_nodes", "discovery_trees")


def sqlite_trigger_statements() -> list[str]:
    """SQLite 版不可变触发器 DDL（UPDATE/DELETE 分设，RAISE(ABORT) 拒绝）。"""
    statements = []
    for table in _IMMUTABLE_TABLES:
        for op_name in ("UPDATE", "DELETE"):
            message = _IMMUTABLE_MESSAGE.format(op=op_name, table=table)
            statements.append(
                f"CREATE TRIGGER {table}_immutable_{op_name.lower()} "
                f"BEFORE {op_name} ON {table} BEGIN "
                f"SELECT RAISE(ABORT, '{message}'); "
                f"END"
            )
    return statements


def pg_trigger_statements() -> list[str]:
    """PostgreSQL 版不可变触发器 DDL（reject_mutation()，与首个 Alembic 迁移一致）。"""
    statements = [
        """
        CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'tree data is immutable: % on % not allowed', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    ]
    for table in _IMMUTABLE_TABLES:
        statements.append(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
        )
    return statements


def create_immutable_triggers(conn: Connection) -> None:
    """按连接方言安装不可变触发器（sqlite / postgresql）。"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        statements = sqlite_trigger_statements()
    elif dialect == "postgresql":
        statements = pg_trigger_statements()
    else:
        raise ValueError(f"不支持的方言：{dialect}")
    for statement in statements:
        conn.execute(text(statement))


def create_schema(engine: Engine) -> None:
    """建表并安装不可变触发器（单元测试的 SQLite 与集成的 PG 共用）。"""
    metadata.create_all(engine)
    with engine.begin() as conn:
        create_immutable_triggers(conn)
