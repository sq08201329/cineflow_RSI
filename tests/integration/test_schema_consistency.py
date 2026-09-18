"""schema 一致性集成断言（T036，PG 集成）。

把 Alembic 首个迁移真实执行到测试库，再将迁移后的 PG schema 与
core/tree/db.py 的 SQLAlchemy metadata 逐表比对（表 / 列名 / 可空性 / 索引 /
immutable 触发器），防止迁移 DDL 与 Table 定义双份维护漂移。
PG 不可达则整体跳过。
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from core.tree.db import metadata

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)

EXPECTED_TABLES = {"tree_nodes", "discovery_trees"}


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：drop 干净 → alembic upgrade head → 比对 → downgrade base。"""
    engine = create_engine(PG_DSN)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 无 PG 环境一律跳过
        pytest.skip(f"PostgreSQL 不可用，跳过集成测试：{exc}")

    os.environ["CINEFLOW_PG_DSN"] = PG_DSN
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "ops" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", PG_DSN)

    # 清场：先拆掉可能存在的表 / 触发器函数 / alembic 版本表
    with engine.begin() as conn:
        metadata.drop_all(conn)
        conn.execute(text("DROP FUNCTION IF EXISTS reject_mutation()"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    command.upgrade(cfg, "head")
    yield engine

    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


def test_表集合一致(migrated_engine):
    inspector = inspect(migrated_engine)
    assert EXPECTED_TABLES <= set(inspector.get_table_names())


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_列名与可空性一致(migrated_engine, table):
    inspector = inspect(migrated_engine)
    actual = {col["name"]: col["nullable"] for col in inspector.get_columns(table)}
    expected = {col.name: col.nullable for col in metadata.tables[table].columns}
    assert actual == expected


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_索引一致(migrated_engine, table):
    inspector = inspect(migrated_engine)
    actual = {idx["name"] for idx in inspector.get_indexes(table)}
    expected = {idx.name for idx in metadata.tables[table].indexes}
    assert actual == expected


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_immutable_触发器已安装(migrated_engine, table):
    with migrated_engine.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT tgname FROM pg_trigger t JOIN pg_class c ON t.tgrelid = c.oid "
                    "WHERE c.relname = :table AND NOT t.tgisinternal"
                ),
                {"table": table},
            )
        }
    assert f"{table}_immutable" in names
