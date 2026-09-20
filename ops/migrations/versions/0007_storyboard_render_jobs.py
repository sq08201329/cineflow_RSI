"""迁移 0007：storyboard_render_jobs 可变运营表（分镜闭环两段式落盘的中间态载体）

- 唯一键 (round_id, shotlist_hash)：同轮次同 ShotList 重复提交直接命中约束（幂等）；
- 实际扣费 ≤ 预估（actual_cost_usd <= estimated_cost_usd）落进 CHECK；
- 运营表不适用 immutable 触发器；节点在评估完成后一次性 INSERT 落树冻结。

修订 ID: 0007_storyboard_render_jobs
父修订: 0006_edit_render_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_storyboard_render_jobs"
down_revision: str | Sequence[str] | None = "0006_edit_render_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storyboard_render_jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("round_id", sa.Text(), nullable=False),
        sa.Column("shotlist_json", sa.Text(), nullable=False),
        sa.Column("shotlist_hash", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('pending', 'rendered', 'evaluated', 'inserted', 'failed')"
            ),
            nullable=False,
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Float(),
            sa.CheckConstraint("estimated_cost_usd >= 0"),
            nullable=False,
        ),
        sa.Column(
            "actual_cost_usd",
            sa.Float(),
            sa.CheckConstraint("actual_cost_usd >= 0 AND actual_cost_usd <= estimated_cost_usd"),
            nullable=True,  # 渲染完成取件时才填（两段式中间态）
        ),
        sa.Column(
            "artifact_hash",
            sa.Text(),
            sa.CheckConstraint("length(artifact_hash) = 64"),
            nullable=True,  # rendered 后填（64 位小写 hex，内容寻址）
        ),
        sa.Column("error", sa.Text(), nullable=True),  # failed 时填
        sa.Column("created_at", sa.Text(), nullable=False),  # ISO8601 UTC
        sa.UniqueConstraint("round_id", "shotlist_hash", name="uq_storyboard_round_shotlist"),
    )
    # 应用账号对运营表有完整读写权限（与 immutable 树表刻意区分，同 0005/0006）
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON storyboard_render_jobs TO cineflow_app")
    # 显式 schema USAGE：schema 重建后默认授权会消失（一期 CI 教训，同 0004）
    op.execute("GRANT USAGE ON SCHEMA public TO cineflow_app")


def downgrade() -> None:
    op.drop_table("storyboard_render_jobs")
