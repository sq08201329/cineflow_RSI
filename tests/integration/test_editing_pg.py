"""剪辑 PG 集成测试（功能 007 / T732，真实 PostgreSQL）。

- alembic upgrade head 真实执行 0006：edit_render_jobs 建表（字段/唯一键/CHECK）；
- 唯一键 (round_id, edl_hash) 冲突拒绝（幂等重建的 DB 层底座）；
- 两段式落盘全链路：edit_render_jobs 可变中间态（rendered → inserted）→
  节点一次性 INSERT 冻结；同 round_id 二次触发幂等重建（0 重复行、0 重复扣费）；
- 成本对账（运营表扣减合计 == 树内成本合计）；
- 应用账号 cineflow_app 对运营表完整读写（与 immutable 树表刻意区分）。

PG 不可达则整体跳过；DSN 惯例见 tests/integration/conftest.py。
"""

import copy
import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from agents.editing.config import EditingConfig
from agents.editing.db import edit_render_jobs
from agents.editing.edl import EditDecisionList
from agents.editing.loop import run_editing_round
from agents.editing.platform.simulated import SimulatedEditRenderer
from agents.editing.shots import Scene, SceneStructure, ShotEntry, ShotLibrary
from core.tree.artifacts import LocalArtifactStore
from core.tree.models import NodeStatus
from core.tree.store import create_tree_store
from tests.stubs import StubProxyEvaluator, StubRuleEvaluator

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)

