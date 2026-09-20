"""storyboard_render_jobs 可变运营表的 SQLAlchemy Core 定义（对齐迁移 0007 DDL）。

运营表不适用 immutable 触发器；树节点在评估完成后一次性 INSERT 落盘冻结。
唯一键 (round_id, shotlist_hash) = 幂等语义（ShotList 规范化哈希即回放匹配键）。
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

storyboard_render_jobs = Table(
    "storyboard_render_jobs",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("round_id", Text, nullable=False),
    Column("shotlist_json", Text, nullable=False),
    Column("shotlist_hash", Text, nullable=False),
    Column(
        "status",
        Text,
        CheckConstraint("status IN ('pending', 'rendered', 'evaluated', 'inserted', 'failed')"),
        nullable=False,
    ),
    Column("estimated_cost_usd", Float, CheckConstraint("estimated_cost_usd >= 0"), nullable=False),
    # 两段式中间态：actual_cost_usd/artifact_hash/error 渲染后才填，允许 NULL
    Column(
        "actual_cost_usd",
        Float,
        CheckConstraint("actual_cost_usd >= 0 AND actual_cost_usd <= estimated_cost_usd"),
        nullable=True,
    ),
    Column(
        "artifact_hash",
        Text,
        CheckConstraint("length(artifact_hash) = 64"),
        nullable=True,
    ),
    Column("error", Text, nullable=True),
    Column("created_at", Text, nullable=False),  # ISO8601 UTC
    UniqueConstraint("round_id", "shotlist_hash", name="uq_storyboard_round_shotlist"),
)


def create_render_jobs_schema(engine: Engine) -> None:
    """建运营表（单元测试 SQLite / 本地开发辅助；生产走 Alembic 迁移 0007）。"""
    metadata.create_all(engine)
