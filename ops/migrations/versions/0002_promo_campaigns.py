"""迁移 0002：promo_campaigns 可变运营表（两段式落盘的中间态载体）

- 唯一键 (round_id, material_id)：同轮次同物料重复投放直接命中约束（幂等）；
- 本表是运营状态，不适用 immutable 触发器（immutable 仍只作用于树两表）；
- 节点由本表状态 delivered 后、指标回流校验通过时一次性构造 INSERT 落树。

修订 ID: 0002_promo_campaigns
父修订: 0001_tree_immutable
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_promo_campaigns"
down_revision: str | Sequence[str] | None = "0001_tree_immutable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "promo_campaigns",
        sa.Column("campaign_id", sa.Text(), primary_key=True),
        sa.Column("round_id", sa.Text(), nullable=False),
        sa.Column("material_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=True),  # 落盘后回填，回填即终态
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('created', 'delivering', 'delivered', 'ingested', 'failed')"
            ),
            nullable=False,
        ),
        sa.Column(
            "spent_usd",
            sa.Float(),
            sa.CheckConstraint("spent_usd >= 0"),
            nullable=False,
            server_default="0",
        ),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),  # 回流后写入一次
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("round_id", "material_id", name="uq_promo_round_material"),
    )
    # 不额外建 round_id 单列索引：唯一约束 (round_id, material_id) 的索引已能服务
    # round_id 前缀查询（与 agents/promo/db.py 的 Table 定义保持一致，防 schema 漂移）
    # 应用账号对运营表有完整读写权限（与 immutable 树表刻意区分）
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON promo_campaigns TO cineflow_app")


def downgrade() -> None:
    op.drop_table("promo_campaigns")
