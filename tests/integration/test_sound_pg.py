"""声音 PG 集成测试（功能 006 / T626，真实 PostgreSQL）。

- alembic upgrade head 真实执行 0005：sound_gen_jobs 建表（字段/唯一键/CHECK）；
- 唯一键 (round_id, params_hash) 冲突拒绝（幂等重建的 DB 层底座）；
- 两段式落盘全链路：sound_gen_jobs 可变中间态（rendered → inserted）→
  节点一次性 INSERT 冻结；同 round_id 二次触发幂等重建（0 重复行、0 重复扣费）；
- 成本按 gen_type 分账合计对账（运营表分组合计 == 树内成本合计）；
- 应用账号 cineflow_app 对运营表完整读写（与 immutable 树表刻意区分）。

PG 不可达则整体跳过；DSN 惯例见 tests/integration/conftest.py。
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from agents.sound.config import SoundConfig
from agents.sound.db import sound_gen_jobs
from agents.sound.loop import _cost_by_type, run_sound_round
from agents.sound.platform.simulated import SimulatedMusicGen, SimulatedTTSGen
from core.tree.artifacts import LocalArtifactStore
from core.tree.models import NodeStatus
from core.tree.store import create_tree_store

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)

_JOB = {
    "job_id": "pg-j1",
    "round_id": "pg-r0",
    "gen_type": "tts",
    "params_json": '{"seed": 1}',
    "params_hash": "aa" * 32,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-20T00:00:00Z",
}


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0005）→ downgrade base。"""
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
    with engine.begin() as conn:
        # 整 schema 重置（0001~0005 全量重建后补 PUBLIC USAGE，同一期 CI 教训）
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


class Test迁移0005真实执行:
    def test_建表字段与约束(self, migrated_engine):
        with migrated_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'sound_gen_jobs'"
                    )
                )
            }
        assert columns == {
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

    def test_唯一键冲突拒绝(self, migrated_engine):
        with migrated_engine.begin() as conn:
            conn.execute(insert(sound_gen_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(insert(sound_gen_jobs).values(**{**_JOB, "job_id": "pg-j2"}))

    def test_实际成本超预估被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(sound_gen_jobs).values(
                        **{
                            **_JOB,
                            "job_id": "pg-j3",
                            "params_hash": "bb" * 32,
                            "actual_cost_usd": 0.6,
                        }
                    )
                )

    def test_应用账号运营表完整读写(self, migrated_engine):
        """运营表与 immutable 树表刻意区分：cineflow_app 有完整读写权限。"""
        with migrated_engine.connect() as conn:
            privileges = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.table_privileges "
                        "WHERE table_name = 'sound_gen_jobs' AND grantee = 'cineflow_app'"
                    )
                )
            }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= privileges


class _TwoJobPolicy:
    """两组参数（1 TTS + 1 music）：两段式全链路与分账对账的最小形态。"""

    policy_version = "pg-sound-v1"

    def __init__(self, tts_params, music_params):
        self._plans = [
            {"gen_type": "tts", "gen_params": tts_params},
            {"gen_type": "music", "gen_params": music_params},
        ]

    def plan(self, config, inputs):
        return self._plans


class Test两段式落盘全链路:
    def test_中间态到一次性插入与幂等重建(self, migrated_engine, tmp_path):
        config = SoundConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        store = create_tree_store(migrated_engine)
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        adapters = {
            "tts": SimulatedTTSGen(config.simulated_gen, config.sample_rate),
            "music": SimulatedMusicGen(config.simulated_gen, config.sample_rate),
        }
        tts_params = {"gen_type": "tts", "seed": 101, "duration_s": 1.0, "cer_injected": 0.0}
        music_params = {"gen_type": "music", "seed": 102, "duration_s": 1.0}
        inputs = {
            "timing_sheet": {
                "utterances": [{"text": "台词", "start_ms": 0, "end_ms": 1000}],
                "effects": [],
            },
            "mood": "悬疑",
        }
        policy = _TwoJobPolicy(tts_params, music_params)

        result = run_sound_round(
            round_id="pg-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapters=adapters,
            engine=migrated_engine,
            config=config,
            inputs=inputs,
        )
        assert [j["status"] for j in result.jobs] == ["inserted"] * 2

        # 两段式：运营表中间态已推进到终态 inserted，artifact_hash 回填（rendered 后填）
        with migrated_engine.connect() as conn:
            rows = conn.execute(
                select(sound_gen_jobs)
                .where(sound_gen_jobs.c.round_id == "pg-r1")
                .order_by(sound_gen_jobs.c.job_id)
            ).all()
        assert len(rows) == 2
        for row in rows:
            assert row.status == "inserted"
            assert row.artifact_hash is not None and len(row.artifact_hash) == 64
            assert row.actual_cost_usd <= row.estimated_cost_usd

        # 节点一次性 INSERT 冻结：root + 2 子节点全部 EVALUATED
        nodes = store.nodes_of(result.tree_id)
        children = [n for n in nodes if n.parent_id is not None]
        assert len(children) == 2
        assert all(n.status is NodeStatus.EVALUATED for n in children)
        assert all(len(n.eval_breakdown) == 4 for n in children)  # 四分量齐全

        # 分账对账：运营表按 gen_type 分组合计 == 树内成本合计
        by_type = _cost_by_type(migrated_engine, "pg-r1")
        assert by_type["tts"] == pytest.approx(0.4)
        assert by_type["music"] == pytest.approx(0.4)
        assert by_type["total"] == pytest.approx(0.8)
        tree_total = sum(n.cost.generation_api_cost_usd for n in children)
        assert tree_total == pytest.approx(by_type["total"])

        # 幂等重建：同 round_id 二次触发 → 0 重复行、0 重复扣费、结果一致
        second = run_sound_round(
            round_id="pg-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapters=adapters,
            engine=migrated_engine,
            config=config,
            inputs=inputs,
        )
        assert second.jobs == result.jobs
        assert second.spent_usd == result.spent_usd == pytest.approx(0.8)
        with migrated_engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM sound_gen_jobs WHERE round_id = 'pg-r1'")
            ).scalar()
            node_count = conn.execute(
                text("SELECT COUNT(*) FROM tree_nodes WHERE tree_id = :tid"),
                {"tid": result.tree_id},
            ).scalar()
        assert count == 2  # 唯一键挡住重复插入
        assert node_count == 3  # root + 2（一次性 INSERT 不重写）
