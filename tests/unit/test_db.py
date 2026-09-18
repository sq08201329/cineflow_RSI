"""表定义与触发器辅助函数单测（补充覆盖：双方言触发器 DDL 生成与方言分发）。"""

import pytest
from sqlalchemy import create_engine

from core.tree.db import (
    create_immutable_triggers,
    create_schema,
    pg_trigger_statements,
    sqlite_trigger_statements,
)


def test_sqlite_触发器_ddl_覆盖两表两操作():
    statements = sqlite_trigger_statements()
    assert len(statements) == 4
    joined = "\n".join(statements)
    for table in ("tree_nodes", "discovery_trees"):
        for op in ("UPDATE", "DELETE"):
            assert f"BEFORE {op} ON {table}" in joined
    assert "RAISE(ABORT" in joined


def test_pg_触发器_ddl_与迁移一致():
    statements = pg_trigger_statements()
    assert "reject_mutation" in statements[0]
    assert "RAISE EXCEPTION" in statements[0]
    assert "plpgsql" in statements[0]
    joined = "\n".join(statements)
    for table in ("tree_nodes", "discovery_trees"):
        assert f"BEFORE UPDATE OR DELETE ON {table}" in joined


def test_create_schema_sqlite_可用():
    from sqlalchemy import insert

    from core.tree.db import discovery_trees

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    # 验证触发器已随建表安装：对 discovery_trees 的 UPDATE 必须被拒
    with engine.begin() as conn:
        conn.execute(
            insert(discovery_trees).values(
                tree_id="t1",
                project_id="p",
                agent_id="a",
                policy_version="v",
                root_id="r",
                node_ids=["r"],
                config_snapshot={"k": 1},
            )
        )
    with pytest.raises(Exception, match="immutable"):
        with engine.begin() as conn:
            conn.execute(discovery_trees.update().values(project_id="p2"))


def test_不支持方言报错():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as conn:
        original = conn.dialect.name
        conn.dialect.name = "oracle"  # type: ignore[misc]
        try:
            with pytest.raises(ValueError, match="不支持的方言"):
                create_immutable_triggers(conn)
        finally:
            conn.dialect.name = original  # type: ignore[misc]
