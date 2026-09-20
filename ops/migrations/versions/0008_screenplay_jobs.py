"""迁移 0008：screenplay_jobs 可变运营表（剧本闭环分阶段两段式落盘的中间态载体）

- 唯一键 (round_id, stage, params_hash)：同轮次同阶段同参数重复提交直接命中约束
  （幂等），阶段是幂等粒度（outline/scenes/script 各自独立 job 与节点）；
- stage 由 CHECK 枚举锁定（分阶段产出的落盘粒度，未知阶段即拒绝）；
- 实际扣费 ≤ 预估（actual_cost_usd <= estimated_cost_usd）落进 CHECK；
- 缓存键/响应哈希为回放核对依据（网关缓存命中即逐字节复现，原则三）；
- 运营表不适用 immutable 触发器；节点在评估完成后一次性 INSERT 落树冻结。

修订 ID: 0008_screenplay_jobs
父修订: 0007_storyboard_render_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_screenplay_jobs"
down_revision: str | Sequence[str] | None = "0007_storyboard_render_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "screenplay_jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("round_id", sa.Text(), nullable=False),
        sa.Column(
            "stage",
            sa.Text(),
            sa.CheckConstraint("stage IN ('outline', 'scenes', 'script')"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=True),  # 网关缓存键（命中即复现）
        sa.Column("response_hash", sa.Text(), nullable=True),  # 网关响应哈希（回放核对）
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('pending', 'generated', 'evaluated', 'inserted', 'failed')"
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
            nullable=True,  # 生成取件时才填（两段式中间态）
        ),
        sa.Column(
            "artifact_hash",
            sa.Text(),
            sa.CheckConstraint("length(artifact_hash) = 64"),
            nullable=True,  # generated 后填（64 位小写 hex，内容寻址）
        ),
        sa.Column("error", sa.Text(), nullable=True),  # failed 时填
        sa.Column("created_at", sa.Text(), nullable=False),  # ISO8601 UTC
        sa.UniqueConstraint(
            "round_id", "stage", "params_hash", name="uq_screenplay_round_stage_params"
        ),
    )
    # 应用账号对运营表有完整读写权限（与 immutable 树表刻意区分，同 0005/0006/0007）
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON screenplay_jobs TO cineflow_app")
    # 显式 schema USAGE：schema 重建后默认授权会消失（一期 CI 教训，同 0004）
    op.execute("GRANT USAGE ON SCHEMA public TO cineflow_app")


def downgrade() -> None:
    op.drop_table("screenplay_jobs")
