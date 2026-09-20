"""分镜 PG 集成测试（功能 008 / T832，真实 PostgreSQL）。

- alembic upgrade head 真实执行 0007：storyboard_render_jobs 建表
  （字段/唯一键 (round_id, shotlist_hash)/CHECK）；
- 唯一键冲突拒绝（幂等重建的 DB 层底座）；
- 两段式落盘全链路：运营表可变中间态（rendered → inserted，shotlist_json 为规范化
  JSON）→ 节点一次性 INSERT 冻结；同 round_id 二次触发幂等重建（0 重复行、0 重复扣费）；
- 成本对账（运营表扣减合计 == 树内成本合计 == 本轮 spent）；
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

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.db import storyboard_render_jobs
from agents.storyboard.loop import run_storyboard_round
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList
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
    "shotlist_json": '{"shots": []}',
    "shotlist_hash": "aa" * 32,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-20T00:00:00Z",
}


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0007）→ downgrade base。"""
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
        # 整 schema 重置（0001~0007 全量重建后补 PUBLIC USAGE，同一期 CI 教训）
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


class Test迁移0007真实执行:
    def test_建表字段与约束(self, migrated_engine):
        with migrated_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'storyboard_render_jobs'"
                    )
                )
            }
        assert columns == {
            "job_id",
            "round_id",
            "shotlist_json",
            "shotlist_hash",
            "status",
            "estimated_cost_usd",
            "actual_cost_usd",
            "artifact_hash",
            "error",
            "created_at",
        }

    def test_唯一键冲突拒绝(self, migrated_engine):
        """唯一键 (round_id, shotlist_hash)：同轮次同 ShotList 重复提交直接命中约束。"""
        with migrated_engine.begin() as conn:
            conn.execute(insert(storyboard_render_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(insert(storyboard_render_jobs).values(**{**_JOB, "job_id": "pg-j2"}))

    def test_实际成本超预估被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(storyboard_render_jobs).values(
                        **{
                            **_JOB,
                            "job_id": "pg-j3",
                            "shotlist_hash": "bb" * 32,
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
                        "WHERE table_name = 'storyboard_render_jobs' AND grantee = 'cineflow_app'"
                    )
                )
            }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= privileges


def _pg_config() -> StoryboardConfig:
    """集成配置：真实 movie.yaml + 渲染尺寸缩小提速。"""
    config = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    )
    config["storyboard"]["render"].update(width=64, height=48)
    return StoryboardConfig.from_dict(config)


def _pg_script() -> ScriptSegment:
    """夹具剧本（3 场景 9 行，含关键行/情绪/轴向基准，与 conftest 工厂同构）。"""
    return ScriptSegment(
        scenes=[
            {
                "scene_id": "scene-1",
                "axis_base": "A",
                "lines": [
                    {
                        "line_id": "s1-l1",
                        "kind": "dialogue",
                        "text": "夜里三点，走廊的灯一闪一闪。",
                        "emotion": "tense",
                    },
                    {
                        "line_id": "s1-l2",
                        "kind": "action",
                        "text": "她握紧门把手，缓缓推开。",
                        "key": True,
                        "emotion": "tense",
                    },
                    {"line_id": "s1-l3", "kind": "dialogue", "text": "有人在吗？"},
                ],
            },
            {
                "scene_id": "scene-2",
                "axis_base": "A",
                "lines": [
                    {
                        "line_id": "s2-l1",
                        "kind": "dialogue",
                        "text": "我不该回来的。",
                        "key": True,
                        "emotion": "sorrow",
                    },
                    {
                        "line_id": "s2-l2",
                        "kind": "action",
                        "text": "窗外的雨敲打着玻璃。",
                        "emotion": "sorrow",
                    },
                    {
                        "line_id": "s2-l3",
                        "kind": "dialogue",
                        "text": "但你回来了。",
                        "emotion": "calm",
                    },
                ],
            },
            {
                "scene_id": "scene-3",
                "axis_base": "B",
                "lines": [
                    {
                        "line_id": "s3-l1",
                        "kind": "action",
                        "text": "两人隔着长长的走廊对视。",
                        "emotion": "awe",
                    },
                    {
                        "line_id": "s3-l2",
                        "kind": "dialogue",
                        "text": "这一次，我不会再走。",
                        "emotion": "joyful",
                    },
                    {
                        "line_id": "s3-l3",
                        "kind": "action",
                        "text": "镜头缓缓拉远，灯光暗下。",
                        "key": True,
                        "emotion": "sorrow",
                    },
                ],
            },
        ]
    )


