"""迁移 0005 sound_gen_jobs 单测（功能 006 / T603，先于实现编写）。

- schema 字段与约束对齐 data-model.md DB 节（可变运营表，两段式落盘中间态）；
- 唯一键 (round_id, params_hash) 幂等拒绝（C1 场景 3 幂等语义的 DB 层底座）；
- gen_type 三类型枚举约束（tts/sfx/music，成本分账粒度，澄清 Q2）；
- status 状态机枚举与成本约束（actual ≤ estimated，实际扣费 ≤ 预估）；
- 迁移纪律断言：修订链 + GRANT（应用账号运营表完整读写 + schema USAGE 显式授予）。
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from agents.sound.db import create_gen_jobs_schema, sound_gen_jobs

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "ops" / "migrations" / "versions" / "0005_sound_gen_jobs.py"

_JOB = {
    "job_id": "j1",
    "round_id": "r1",
    "gen_type": "tts",
    "params_json": '{"seed": 7}',
    "params_hash": "cd" * 32,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-20T00:00:00Z",
}


@pytest.fixture()
def jobs_engine():
    """SQLite 内存库：建 sound_gen_jobs（可变运营表，无 immutable 触发器）。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_gen_jobs_schema(engine)
    return engine


class Test表结构:
    def test_字段集与数据模型一致(self, jobs_engine):
        columns = {c["name"]: c for c in inspect(jobs_engine).get_columns("sound_gen_jobs")}
        assert set(columns) == {
            "job_id",
            "round_id",
            "gen_type",
            "params_json",
            "params_hash",
            "status",
            "estimated_cost_usd",
            "actual_cost_usd",
            "artifact_hash",
            "error",
            "created_at",
        }

    def test_必填字段不可空(self, jobs_engine):
        columns = {c["name"]: c for c in inspect(jobs_engine).get_columns("sound_gen_jobs")}
        # 可变中间态：actual_cost_usd/artifact_hash/error 渲染后才填，允许 NULL
        for name in (
            "job_id",
            "round_id",
            "gen_type",
            "params_json",
            "params_hash",
            "status",
            "estimated_cost_usd",
            "created_at",
        ):
            assert not columns[name]["nullable"], f"{name} 必须 NOT NULL"
        for name in ("actual_cost_usd", "artifact_hash", "error"):
            assert columns[name]["nullable"], f"{name} 必须为可空（两段式中间态）"

    def test_主键为_job_id(self):
        assert [c.name for c in sound_gen_jobs.primary_key.columns] == ["job_id"]

    def test_唯一键_round_params(self):
        unique = [
            tuple(c.name for c in cons.columns)
            for cons in sound_gen_jobs.constraints
            if cons.__class__.__name__ == "UniqueConstraint"
        ]
        assert ("round_id", "params_hash") in unique


class Test唯一键幂等:
    def test_同轮次同参数重复插入被拒(self, jobs_engine):
        with jobs_engine.begin() as conn:
            conn.execute(insert(sound_gen_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(
                    insert(sound_gen_jobs).values(**{**_JOB, "job_id": "j2"})
                )

    def test_不同参数同轮次可共存(self, jobs_engine):
        with jobs_engine.begin() as conn:
            conn.execute(insert(sound_gen_jobs).values(**_JOB))
            conn.execute(
                insert(sound_gen_jobs).values(
                    **{**_JOB, "job_id": "j2", "params_hash": "ef" * 32}
                )
            )
        with jobs_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM sound_gen_jobs")).scalar() == 2


class TestGenType枚举:
    @pytest.mark.parametrize("gen_type", ["tts", "sfx", "music"])
    def test_三类型可入库(self, jobs_engine, gen_type):
        with jobs_engine.begin() as conn:
            conn.execute(
                insert(sound_gen_jobs).values(
                    **{**_JOB, "gen_type": gen_type, "job_id": f"j-{gen_type}"}
                )
            )

    @pytest.mark.parametrize("bad_type", ["voice", "bgm", ""])
    def test_非法类型被_CHECK_拒绝(self, jobs_engine, bad_type):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(sound_gen_jobs).values(**{**_JOB, "gen_type": bad_type}))


class Test状态机与成本约束:
    @pytest.mark.parametrize(
        "status", ["pending", "rendered", "evaluated", "inserted", "failed"]
    )
    def test_状态机全序列可入库(self, jobs_engine, status):
        with jobs_engine.begin() as conn:
            conn.execute(
                insert(sound_gen_jobs).values(
                    **{**_JOB, "status": status, "job_id": f"j-{status}"}
                )
            )

    def test_非法状态被_CHECK_拒绝(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(sound_gen_jobs).values(**{**_JOB, "status": "done"}))

    def test_预估成本为负被_CHECK_拒绝(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(
                    insert(sound_gen_jobs).values(**{**_JOB, "estimated_cost_usd": -0.1})
                )

    def test_实际成本超预估被_CHECK_拒绝(self, jobs_engine):
        """实际扣费 ≤ 预估（research 决策 7/视觉同款纪律）落进 schema。"""
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(
                    insert(sound_gen_jobs).values(**{**_JOB, "actual_cost_usd": 0.6})
                )

    def test_实际成本不超预估可入库(self, jobs_engine):
        with jobs_engine.begin() as conn:
            conn.execute(insert(sound_gen_jobs).values(**{**_JOB, "actual_cost_usd": 0.4}))

    def test_artifact_hash_长度校验(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(sound_gen_jobs).values(**{**_JOB, "artifact_hash": "ab"}))
        with jobs_engine.begin() as conn:
            conn.execute(insert(sound_gen_jobs).values(**{**_JOB, "artifact_hash": "ab" * 32}))


class Test迁移文件纪律:
    @staticmethod
    def _load_migration():
        spec = importlib.util.spec_from_file_location("migration_0005", MIGRATION_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_修订链(self):
        module = self._load_migration()
        assert module.revision == "0005_sound_gen_jobs"
        assert module.down_revision == "0004_calibration_anchors"

    def test_授权纪律(self):
        """运营表应用账号完整读写（与 immutable 树表刻意区分）+ schema USAGE 显式授予。"""
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON sound_gen_jobs TO cineflow_app" in source
        assert "GRANT USAGE ON SCHEMA public TO cineflow_app" in source
