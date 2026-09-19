"""calibration_anchors 表定义与 INSERT-only 触发器（对齐迁移 0004 DDL）。

原始锚点属存储层冻结对象（宪章原则一/二，FR-003）：UPDATE/DELETE 由双方言
触发器拒绝——SQLite 用 SELECT RAISE(ABORT)，PostgreSQL 用 plpgsql RAISE EXCEPTION
（正式 DDL 另含应用账号权限回收，见 ops/migrations/versions/0004_calibration_anchors.py）。
"""

from sqlalchemy import (
    CheckConstraint,
    Connection,
    Float,
    MetaData,
    Table,
    Column,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

calibration_anchors = Table(
    "calibration_anchors",
    metadata,
    Column("anchor_id", Text, primary_key=True),
    Column("node_id", Text, nullable=False),
    Column("artifact_hash", Text, CheckConstraint("length(artifact_hash) = 64"), nullable=False),
    Column("agent_id", Text, nullable=False),
    Column(
        "source",
        Text,
        CheckConstraint("source IN ('human_blind', 'platform_truth')"),
        nullable=False,
    ),
    Column("score", Float, CheckConstraint("score >= 0 AND score <= 1"), nullable=False),
    Column("reviewer", Text, nullable=False),
    Column("round_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    # 同键重复录入在 DB 层拒绝（FR-003 幂等）
    UniqueConstraint("node_id", "reviewer", "round_id", name="uq_anchor_node_reviewer_round"),
)

_IMMUTABLE_MESSAGE = "anchor data is immutable: {op} on calibration_anchors not allowed"


def sqlite_trigger_statements() -> list[str]:
    """SQLite 版 INSERT-only 触发器 DDL（UPDATE/DELETE 分设，RAISE(ABORT) 拒绝）。"""
    statements = []
    for op_name in ("UPDATE", "DELETE"):
        message = _IMMUTABLE_MESSAGE.format(op=op_name)
        statements.append(
            f"CREATE TRIGGER calibration_anchors_immutable_{op_name.lower()} "
            f"BEFORE {op_name} ON calibration_anchors BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); "
            f"END"
        )
    return statements


def pg_trigger_statements() -> list[str]:
    """PostgreSQL 版 INSERT-only 触发器 DDL（reject_anchor_mutation()，与迁移 0004 一致）。"""
    return [
        """
        CREATE OR REPLACE FUNCTION reject_anchor_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'anchor data is immutable: % on % not allowed', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """,
        "CREATE TRIGGER calibration_anchors_immutable BEFORE UPDATE OR DELETE "
        "ON calibration_anchors FOR EACH ROW EXECUTE FUNCTION reject_anchor_mutation()",
    ]


def create_anchor_triggers(conn: Connection) -> None:
    """按连接方言安装 INSERT-only 触发器（sqlite / postgresql）。"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        statements = sqlite_trigger_statements()
    elif dialect == "postgresql":
        statements = pg_trigger_statements()
    else:
        raise ValueError(f"不支持的方言：{dialect}")
    for statement in statements:
        conn.execute(text(statement))


def create_anchor_schema(engine: Engine) -> None:
    """建表并安装 INSERT-only 触发器（单元测试 SQLite 与集成 PG 共用）。"""
    metadata.create_all(engine)
    with engine.begin() as conn:
        create_anchor_triggers(conn)
