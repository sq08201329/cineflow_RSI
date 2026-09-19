"""迁移 0004：calibration_anchors 原始锚点表（INSERT-only 存储层冻结）

- 唯一键 (node_id, reviewer, round_id)：同键重复录入在 DB 层幂等拒绝（FR-003）；
- reject_anchor_mutation() 触发器拒绝 UPDATE/DELETE（宪章原则一/二，复用 0001 模式）；
- 应用账号 cineflow_app 仅 SELECT/INSERT，UPDATE/DELETE 回收；schema USAGE 显式授予
  （一期 CI 教训：不能依赖 initdb 给 PUBLIC 的默认授权）。

修订 ID: 0004_calibration_anchors
父修订: 0003_visual_gen_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_calibration_anchors"
down_revision: str | Sequence[str] | None = "0003_visual_gen_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibration_anchors",
        sa.Column("anchor_id", sa.Text(), primary_key=True),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column(
            "artifact_hash",
            sa.Text(),
            sa.CheckConstraint("length(artifact_hash) = 64"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.Text(),
            sa.CheckConstraint("source IN ('human_blind', 'platform_truth')"),
            nullable=False,
        ),
        sa.Column(
            "score",
            sa.Float(),
            sa.CheckConstraint("score >= 0 AND score <= 1"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("round_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        # 同键重复录入在 DB 层拒绝（FR-003 幂等）
        sa.UniqueConstraint(
            "node_id", "reviewer", "round_id", name="uq_anchor_node_reviewer_round"
        ),
    )

    # 存储层冻结：触发器拒绝一切 UPDATE/DELETE（对任何连接路径生效）
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_anchor_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'anchor data is immutable: % on % not allowed', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER calibration_anchors_immutable BEFORE UPDATE OR DELETE "
        "ON calibration_anchors FOR EACH ROW EXECUTE FUNCTION reject_anchor_mutation()"
    )

    # 应用账号权限最小化：锚点只写不改；角色由迁移 0001 内建
    op.execute("GRANT SELECT, INSERT ON calibration_anchors TO cineflow_app")
    op.execute("REVOKE UPDATE, DELETE ON calibration_anchors FROM cineflow_app")
    # 显式 schema USAGE：schema 重建后默认授权会消失（一期 CI 教训，同 0001）
    op.execute("GRANT USAGE ON SCHEMA public TO cineflow_app")


def downgrade() -> None:
    # 先恢复应用账号权限（表尚在），再拆触发器与表
    op.execute("GRANT UPDATE, DELETE ON calibration_anchors TO cineflow_app")
    op.execute("DROP TRIGGER IF EXISTS calibration_anchors_immutable ON calibration_anchors")
    op.execute("DROP FUNCTION IF EXISTS reject_anchor_mutation()")
    op.drop_table("calibration_anchors")
