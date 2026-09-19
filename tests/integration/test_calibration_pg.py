"""校准 PG 集成测试（功能 010 / T528，真实 PostgreSQL）。

- 0004 迁移真实执行后：INSERT-only 触发器双侧证明（UPDATE/DELETE 均被
  reject_anchor_mutation 拒绝）、唯一键 (node_id, reviewer, round_id) 冲突拒绝、
  score CHECK 域、应用账号权限最小化（SELECT/INSERT 有、UPDATE/DELETE 无）；
- 生效管线对真实 configs/movie.yaml 副本的定点改写（注释保留、新版本号回读）。

PG 不可达则整体跳过（本地无 Docker 的开发机）；DSN 惯例见 tests/integration/conftest.py。
"""

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from core.calibration.config import CalibrationConfig
from core.calibration.ledger import append_ledger
from core.calibration.models import BiasRecord, PairingRecord, ProposalStatus
from core.calibration.refit import confirm_proposal, load_proposal, maybe_propose
from core.evaluators.registry import Registry

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)

_ANCHOR = (
    "INSERT INTO calibration_anchors (anchor_id, node_id, artifact_hash, agent_id, source,"
    " score, reviewer, round_id, created_at) VALUES"
    " ('a1', 'n1', :hash, 'visual', 'human_blind', 0.8, 'r1', 'calib-r1', '2026-09-19T00:00:00Z')"
)


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0004）→ downgrade base。"""
    engine = create_engine(PG_DSN)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 无 PG 环境一律跳过
        pytest.skip(f"PostgreSQL 不可用，跳过集成测试：{exc}")

    import os

    os.environ["CINEFLOW_PG_DSN"] = PG_DSN
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "ops" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", PG_DSN)
    with engine.begin() as conn:
        # 整 schema 重置（0002/0003 还有运营表；重建后补 PUBLIC USAGE，同一期 CI 教训）
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


class TestInsertOnly触发器双侧证明:
    def test_update_被触发器拒绝(self, migrated_engine):
        with migrated_engine.begin() as conn:
            conn.execute(text(_ANCHOR), {"hash": "ab" * 32})
        with pytest.raises(Exception, match="anchor data is immutable"):
            with migrated_engine.begin() as conn:
                conn.execute(text("UPDATE calibration_anchors SET score = 0.1"))

    def test_delete_被触发器拒绝(self, migrated_engine):
        with pytest.raises(Exception, match="anchor data is immutable"):
            with migrated_engine.begin() as conn:
                conn.execute(text("DELETE FROM calibration_anchors"))

    def test_insert_正常(self, migrated_engine):
        with migrated_engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM calibration_anchors")).scalar()
        assert count == 1


class Test唯一键与域约束:
    def test_唯一键冲突拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(text(_ANCHOR.replace("'a1'", "'a2'")), {"hash": "cd" * 32})

    def test_score_越界被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    text(_ANCHOR.replace("'a1'", "'a3'").replace("0.8", "1.2")
                         .replace("'n1'", "'n2'")),
                    {"hash": "ef" * 32},
                )


class Test应用账号权限最小化:
    def test_cineflow_app_仅有_select_insert(self, migrated_engine):
        with migrated_engine.connect() as conn:
            privileges = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.table_privileges "
                        "WHERE table_name = 'calibration_anchors' AND grantee = 'cineflow_app'"
                    )
                )
            }
        assert {"SELECT", "INSERT"} <= privileges
        assert not (privileges & {"UPDATE", "DELETE"})


class Test生效管线对真实配置副本:
    def test_定点改写真实_movie_yaml_副本(self, tmp_path):
        config_copy = tmp_path / "movie.yaml"
        shutil.copy(REPO_ROOT / "configs" / "movie.yaml", config_copy)
        data_dir = tmp_path / "calibration"
        for sub in ("rounds", "ledger", "reports", "proposals"):
            (data_dir / sub).mkdir(parents=True)
        append_ledger(
            data_dir, "visual",
            [BiasRecord(evaluator_key="proxy.aesthetic@1.0.0", period="2026-W38", samples=5,
                        mean_shift=0.2, pearson_r=0.7)],
        )
        cfg = CalibrationConfig.from_yaml(config_copy)
        weights = {
            "rule.format_compliance": 0.0,
            "proxy.aesthetic": 0.25,
            "proxy.identity_consistency": 0.35,
            "proxy.flicker": 0.15,
            "judge.cinematic": 0.25,
        }
        pairs = [
            PairingRecord(anchor_id=f"a{i}", evaluator_key=key,
                          anchor_score=0.65 + i * 0.05, auto_score=auto)
            for i in range(5)
            for key, auto in (
                ("proxy.aesthetic@1.0.0", 0.3 + i * 0.05),
                ("proxy.identity_consistency@1.0.0", 0.5),
                ("proxy.flicker@1.0.0", 0.4),
                ("judge.cinematic@1.0.0", 0.6),
            )
        ]
        proposal = maybe_propose(
            agent_id="visual",
            bias_records=[BiasRecord(evaluator_key="proxy.aesthetic@1.0.0", period="2026-W38",
                                     samples=5, mean_shift=0.2, pearson_r=0.7)],
            pairs=pairs, current_weights=weights, cfg=cfg, has_history=True,
            data_dir=data_dir,
            fixed_keys=frozenset({"rule.format_compliance"}),
        )
        assert proposal is not None

        new_version = confirm_proposal(
            data_dir, config_copy, proposal_id=proposal.proposal_id,
            by="ops-user", registry=Registry(),
        )
        text = config_copy.read_text(encoding="utf-8")
        assert "    rule.format_compliance: gate\n" in text  # gate 行不动
        assert "# gate 表示硬规则门禁" in text  # 注释保留
        assert "# 回放形态参数" in text  # 其他段原样
        assert load_proposal(data_dir, proposal.proposal_id).status is ProposalStatus.CONFIRMED
        assert new_version.startswith("1.0.0+w")
