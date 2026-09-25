"""promo 运营表与日级分片表的 SQLAlchemy Core 定义（对齐迁移 0002 / 0011 DDL）。

- `promo_campaigns` 是**运营状态**（状态机/幂等键/暂存指标），刻意不加 immutable 触发器；
  树节点由本表 delivered + 指标回流校验后一次性构造 INSERT 落盘冻结。
  **表结构零 DDL**（功能 020 只更新 `node_id`/`metrics` 两列的**语义**与注释）。
- `promo_daily_metrics` 是**日级回流记录**（功能 020，迁移 `0011_daily_feedback`）：
  唯一键 **（campaign_id, period）** + INSERT-only 触发器（写入即冻结），
  即"同一（活动, 周期）重复回流 = 幂等拒绝"的存储层落点。日级分片记录落本表 ⇒
  `promo_campaigns` 的既有唯一键 `(round_id, material_id)` 与历史行**零改动**。
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Connection,
    Float,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from core.billing.runlog import RUN_SOURCES

metadata = MetaData()


def _jsonb():
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


promo_daily_metrics = Table(
    "promo_daily_metrics",
    metadata,
    Column("ingest_id", Text, primary_key=True),
    Column("campaign_id", Text, nullable=False),  # 活动标识的分量之一
    Column("round_id", Text, nullable=False),
    Column("external_id", Text, nullable=False),  # 平台活动 id（平台返回，不编造）
    Column("material_id", Text, nullable=False),
    Column("period", Text, nullable=False),  # 归属周期标签 = period_label(metric_date, cadence)
    Column(
        "metric_date",
        Text,
        CheckConstraint("length(metric_date) = 10"),
        nullable=False,
    ),  # 归属日（平台指标所描述的日期）；**新采集写入路径恒非空**（缺失即显式失败）
    Column("collected_at", Float, nullable=False),  # 采集墙钟（time.time()）
    Column(
        "platform_timestamp", Float, nullable=False
    ),  # 平台时间戳（真值产生时刻）——与归属日不等同
    Column(
        "source",
        Text,
        # 取值域唯一属主 = core/billing/runlog.py 的 RUN_SOURCES：DDL **由其派生**，
        # 不在本模块重复声明取值域字面量（第二份字面量必然漂移）
        CheckConstraint(f"source IN ({', '.join(repr(item) for item in RUN_SOURCES)})"),
        nullable=False,
    ),
    Column(
        "snapshot_fingerprint",
        Text,
        CheckConstraint("length(snapshot_fingerprint) = 64"),
        nullable=False,
    ),  # BLAKE3（64 位小写十六进制）：指标快照内容指纹
    Column("node_id", Text, nullable=False),  # 该周期落树节点的 id（含周期派生）
    Column("metrics", _jsonb(), nullable=False),  # 完整 MetricSnapshot 快照（asdict 形态）
    Column("created_at", Float, nullable=False),  # 落盘墙钟
    # 唯一性键 =（活动, 周期）：同键再次回流即 DB 层幂等拒绝（零变更、整批不中断）
    UniqueConstraint("campaign_id", "period", name="uq_promo_daily_campaign_period"),
)

promo_campaigns = Table(
    "promo_campaigns",
    metadata,
    Column("campaign_id", Text, primary_key=True),
    Column("round_id", Text, nullable=False),
    Column("material_id", Text, nullable=False),
    Column(
        "node_id", Text, nullable=True
    ),  # **最近一次**落盘节点（多周期各一条 ⇒ 不再是终态唯一标识）
    Column(
        "status",
        Text,
        CheckConstraint("status IN ('created', 'delivering', 'delivered', 'ingested', 'failed')"),
        nullable=False,
    ),
    Column(
        "spent_usd", Float, CheckConstraint("spent_usd >= 0"), nullable=False, server_default="0"
    ),
    Column("external_id", Text, nullable=True),
    # 节点构建素材（eval_fragments / material / cost / gen_params）+ **最近一次快照**；
    # 多日快照的正本是 promo_daily_metrics（本列不是"写入一次"的终态）
    Column("metrics", _jsonb(), nullable=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("round_id", "material_id", name="uq_promo_round_material"),
)

_DAILY_IMMUTABLE_MESSAGE = (
    "daily metric ingest is immutable: {op} on promo_daily_metrics not allowed"
)


def sqlite_trigger_statements() -> list[str]:
    """SQLite 版 `promo_daily_metrics` INSERT-only 触发器（镜像 `core/calibration/db.py`）。"""
    statements = []
    for op_name in ("UPDATE", "DELETE"):
        message = _DAILY_IMMUTABLE_MESSAGE.format(op=op_name)
        statements.append(
            f"CREATE TRIGGER promo_daily_metrics_immutable_{op_name.lower()} "
            f"BEFORE {op_name} ON promo_daily_metrics BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); "
            f"END"
        )
    return statements


def pg_trigger_statements() -> list[str]:
    """PostgreSQL 版 `promo_daily_metrics` INSERT-only 触发器（镜像 0011 迁移件）。"""
    return [
        """
        CREATE OR REPLACE FUNCTION reject_promo_daily_metric_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'daily metric ingest is immutable: % on % not allowed',
                TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """,
        "CREATE TRIGGER promo_daily_metrics_immutable BEFORE UPDATE OR DELETE "
        "ON promo_daily_metrics FOR EACH ROW EXECUTE FUNCTION reject_promo_daily_metric_mutation()",
    ]


def create_daily_metric_triggers(conn: Connection) -> None:
    """按连接方言安装 `promo_daily_metrics` 的 INSERT-only 触发器（sqlite / postgresql）。"""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        statements = sqlite_trigger_statements()
    elif dialect == "postgresql":
        statements = pg_trigger_statements()
    else:
        raise ValueError(f"不支持的方言：{dialect}")
    for statement in statements:
        conn.execute(text(statement))


def create_campaigns_schema(engine: Engine) -> None:
    """建运营表与日级分片表（单元测试 SQLite / 本地开发辅助；生产走 Alembic 迁移）。

    **只为 `promo_daily_metrics` 安装 INSERT-only 触发器**——`promo_campaigns` 是运营
    状态表，按其既定口径**保持无触发器**（迁移 0011 同样如此）。
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        create_daily_metric_triggers(conn)
