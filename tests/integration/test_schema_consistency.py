"""schema 一致性集成断言（T036，PG 集成）。

把 Alembic 迁移真实执行到测试库，再将迁移后的 PG schema 与各方言的 SQLAlchemy
metadata 逐表比对（表 / 列名 / 可空性 / 索引 / immutable 触发器），防止迁移 DDL
与 Table 定义双份维护漂移——树表（core/tree/db.py）与两张运营表
（agents/promo/db.py、agents/visual/db.py）同在守卫范围内。
PG 不可达则整体跳过。
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine, inspect, text

from agents.promo.db import metadata as promo_metadata
from agents.visual.db import metadata as visual_metadata
from core.tree.db import metadata as tree_metadata

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)

# 迁移产物 → 对应的 Table 定义（防漂移守卫范围）
METADATA_BY_TABLE = {
    **{t.name: tree_metadata for t in tree_metadata.tables.values()},
    **{t.name: promo_metadata for t in promo_metadata.tables.values()},
    **{t.name: visual_metadata for t in visual_metadata.tables.values()},
}
EXPECTED_TABLES = set(METADATA_BY_TABLE)
metadata = tree_metadata  # 兼容既有引用（immutable 触发器断言用）


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

    # 清场：整 schema 重置——迁移创建的对象不止树表（0002/0003 还有运营表），
    # 只 drop 树表 + alembic_version 会在"先 alembic upgrade head 再跑测试"的场景下
    # 撞 DuplicateTable（CI 正是先迁移后跑），故一律从空 schema 开始。
    # 注意：重建的 schema 不带 initdb 的默认授权，须显式补 USAGE，
    # 否则应用账号 cineflow_app 连表都看不见（报 relation does not exist）。
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))

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
    expected = {col.name: col.nullable for col in METADATA_BY_TABLE[table].tables[table].columns}
    assert actual == expected


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_索引一致(migrated_engine, table):
    inspector = inspect(migrated_engine)
    actual = {idx["name"] for idx in inspector.get_indexes(table)}
    # PG 会把唯一约束的支撑索引一并报为索引，故期望集 = 显式索引 ∪ 唯一约束名
    declared = METADATA_BY_TABLE[table].tables[table]
    expected = {idx.name for idx in declared.indexes} | {
        c.name for c in declared.constraints if isinstance(c, UniqueConstraint)
    }
    assert actual == expected


@pytest.mark.parametrize("table", sorted(tree_metadata.tables))
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
