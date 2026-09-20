"""迁移 0008 screenplay_jobs 单测（功能 009 / T903，先于实现编写）。

- schema 字段与约束对齐 data-model.md DB 节（可变运营表，两段式落盘中间态：
  pending → generated → evaluated → inserted；失败 → failed 且成本照计）；
- stage 枚举 CHECK（outline/scenes/script，分阶段产出的落盘粒度）；
- 唯一键 (round_id, stage, params_hash) 幂等拒绝（C1 场景 2 的 DB 层底座）；
- 成本约束（actual ≤ estimated）与网关缓存键/响应哈希（回放核对）字段；
- 迁移纪律断言：修订链（down_revision=0007）+ GRANT（应用账号运营表完整读写
  + schema USAGE 显式授予）。
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from agents.screenplay.db import create_jobs_schema, screenplay_jobs

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "ops" / "migrations" / "versions" / "0008_screenplay_jobs.py"

_JOB = {
    "job_id": "j1",
    "round_id": "r1",
    "stage": "outline",
    "policy_version": "a1b2c3d4e5f6",
    "params_json": '{"topic": "\u75c5\u623f"}',
    "params_hash": "cd" * 32,
    "cache_key": None,
    "response_hash": None,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-20T00:00:00Z",
}


@pytest.fixture()
def jobs_engine():
    """SQLite 内存库：建 screenplay_jobs（可变运营表，无 immutable 触发器）。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_jobs_schema(engine)
    return engine


class Test表结构:
    def test_字段集与数据模型一致(self, jobs_engine):
        columns = {c["name"]: c for c in inspect(jobs_engine).get_columns("screenplay_jobs")}
        assert set(columns) == {
            "job_id",
            "round_id",
            "stage",
            "policy_version",
            "params_json",
            "params_hash",
            "cache_key",
            "response_hash",
            "status",
            "estimated_cost_usd",
            "actual_cost_usd",
            "artifact_hash",
            "error",
            "created_at",
        }

    def test_必填字段不可空(self, jobs_engine):
        columns = {c["name"]: c for c in inspect(jobs_engine).get_columns("screenplay_jobs")}
        for name in (
            "job_id",
            "round_id",
            "stage",
            "policy_version",
            "params_json",
            "params_hash",
            "status",
            "estimated_cost_usd",
            "created_at",
        ):
            assert not columns[name]["nullable"], f"{name} 必须 NOT NULL"
        # 两段式中间态：生成取件与评估后才填的字段允许 NULL
        for name in (
            "cache_key",
            "response_hash",
            "actual_cost_usd",
            "artifact_hash",
            "error",
        ):
            assert columns[name]["nullable"], f"{name} 必须为可空（两段式中间态）"

    def test_主键为_job_id(self):
        assert [c.name for c in screenplay_jobs.primary_key.columns] == ["job_id"]

    def test_唯一键_round_stage_params(self):
        unique = [
            tuple(c.name for c in cons.columns)
            for cons in screenplay_jobs.constraints
            if cons.__class__.__name__ == "UniqueConstraint"
        ]
        assert ("round_id", "stage", "params_hash") in unique


class Test唯一键幂等:
    def test_同轮次同阶段同参数重复插入被拒(self, jobs_engine):
        with jobs_engine.begin() as conn:
            conn.execute(insert(screenplay_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "job_id": "j2"}))

    @pytest.mark.parametrize(
        "override",
        [{"stage": "scenes"}, {"params_hash": "ef" * 32}],
        ids=["不同阶段", "不同参数哈希"],
    )
    def test_同轮次可分阶段与分参数共存(self, jobs_engine, override):
        with jobs_engine.begin() as conn:
            conn.execute(insert(screenplay_jobs).values(**_JOB))
            conn.execute(insert(screenplay_jobs).values(**{**_JOB, "job_id": "j2", **override}))
        with jobs_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM screenplay_jobs")).scalar() == 2


class Test阶段与状态机枚举:
    @pytest.mark.parametrize("stage", ["outline", "scenes", "script"])
    def test_三阶段枚举可入库(self, jobs_engine, stage):
        with jobs_engine.begin() as conn:
            conn.execute(
                insert(screenplay_jobs).values(**{**_JOB, "stage": stage, "job_id": f"j-{stage}"})
            )

    def test_非法阶段被_CHECK_拒绝(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "stage": "treatment"}))

    @pytest.mark.parametrize("status", ["pending", "generated", "evaluated", "inserted", "failed"])
    def test_状态机全序列可入库(self, jobs_engine, status):
        with jobs_engine.begin() as conn:
            conn.execute(
                insert(screenplay_jobs).values(
                    **{**_JOB, "status": status, "job_id": f"j-{status}"}
                )
            )

    def test_非法状态被_CHECK_拒绝(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "status": "done"}))


class Test成本与工件约束:
    def test_预估成本为负被_CHECK_拒绝(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "estimated_cost_usd": -0.1}))

    def test_实际成本超预估被_CHECK_拒绝(self, jobs_engine):
        """实际扣费 ≤ 预估（003/007/008 同款纪律）落进 schema。"""
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "actual_cost_usd": 0.6}))

    def test_实际成本不超预估可入库(self, jobs_engine):
        with jobs_engine.begin() as conn:
            conn.execute(insert(screenplay_jobs).values(**{**_JOB, "actual_cost_usd": 0.4}))

    def test_artifact_hash_长度校验(self, jobs_engine):
        with pytest.raises(IntegrityError):
            with jobs_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "artifact_hash": "ab"}))
        with jobs_engine.begin() as conn:
            conn.execute(insert(screenplay_jobs).values(**{**_JOB, "artifact_hash": "ab" * 32}))

    def test_网关缓存键与响应哈希可落盘(self, jobs_engine):
        """回放核对依据（C1：缓存键 = 输入摘要 + 模型 + 参数；响应哈希落盘）。"""
        with jobs_engine.begin() as conn:
            conn.execute(
                insert(screenplay_jobs).values(
                    **{
                        **_JOB,
                        "status": "generated",
                        "cache_key": "cache-key-1",
                        "response_hash": "ef" * 32,
                    }
                )
            )
        with jobs_engine.connect() as conn:
            row = conn.execute(text("SELECT cache_key, response_hash FROM screenplay_jobs")).one()
        assert row == ("cache-key-1", "ef" * 32)


class Test迁移文件纪律:
    @staticmethod
    def _load_migration():
        spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_修订链(self):
        module = self._load_migration()
        assert module.revision == "0008_screenplay_jobs"
        assert module.down_revision == "0007_storyboard_render_jobs"

    def test_授权纪律(self):
        """运营表应用账号完整读写（与 immutable 树表刻意区分）+ schema USAGE 显式授予。"""
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON screenplay_jobs TO cineflow_app" in source
        assert "GRANT USAGE ON SCHEMA public TO cineflow_app" in source
