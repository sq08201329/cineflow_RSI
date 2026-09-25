"""迁移 0011：日级回流（本特性唯一的 DB 变更，**两件 DDL**）

- **步骤 ①** `calibration_anchors` 增**可空**列 `metric_date`（归属日，ISO `YYYY-MM-DD`）。
  **无 `server_default`** ⇒ 历史行恒 `NULL`、永不回填；`ALTER TABLE ADD COLUMN` 是 DDL、
  不触发既有 INSERT-only 触发器（`0004` 的 `reject_anchor_mutation()` 只管 `UPDATE`/`DELETE`）
  ⇒ **历史锚点零回改**（宪章原则一/二）。`0011` 之后锚点表**只多这一列**：`collected_at`
  不在该表（采集墙钟由 `promo_daily_metrics.collected_at` 与覆盖视图 `days[].collected_at`
  承载，避免同一事实两处漂移）。
- **步骤 ②** 新建 `promo_daily_metrics`（日级分片记录）：唯一键 **（campaign_id, period）**
  + **INSERT-only 触发器**（镜像 0004 的双方言范式），并回收应用账号的 `UPDATE`/`DELETE`。
  唯一性键落**新表**而不是改 `promo_campaigns`：该表是**运营状态**（状态机要写回 `ingested`）
  且**刻意无触发器**，把"写入即冻结"的纪律挂上去会自相矛盾；新表因此让既有唯一键
  `(round_id, material_id)` 与既有历史行**零改动**。

降级安全：对锚点表只 `DROP COLUMN`、对新表只 `DROP TABLE`，**不触碰任何既有行数据**。

修订 ID: 0011_daily_feedback
父修订: 0010_dev_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_daily_feedback"
down_revision: str | Sequence[str] | None = "0010_dev_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANCHOR_TABLE = "calibration_anchors"
_DAILY_TABLE = "promo_daily_metrics"
_IMMUTABLE_MESSAGE = "daily metric ingest is immutable: {op} on promo_daily_metrics not allowed"

# 来源取值域的唯一属主是 core/billing/runlog.py 的 RUN_SOURCES（此处只是把取值域写进 DDL 的
# CHECK，属"列即契约"；不新增第二份实现、不派生）
_RUN_SOURCES_DDL = "source IN ('real', 'simulated', 'fallback')"


def _jsonb():
    """JSON 列：SQLite 用通用 JSON、PostgreSQL 用 JSONB（与 `agents/promo/db.py` 同一范式）。"""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def sqlite_trigger_statements() -> list[str]:
    """SQLite 版 INSERT-only 触发器 DDL（UPDATE/DELETE 分设，RAISE(ABORT) 拒绝）。"""
    statements = []
    for op_name in ("UPDATE", "DELETE"):
        message = _IMMUTABLE_MESSAGE.format(op=op_name)
        statements.append(
            f"CREATE TRIGGER promo_daily_metrics_immutable_{op_name.lower()} "
            f"BEFORE {op_name} ON {_DAILY_TABLE} BEGIN "
            f"SELECT RAISE(ABORT, '{message}'); "
            f"END"
        )
    return statements


def pg_trigger_statements() -> list[str]:
    """PostgreSQL 版 INSERT-only 触发器 DDL（reject_promo_daily_metric_mutation()）。"""
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


def _install_daily_triggers(dialect: str) -> None:
    if dialect == "sqlite":
        statements = sqlite_trigger_statements()
    elif dialect == "postgresql":
        statements = pg_trigger_statements()
    else:
        raise ValueError(f"不支持的方言：{dialect}")
    for statement in statements:
        op.execute(statement)


def _drop_daily_triggers(dialect: str) -> None:
    if dialect == "sqlite":
        for op_name in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS promo_daily_metrics_immutable_{op_name.lower()}")
    elif dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS promo_daily_metrics_immutable ON {_DAILY_TABLE}")
        op.execute("DROP FUNCTION IF EXISTS reject_promo_daily_metric_mutation()")
    else:
        raise ValueError(f"不支持的方言：{dialect}")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    # 步骤 ①：锚点表增可空归属日列（历史行恒 NULL；ADD COLUMN 不动任何既有行）
    op.execute(f"ALTER TABLE {_ANCHOR_TABLE} ADD COLUMN metric_date TEXT NULL")

    # 步骤 ②：新建日级分片表（唯一键（活动, 周期）+ 存储层拒绝改写）
    op.create_table(
        _DAILY_TABLE,
        sa.Column("ingest_id", sa.Text(), primary_key=True),
        sa.Column("campaign_id", sa.Text(), nullable=False),
        sa.Column("round_id", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("material_id", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column(
            "metric_date",
            sa.Text(),
            sa.CheckConstraint("length(metric_date) = 10"),
            nullable=False,
        ),
        sa.Column("collected_at", sa.Float(), nullable=False),
        sa.Column("platform_timestamp", sa.Float(), nullable=False),
        sa.Column("source", sa.Text(), sa.CheckConstraint(_RUN_SOURCES_DDL), nullable=False),
        sa.Column(
            "snapshot_fingerprint",
            sa.Text(),
            sa.CheckConstraint("length(snapshot_fingerprint) = 64"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("metrics", _jsonb(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        # 唯一性键 =（活动, 周期）：同键再次回流即 DB 层幂等拒绝（零变更、整批不中断）
        sa.UniqueConstraint("campaign_id", "period", name="uq_promo_daily_campaign_period"),
    )
    _install_daily_triggers(dialect)

    if dialect == "postgresql":
        # 应用账号权限最小化：日级记录只写不改（角色由迁移 0001 内建，做法同 0004）
        op.execute(f"GRANT SELECT, INSERT ON {_DAILY_TABLE} TO cineflow_app")
        op.execute(f"REVOKE UPDATE, DELETE ON {_DAILY_TABLE} FROM cineflow_app")
        op.execute("GRANT USAGE ON SCHEMA public TO cineflow_app")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        # 先恢复应用账号权限（表尚在），再拆触发器与表（同 0004 的降级顺序）
        op.execute(f"GRANT UPDATE, DELETE ON {_DAILY_TABLE} TO cineflow_app")
    _drop_daily_triggers(dialect)
    op.drop_table(_DAILY_TABLE)

    # 步骤 ① 的降级：只 DROP COLUMN，不触碰任何既有行数据
    op.execute(f"ALTER TABLE {_ANCHOR_TABLE} DROP COLUMN metric_date")
