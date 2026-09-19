"""迁移 0003：visual_gen_jobs 可变运营表（视觉闭环两段式落盘的中间态载体）

- 唯一键 (round_id, params_hash)：同轮次同参数重复提交直接命中约束（幂等）；
- 运营表不适用 immutable 触发器；节点在评估完成后一次性 INSERT 落树冻结。

修订 ID: 0003_visual_gen_jobs
父修订: 0002_promo_campaigns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_visual_gen_jobs"
down_revision: str | Sequence[str] | None = "0002_promo_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "visual_gen_jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("round_id", sa.Text(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=True),  # 落盘后回填，回填即终态
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('submitted', 'generating', 'completed', 'ingested', 'failed')"
            ),
            nullable=False,
        ),
        sa.Column(
            "cost_usd",
            sa.Float(),
            sa.CheckConstraint("cost_usd >= 0"),
            nullable=False,
            server_default="0",
        ),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("round_id", "params_hash", name="uq_visual_round_params"),
    )
    # 不额外建 round_id 单列索引：唯一约束 (round_id, params_hash) 的索引已能服务
    # round_id 前缀查询（与 agents/visual/db.py 的 Table 定义保持一致，防 schema 漂移）
    # 应用账号对运营表有完整读写权限（与 immutable 树表刻意区分）
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON visual_gen_jobs TO cineflow_app")


def downgrade() -> None:
    op.drop_table("visual_gen_jobs")