_JOB = {
    "job_id": "pg-j1",
    "round_id": "pg-r0",
    "edl_json": '{"clips": []}',
    "edl_hash": "aa" * 32,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-20T00:00:00Z",
}


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0006）→ downgrade base。"""
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
        # 整 schema 重置（0001~0006 全量重建后补 PUBLIC USAGE，同一期 CI 教训）
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


class Test迁移0006真实执行:
    def test_建表字段与约束(self, migrated_engine):
        with migrated_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'edit_render_jobs'"
                    )
                )
            }
        assert columns == {
            "job_id",
            "round_id",
            "edl_json",
            "edl_hash",
            "status",
            "estimated_cost_usd",
            "actual_cost_usd",
            "artifact_hash",
            "error",
            "created_at",
        }

    def test_唯一键冲突拒绝(self, migrated_engine):
        with migrated_engine.begin() as conn:
            conn.execute(insert(edit_render_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(insert(edit_render_jobs).values(**{**_JOB, "job_id": "pg-j2"}))

    def test_实际成本超预估被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(edit_render_jobs).values(
                        **{**_JOB, "job_id": "pg-j3", "edl_hash": "bb" * 32, "actual_cost_usd": 0.6}
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
                        "WHERE table_name = 'edit_render_jobs' AND grantee = 'cineflow_app'"
                    )
                )
            }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= privileges


def _pg_config() -> EditingConfig:
    """集成配置：真实 movie.yaml + 时长窗口收窄到夹具可行域 + 渲染尺寸缩小提速。"""
    config = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    )
    config["editing"]["target_duration_s"] = 12
    config["editing"]["duration_tolerance_s"] = 8
    config["editing"]["render"].update(width=64, height=48)
    return EditingConfig.from_dict(config)


def _pg_library() -> tuple[ShotLibrary, SceneStructure]:
    """夹具镜头库（6 镜头 3 分区，与 conftest 工厂同构）。"""
    shots = [
        ShotEntry(shot_id=f"shot-{i}", artifact_hash=f"{i:064x}", duration_ms=d, scene_id=s)
        for i, (d, s) in enumerate(
            [
                (4000, "scene-a"),
                (4000, "scene-a"),
                (5000, "scene-b"),
                (4000, "scene-b"),
                (6000, "scene-c"),
                (4000, "scene-c"),
            ],
            start=1,
        )
    ]
    library = ShotLibrary(shots=shots, audio_tracks=("bgm-01",))
    structure = SceneStructure(
        scenes=[
            Scene(scene_id="scene-a", shot_ids=("shot-1", "shot-2")),
            Scene(scene_id="scene-b", shot_ids=("shot-3", "shot-4")),
            Scene(scene_id="scene-c", shot_ids=("shot-5", "shot-6")),
        ],
        shot_library=library,
    )
    return library, structure


def _pg_edls() -> list[EditDecisionList]:
    """两组哈希不同的合法 EDL（两段式全链路最小形态）。"""
    return [
        EditDecisionList(
            clips=[
                {
                    "shot_id": "shot-1",
                    "in_ms": 0,
                    "out_ms": 3000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-3",
                    "in_ms": 0,
                    "out_ms": 4000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-5",
                    "in_ms": 0,
                    "out_ms": 5000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ]
        ),
        EditDecisionList(
            clips=[
                {
                    "shot_id": "shot-2",
                    "in_ms": 0,
                    "out_ms": 3500,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-4",
                    "in_ms": 0,
                    "out_ms": 3000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
                {
                    "shot_id": "shot-6",
                    "in_ms": 0,
                    "out_ms": 4000,
                    "transition": {"type": "cut", "duration_ms": 0},
                },
            ],
            audio=[{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.8}],
        ),
    ]


class _TwoEdlPolicy:
    policy_version = "pg-editing-v1"

    def __init__(self, edls):
        self._edls = edls

    def plan(self, config, inputs):
        return list(self._edls)


class Test两段式落盘全链路:
    def test_中间态到一次性插入与幂等重建(self, migrated_engine, tmp_path):
        config = _pg_config()
        library, structure = _pg_library()
        store = create_tree_store(migrated_engine)
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        adapter = SimulatedEditRenderer(config.render)
        edls = _pg_edls()
        inputs = {"shot_library": library, "scene_structure": structure}
        evaluators = [
            StubRuleEvaluator("rule.duration_compliance"),
            StubProxyEvaluator("proxy.pacing_curve", score=0.8),
        ]
        policy = _TwoEdlPolicy(edls)

        result = run_editing_round(
            round_id="pg-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapter=adapter,
            engine=migrated_engine,
            config=config,
            inputs=inputs,
            evaluators=evaluators,
        )
        assert [j["status"] for j in result.jobs] == ["inserted"] * 2

        # 两段式：运营表中间态已推进到终态 inserted，artifact_hash 回填（rendered 后填）
        with migrated_engine.connect() as conn:
            rows = conn.execute(
                select(edit_render_jobs)
                .where(edit_render_jobs.c.round_id == "pg-r1")
                .order_by(edit_render_jobs.c.job_id)
            ).all()
        assert len(rows) == 2
        for row in rows:
            assert row.status == "inserted"
            assert row.artifact_hash is not None and len(row.artifact_hash) == 64
            assert row.actual_cost_usd <= row.estimated_cost_usd
            # 唯一键分量：edl_hash 为规范化 EDL BLAKE3
            assert row.edl_hash in {e.edl_hash() for e in edls}

        # 节点一次性 INSERT 冻结：root + 2 子节点全部 EVALUATED
        nodes = store.nodes_of(result.tree_id)
        children = [n for n in nodes if n.parent_id is not None]
        assert len(children) == 2
        assert all(n.status is NodeStatus.EVALUATED for n in children)
        assert all(n.policy_version == "pg-editing-v1" for n in children)

        # 成本对账：运营表扣减合计 == 树内成本合计（SC-003 口径）
        assert result.cost_reconciliation["consistent"] is True
        tree_total = sum(n.cost.generation_api_cost_usd for n in children)
        assert tree_total == pytest.approx(result.spent_usd)

        # 幂等重建：同 round_id 二次触发 → 0 重复行、0 重复扣费、结果一致
        second = run_editing_round(
            round_id="pg-r1",
            policy=policy,
            store=store,
            artifacts=artifacts,
            adapter=adapter,
            engine=migrated_engine,
            config=config,
            inputs=inputs,
            evaluators=evaluators,
        )
        assert second.jobs == result.jobs
        assert second.spent_usd == result.spent_usd
        assert adapter.render_calls == 2  # 0 重复渲染
        with migrated_engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM edit_render_jobs WHERE round_id = 'pg-r1'")
            ).scalar()
            node_count = conn.execute(
                text("SELECT COUNT(*) FROM tree_nodes WHERE tree_id = :tid"),
                {"tid": result.tree_id},
            ).scalar()
        assert count == 2  # 唯一键 (round_id, edl_hash) 挡住重复插入
        assert node_count == 3  # root + 2（一次性 INSERT 不重写）
