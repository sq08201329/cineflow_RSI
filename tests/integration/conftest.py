"""集成测试公共夹具：Docker 化 PostgreSQL（本地无 Docker 时一律跳过）。

PG DSN 由 CINEFLOW_PG_TEST_DSN 注入，默认指向 ops/dev.compose.yml 的本地开发库。
"""

import os

import pytest
from sqlalchemy import create_engine, text

from core.tree.store import create_tree_store

PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)


@pytest.fixture(scope="module")
def pg_engine():
    from core.tree.db import create_schema, metadata

    engine = create_engine(PG_DSN)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 无 PG 环境一律跳过
        pytest.skip(f"PostgreSQL 不可用，跳过集成测试：{exc}")
    with engine.begin() as conn:  # 隔离命名：drop 后重建，避免污染开发库
        metadata.drop_all(conn)
    create_schema(engine)
    yield engine
    with engine.begin() as conn:
        metadata.drop_all(conn)
    engine.dispose()


@pytest.fixture()
def pg_store(pg_engine):
    return create_tree_store(pg_engine)
