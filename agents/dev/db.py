"""dev_jobs 可变运营表的 SQLAlchemy Core 定义（对齐迁移 0010 DDL）。

运营表不适用 immutable 触发器；树节点在评估完成后一次性 INSERT 落盘冻结。
唯一键 **(round_id, params_hash)** = 幂等语义——本环节**单一产出**（澄清第 8 条：立项组合
是唯一交付物、不设阶段划分），故 009 的 `stage` 列在此**删去而非置空**（分阶段粒度在此不适用）。
`inputs_json` 即规范化立项约束 + 形态参数（组合区间/标记区间/生成档），`params_hash` 为其
BLAKE3——列内容自可复核（同 008/009 的 params_json 口径）。
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

dev_jobs = Table(
    "dev_jobs",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("round_id", Text, nullable=False),
    Column("policy_version", Text, nullable=False),  # 人工策略版本（BLAKE3 前 12 位）
    Column("inputs_json", Text, nullable=False),  # 规范化立项约束 + 形态参数 JSON
    Column("params_hash", Text, nullable=False),
    # 网关缓存键与响应哈希：回放核对依据（命中缓存即逐字节复现，原则三）
    Column("cache_key", Text, nullable=True),
    Column("response_hash", Text, nullable=True),
    Column(
        "status",
        Text,
        CheckConstraint("status IN ('pending', 'generated', 'evaluated', 'inserted', 'failed')"),
        nullable=False,
    ),
    Column("estimated_cost_usd", Float, CheckConstraint("estimated_cost_usd >= 0"), nullable=False),
    # 两段式中间态：actual_cost_usd/artifact_hash/error 生成取件后才填，允许 NULL
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
    UniqueConstraint("round_id", "params_hash", name="uq_dev_round_params"),
)


def create_jobs_schema(engine: Engine) -> None:
    """建运营表（单元测试 SQLite / 本地开发辅助；生产走 Alembic 迁移 0010）。"""
    metadata.create_all(engine)
