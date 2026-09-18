"""promo_campaigns 可变运营表的 SQLAlchemy Core 定义（对齐迁移 0002 DDL）。

本表是运营状态（状态机/幂等键/暂存指标），刻意不加 immutable 触发器；
树节点由本表 delivered + 指标回流校验后一次性构造 INSERT 落盘冻结。
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Float,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

metadata = MetaData()


def _jsonb():
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


promo_campaigns = Table(
    "promo_campaigns",
    metadata,
    Column("campaign_id", Text, primary_key=True),
    Column("round_id", Text, nullable=False),
    Column("material_id", Text, nullable=False),
    Column("node_id", Text, nullable=True),  # 落盘后回填，回填即终态不再变
    Column(
        "status",
        Text,
        CheckConstraint(
            "status IN ('created', 'delivering', 'delivered', 'ingested', 'failed')"
        ),
        nullable=False,
    ),
    Column("spent_usd", Float, CheckConstraint("spent_usd >= 0"),
           nullable=False, server_default="0"),
    Column("external_id", Text, nullable=True),
    Column("metrics", _jsonb(), nullable=True),  # 回流后写入一次
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("round_id", "material_id", name="uq_promo_round_material"),
)


def create_campaigns_schema(engine: Engine) -> None:
    """建运营表（单元测试 SQLite / 本地开发辅助；生产走 Alembic 迁移 0002）。"""
    metadata.create_all(engine)
