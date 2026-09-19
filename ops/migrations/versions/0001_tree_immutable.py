"""首个迁移：发现树表结构 + immutable 双保险

- tree_nodes / discovery_trees 两张表（DDL 对齐 data-model.md §2）
- reject_mutation() 触发器：拒绝 UPDATE/DELETE（宪章原则二，存储层强制）
- 应用账号 cineflow_app 的 UPDATE/DELETE 权限回收（迁移内建角色，保证可独立执行）

修订 ID: 0001_tree_immutable
父修订: 无
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_tree_immutable"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_trees",
        sa.Column("tree_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("root_id", sa.Text(), nullable=False),
        sa.Column("node_ids", postgresql.JSONB(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
    )
    # 三维谱系过滤索引：(项目, Agent, 策略版本)
    op.create_index(
        "ix_discovery_trees_lineage",
        "discovery_trees",
        ["project_id", "agent_id", "policy_version"],
    )

    op.create_table(
        "tree_nodes",
        sa.Column("node_id", sa.Text(), primary_key=True),
        sa.Column(
            "tree_id",
            sa.Text(),
            sa.ForeignKey("discovery_trees.tree_id"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Text(), sa.ForeignKey("tree_nodes.node_id"), nullable=True),
        sa.Column("depth", sa.Integer(), sa.CheckConstraint("depth >= 0"), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("observation_context", postgresql.JSONB(), nullable=False),
        sa.Column(
            "artifact_hash",
            sa.Text(),
            sa.CheckConstraint("length(artifact_hash) = 64"),
            nullable=False,
        ),
        sa.Column("eval_breakdown", postgresql.JSONB(), nullable=False),
        sa.Column(
            "score",
            sa.Float(),
            sa.CheckConstraint("score >= 0 AND score <= 1"),
            nullable=True,
        ),
        # 存储层只允许终态：planned 属内存态，禁止落盘
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint("status IN ('evaluated', 'failed')"),
            nullable=False,
        ),
        sa.Column("cost", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_tree_nodes_tree_parent", "tree_nodes", ["tree_id", "parent_id"])
    op.create_index("ix_tree_nodes_agent_policy", "tree_nodes", ["agent_id", "policy_version"])

    # immutable 第一防线：触发器拒绝一切 UPDATE/DELETE（对任何连接路径生效）
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'tree data is immutable: % on % not allowed', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("tree_nodes", "discovery_trees"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
        )

    # immutable 第二防线：应用账号权限回收。迁移内建角色（IF NOT EXISTS），保证迁移可独立执行
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cineflow_app') THEN
                CREATE ROLE cineflow_app LOGIN PASSWORD 'cineflow_app';
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT SELECT, INSERT ON tree_nodes, discovery_trees TO cineflow_app")
    op.execute("REVOKE UPDATE, DELETE ON tree_nodes, discovery_trees FROM cineflow_app")
    # 应用账号需要 schema 级 USAGE 才能访问表——不能依赖 initdb 给 PUBLIC 的默认授权
    # （schema 被重建时默认授权会消失，届时会退化成"表不存在"这类误导性报错）
    op.execute("GRANT USAGE ON SCHEMA public TO cineflow_app")


def downgrade() -> None:
    # 先恢复应用账号权限（表尚在），再拆触发器与表
    op.execute("GRANT UPDATE, DELETE ON tree_nodes, discovery_trees TO cineflow_app")
    for table in ("tree_nodes", "discovery_trees"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_mutation()")
    op.drop_table("tree_nodes")
    op.drop_table("discovery_trees")
