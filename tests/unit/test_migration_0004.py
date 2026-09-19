"""迁移 0004 calibration_anchors 单测（功能 010 / T503，先于实现编写）。

- schema 字段与约束对齐 data-model.md DB 节；
- 唯一键 (node_id, reviewer, round_id) 幂等拒绝（FR-003）；
- score CHECK 域 ∈ [0,1]；
- SQLite 侧 INSERT-only 触发器拒绝 UPDATE/DELETE（PG 双侧证明见 T528 集成测试）；
- 迁移纪律断言：GRANT USAGE ON SCHEMA public（一期 CI 教训）与应用账号权限回收。
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from core.calibration.db import (
    calibration_anchors,
    create_anchor_schema,
    pg_trigger_statements,
    sqlite_trigger_statements,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "ops" / "migrations" / "versions" / "0004_calibration_anchors.py"

_ANCHOR = {
    "anchor_id": "a1",
    "node_id": "n1",
    "artifact_hash": "ab" * 32,
    "agent_id": "visual",
    "source": "human_blind",
    "score": 0.8,
    "reviewer": "reviewer-1",
    "round_id": "r1",
    "created_at": "2026-09-19T00:00:00Z",
}


@pytest.fixture()
def anchors_engine():
    """SQLite 内存库：建 calibration_anchors 并安装 INSERT-only 触发器。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_anchor_schema(engine)
    return engine


class Test表结构:
    def test_字段集与可空性与数据模型一致(self, anchors_engine):
        columns = {c.name: c for c in inspect(anchors_engine).get_columns("calibration_anchors")}
        assert set(columns) == {
            "anchor_id",
            "node_id",
            "artifact_hash",
            "agent_id",
            "source",
            "score",
            "reviewer",
            "round_id",
            "created_at",
        }
        for name, column in columns.items():
            assert not column.nullable, f"{name} 必须 NOT NULL"

    def test_主键为_anchor_id(self):
        assert [c.name for c in calibration_anchors.primary_key.columns] == ["anchor_id"]

    def test_唯一键_三元组(self):
        unique = [
            tuple(c.name for c in cons.columns)
            for cons in calibration_anchors.constraints
            if cons.__class__.__name__ == "UniqueConstraint"
        ]
        assert ("node_id", "reviewer", "round_id") in unique


class Test唯一键幂等:
    def test_同键重复插入被拒(self, anchors_engine):
        with anchors_engine.begin() as conn:
            conn.execute(insert(calibration_anchors).values(**_ANCHOR))
        with pytest.raises(IntegrityError):
            with anchors_engine.begin() as conn:
                conn.execute(
                    insert(calibration_anchors).values(**{**_ANCHOR, "anchor_id": "a2"})
                )

    def test_不同_reviewer_同节点可共存(self, anchors_engine):
        with anchors_engine.begin() as conn:
            conn.execute(insert(calibration_anchors).values(**_ANCHOR))
            conn.execute(
                insert(calibration_anchors).values(
                    **{**_ANCHOR, "anchor_id": "a2", "reviewer": "reviewer-2"}
                )
            )
        with anchors_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM calibration_anchors")).scalar() == 2


class TestScore域:
    @pytest.mark.parametrize("bad_score", [-0.1, 1.2])
    def test_越界得分被_CHECK_拒绝(self, anchors_engine, bad_score):
        with pytest.raises(IntegrityError):
            with anchors_engine.begin() as conn:
                conn.execute(
                    insert(calibration_anchors).values(**{**_ANCHOR, "score": bad_score})
                )

    @pytest.mark.parametrize("edge_score", [0.0, 1.0])
    def test_边界得分可入库(self, anchors_engine, edge_score):
        with anchors_engine.begin() as conn:
            conn.execute(insert(calibration_anchors).values(**{**_ANCHOR, "score": edge_score}))


class TestInsertOnly触发器:
    def test_sqlite_触发器_ddl_覆盖两操作(self):
        statements = sqlite_trigger_statements()
        assert len(statements) == 2
        joined = "\n".join(statements)
        for op_name in ("UPDATE", "DELETE"):
            assert f"BEFORE {op_name} ON calibration_anchors" in joined
        assert "RAISE(ABORT" in joined

    def test_update_被拒(self, anchors_engine):
        with anchors_engine.begin() as conn:
            conn.execute(insert(calibration_anchors).values(**_ANCHOR))
        with pytest.raises(Exception, match="immutable"):
            with anchors_engine.begin() as conn:
                conn.execute(calibration_anchors.update().values(score=0.1))

    def test_delete_被拒(self, anchors_engine):
        with anchors_engine.begin() as conn:
            conn.execute(insert(calibration_anchors).values(**_ANCHOR))
        with pytest.raises(Exception, match="immutable"):
            with anchors_engine.begin() as conn:
                conn.execute(calibration_anchors.delete())

    def test_pg_触发器_ddl_与迁移一致(self):
        statements = pg_trigger_statements()
        assert "reject_anchor_mutation" in statements[0]
        assert "RAISE EXCEPTION" in statements[0]
        assert "plpgsql" in statements[0]
        assert "BEFORE UPDATE OR DELETE ON calibration_anchors" in "\n".join(statements)


class Test迁移文件纪律:
    @staticmethod
    def _load_migration():
        spec = importlib.util.spec_from_file_location("migration_0004", MIGRATION_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_修订链(self):
        module = self._load_migration()
        assert module.revision == "0004_calibration_anchors"
        assert module.down_revision == "0003_visual_gen_jobs"

    def test_授权纪律(self):
        """一期 CI 教训：显式 GRANT USAGE ON SCHEMA public + 应用账号权限最小化。"""
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "GRANT USAGE ON SCHEMA public TO cineflow_app" in source
        assert "GRANT SELECT, INSERT ON calibration_anchors TO cineflow_app" in source
        assert "REVOKE UPDATE, DELETE ON calibration_anchors FROM cineflow_app" in source
