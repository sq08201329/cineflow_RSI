"""visual_gen_jobs 可变运营表的 SQLAlchemy Core 定义（对齐迁移 0003 DDL）。

运营表不适用 immutable 触发器；树节点在评估完成后一次性 INSERT 落盘冻结。
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

visual_gen_jobs = Table(
    "visual_gen_jobs",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("round_id", Text, nullable=False),
    Column("params_hash", Text, nullable=False),
    Column("node_id", Text, nullable=True),  # 落盘后回填，回填即终态不再变
    Column(
        "status",
        Text,
        CheckConstraint("status IN ('submitted', 'generating', 'completed', 'ingested', 'failed')"),
        nullable=False,
    ),
    Column("cost_usd", Float, CheckConstraint("cost_usd >= 0"), nullable=False, server_default="0"),
    Column("external_id", Text, nullable=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("round_id", "params_hash", name="uq_visual_round_params"),
)


def create_gen_jobs_schema(engine: Engine) -> None:
    """建运营表（单元测试 SQLite / 本地开发辅助；生产走 Alembic 迁移 0003）。"""
    metadata.create_all(engine)