_SHAPES = [
    ("scene-1", "shot-01", ["s1-l1"], "close_up", "eye_level", "static", 1000),
    ("scene-1", "shot-02", ["s1-l2"], "medium", "over_shoulder", "dolly", 1250),
    ("scene-1", "shot-03", ["s1-l3"], "full", "side", "pan", 1000),
    ("scene-2", "shot-04", ["s2-l1"], "close_up", "low_angle", "static", 1000),
    ("scene-2", "shot-05", ["s2-l2"], "medium", "high_angle", "tilt", 1250),
    ("scene-2", "shot-06", ["s2-l3"], "wide", "eye_level", "handheld", 1500),
    ("scene-3", "shot-07", ["s3-l1"], "medium", "high_angle", "static", 1000),
    ("scene-3", "shot-08", ["s3-l2"], "close_up", "eye_level", "dolly", 1500),
    ("scene-3", "shot-09", ["s3-l3"], "full", "low_angle", "static", 750),
]
_SIDES = {"scene-1": "A", "scene-2": "A", "scene-3": "B"}


def _pg_shotlists() -> list[ShotList]:
    """两组哈希不同的合法 ShotList（两段式全链路最小形态）。"""
    shots = [
        {
            "shot_id": shot_id,
            "scene_id": scene_id,
            "covers": list(covers),
            "shot_size": shot_size,
            "camera": camera,
            "side": _SIDES[scene_id],
            "movement": movement,
            "est_duration_ms": duration,
            "alternatives": 1,
        }
        for (scene_id, shot_id, covers, shot_size, camera, movement, duration) in _SHAPES
    ]
    # 第二组：镜头时整体 +125ms（哈希不同，仍满足三门禁与帧网格口径）
    shifted = [{**shot, "est_duration_ms": shot["est_duration_ms"] + 125} for shot in shots]
    return [ShotList(shots=shots), ShotList(shots=shifted)]


class _TwoShotListPolicy:
    policy_version = "pg-storyboard-v1"

    def __init__(self, shotlists):
        self._shotlists = shotlists

    def plan(self, config, inputs):
        return list(self._shotlists)


class Test两段式落盘全链路:
    def test_中间态到一次性插入与幂等重建(self, migrated_engine, tmp_path):
        config = _pg_config()
        script = _pg_script()
        store = create_tree_store(migrated_engine)
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        adapter = SimulatedStoryboardRenderer()
        shotlists = _pg_shotlists()
        inputs = {"script": script}
        evaluators = [
            StubRuleEvaluator("rule.shot_grammar"),
            StubProxyEvaluator("proxy.emotion_alignment", score=0.8),
        ]
        policy = _TwoShotListPolicy(shotlists)

        result = run_storyboard_round(
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
                select(storyboard_render_jobs)
                .where(storyboard_render_jobs.c.round_id == "pg-r1")
                .order_by(storyboard_render_jobs.c.job_id)
            ).all()
        assert len(rows) == 2
        for row in rows:
            assert row.status == "inserted"
            assert row.artifact_hash is not None and len(row.artifact_hash) == 64
            assert row.actual_cost_usd <= row.estimated_cost_usd
            # 唯一键分量：shotlist_hash 为规范化 ShotList BLAKE3
            assert row.shotlist_hash in {sl.shotlist_hash() for sl in shotlists}
        # shotlist_json 即规范化 JSON（与哈希预像逐字节一致，无二次序列化差异）
        by_hash = {row.shotlist_hash: row for row in rows}
        for shotlist in shotlists:
            assert by_hash[shotlist.shotlist_hash()].shotlist_json == shotlist.canonical_json()

        # 节点一次性 INSERT 冻结：root + 2 子节点全部 EVALUATED（双键观测随节点落盘）
        nodes = store.nodes_of(result.tree_id)
        children = [n for n in nodes if n.parent_id is not None]
        assert len(children) == 2
        assert all(n.status is NodeStatus.EVALUATED for n in children)
        assert all(n.policy_version == "pg-storyboard-v1" for n in children)
        assert {n.observation_context["shotlist_hash"] for n in children} == {
            sl.shotlist_hash() for sl in shotlists
        }
        assert all(
            n.observation_context["gen_params"] == n.observation_context["shotlist"]
            for n in children
        )

        # 成本对账：运营表扣减合计 == 树内成本合计 == 本轮 spent（SC-003 口径）
        assert result.cost_reconciliation["consistent"] is True
        tree_total = sum(n.cost.generation_api_cost_usd for n in children)
        assert tree_total == pytest.approx(result.spent_usd)

        # 幂等重建：同 round_id 二次触发 → 0 重复行、0 重复扣费、结果一致
        second = run_storyboard_round(
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
                text("SELECT COUNT(*) FROM storyboard_render_jobs WHERE round_id = 'pg-r1'")
            ).scalar()
            node_count = conn.execute(
                text("SELECT COUNT(*) FROM tree_nodes WHERE tree_id = :tid"),
                {"tid": result.tree_id},
            ).scalar()
        assert count == 2  # 唯一键 (round_id, shotlist_hash) 挡住重复插入
        assert node_count == 3  # root + 2（一次性 INSERT 不重写）
