"""剧本 PG 集成测试（功能 009 / T933，真实 PostgreSQL）。

- alembic upgrade head 真实执行 0008：screenplay_jobs 建表（字段/stage 枚举 CHECK/
  唯一键 (round_id, stage, params_hash)/actual ≤ estimated CHECK）；
- 唯一键冲突拒绝与分阶段共存（幂等重建的 DB 层底座）；
- 两段式落盘全链路：运营表可变中间态（generated → inserted，params_json 为规范化
  JSON、缓存键/响应哈希落盘）→ 节点一次性 INSERT 冻结；同 round_id 二次触发幂等
  重建（0 重复行、0 重复生成、0 重复扣费）；
- 成本对账（运营表扣减合计 + judge 计费增量 == 树内成本合计 == 本轮 spent）；
- 应用账号 cineflow_app 对运营表完整读写（与 immutable 树表刻意区分）。

PG 不可达则整体跳过；DSN 惯例见 tests/integration/conftest.py。
"""

import copy
import json
import os
from pathlib import Path

import blake3
import pytest
import yaml
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.db import screenplay_jobs
from agents.screenplay.loop import run_screenplay_round
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
    "stage": "outline",
    "policy_version": "a1b2c3d4e5f6",
    "params_json": '{"stage": "outline"}',
    "params_hash": "aa" * 32,
    "cache_key": None,
    "response_hash": None,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-20T00:00:00Z",
}


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0008）→ downgrade base。"""
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
        # 整 schema 重置（0001~0008 全量重建后补 PUBLIC USAGE，同一期 CI 教训）
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


class Test迁移0008真实执行:
    def test_建表字段与约束(self, migrated_engine):
        with migrated_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'screenplay_jobs'"
                    )
                )
            }
        assert columns == {
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

    def test_唯一键冲突拒绝(self, migrated_engine):
        """唯一键 (round_id, stage, params_hash)：同轮次同阶段同参数直接命中约束。"""
        with migrated_engine.begin() as conn:
            conn.execute(insert(screenplay_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(insert(screenplay_jobs).values(**{**_JOB, "job_id": "pg-j2"}))

    def test_分阶段与分参数可共存(self, migrated_engine):
        with migrated_engine.begin() as conn:
            conn.execute(
                insert(screenplay_jobs).values(
                    **{**_JOB, "job_id": "pg-j3", "stage": "scenes", "params_hash": "bb" * 32}
                )
            )

    def test_非法阶段被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(screenplay_jobs).values(
                        **{**_JOB, "job_id": "pg-j4", "stage": "treatment"}
                    )
                )

    def test_实际成本超预估被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(screenplay_jobs).values(
                        **{
                            **_JOB,
                            "job_id": "pg-j5",
                            "params_hash": "cc" * 32,
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
                        "WHERE table_name = 'screenplay_jobs' AND grantee = 'cineflow_app'"
                    )
                )
            }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= privileges


def _pg_config() -> ScreenplayConfig:
    """集成配置：真实 movie.yaml + 页数窗口按夹具收窄（9 行 → 3 页）。"""
    config = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    )
    config["screenplay"].update(
        {"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3}
    )
    return ScreenplayConfig.from_dict(config)


_MARKERS = {
    "beats": [
        {"beat_id": beat_id, "act": act, "required": True, "description": f"{beat_id} 节拍"}
        for beat_id, act in (
            ("opening_image", "act1"),
            ("inciting_incident", "act1"),
            ("act1_turn", "act1"),
            ("midpoint", "act2"),
            ("dark_night", "act2"),
            ("act2_turn", "act2"),
            ("climax", "act3"),
            ("resolution", "act3"),
        )
    ],
    "scenes": [
        {
            "scene_id": "scene-1",
            "heading": "内景 - 病房 - 夜",
            "location": "病房",
            "time_marker": 0,
            "characters": ["林静", "陈默"],
            "axis_base": "A",
        }
    ],
    "characters": [
        {"name": "林静", "aliases": ["阿静"]},
        {"name": "陈默", "aliases": ["默哥"]},
    ],
    "lines": [
        {
            "line_id": f"s1-l{index + 1}",
            "scene_id": "scene-1",
            "kind": "dialogue" if index % 3 != 2 else "action",
            "text": f"第 {index + 1} 行（PG 集成夹具）。",
            "character": "林静" if index % 3 != 2 else None,
            "key": index == 0,
            "emotion": ("tense", "sorrow", "calm")[index % 3],
        }
        for index in range(9)
    ],
}


class _PgPolicy:
    """三阶段计划桩（结构化标记齐备；策略版本 = 人工版本口径）。"""

    policy_version = "pg-screenplay-v1"

    def plan(self, inputs, config):
        return {stage: copy.deepcopy(_MARKERS) for stage in ("outline", "scenes", "script")}


def _pg_evaluators() -> list:
    """评估器桩（四 gate + 两 proxy + judge；不触网关，聚焦 DB 链路）。"""
    return [
        StubRuleEvaluator("rule.beat_structure"),
        StubRuleEvaluator("rule.page_minutes"),
        StubRuleEvaluator("rule.scene_character"),
        StubRuleEvaluator("rule.dialogue_action_ratio"),
        StubProxyEvaluator("proxy.entity_consistency", score=0.8),
        StubProxyEvaluator("proxy.timeline_conflict", score=0.6),
        StubProxyEvaluator("judge.dramatic_tension", score=0.7),
    ]


def _pg_run(engine, artifacts, round_id, gateway, *, evaluators=None):
    return run_screenplay_round(
        round_id=round_id,
        policy=_PgPolicy(),
        store=create_tree_store(engine),
        artifacts=artifacts,
        engine=engine,
        gateway=gateway,
        config=_pg_config(),
        inputs={
            "topic": "病房里的三个月",
            "target_duration_min": 3,
            "characters": ["林静", "陈默"],
        },
        evaluators=_pg_evaluators() if evaluators is None else evaluators,
    )


class Test两段式落盘全链路:
    def test_中间态到一次性插入与幂等重建(self, migrated_engine, tmp_path, mock_gateway):
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        calls_before = mock_gateway.call_count
        result = _pg_run(migrated_engine, artifacts, "pg-r1", mock_gateway)
        assert [job["status"] for job in result.jobs] == ["inserted"] * 3
        calls = mock_gateway.call_count - calls_before
        assert calls == 3  # 三阶段生成各一次（网关缓存键落盘可复核）

        # 两段式：运营表中间态推进到终态 inserted，artifact_hash/缓存键/响应哈希回填
        with migrated_engine.connect() as conn:
            rows = conn.execute(
                select(screenplay_jobs)
                .where(screenplay_jobs.c.round_id == "pg-r1")
                .order_by(screenplay_jobs.c.stage)
            ).all()
        assert len(rows) == 3
        for row in rows:
            assert row.status == "inserted"
            assert row.policy_version == "pg-screenplay-v1"
            assert row.artifact_hash is not None and len(row.artifact_hash) == 64
            assert row.cache_key is not None and row.response_hash is not None
            assert row.actual_cost_usd <= row.estimated_cost_usd
            # params_json 即规范化 JSON，params_hash 与其逐字节一致（唯一键分量可复核）
            payload = json.loads(row.params_json)
            assert row.params_json == json.dumps(payload, sort_keys=True, ensure_ascii=False)
            assert row.params_hash == blake3.blake3(row.params_json.encode()).hexdigest()
        assert {row.stage for row in rows} == {"outline", "scenes", "script"}

        # 节点一次性 INSERT 冻结：root + 3 阶段节点（观测携带结构键与网关核对键）
        store = create_tree_store(migrated_engine)
        nodes = store.nodes_of(result.tree_id)
        children = [node for node in nodes if node.parent_id is not None]
        assert len(children) == 3
        assert all(node.status is NodeStatus.EVALUATED for node in children)
        assert all(node.policy_version == "pg-screenplay-v1" for node in children)
        assert {node.observation_context["stage"] for node in children} == {
            "outline",
            "scenes",
            "script",
        }
        assert all(node.observation_context["gen_params"]["stage"] for node in children)
        assert all(node.observation_context["cache_key"] for node in children)

        # 幂等重建：同 round_id 二次触发 0 重复行、0 重复生成
        second = _pg_run(migrated_engine, artifacts, "pg-r1", mock_gateway)
        assert second.jobs == result.jobs
        assert mock_gateway.call_count - calls_before == calls  # 0 重复生成
        assert len(store.nodes_of(result.tree_id)) == 4  # 0 重复节点
        with migrated_engine.connect() as conn:
            total = conn.execute(
                select(func.count())
                .select_from(screenplay_jobs)
                .where(screenplay_jobs.c.round_id == "pg-r1")
            ).scalar()
        assert total == 3  # 0 重复行

        # 成本对账：运营表扣减 + 评估器计费增量 == 树内成本 == 本轮 spent
        assert result.cost_reconciliation["consistent"] is True
        tree_total = sum(
            node.cost.generation_api_cost_usd for node in store.nodes_of(result.tree_id)
        )
        assert tree_total == pytest.approx(
            result.cost_reconciliation["ledger_total_usd"]
            + result.cost_reconciliation["evaluator_cost_usd"]
        )
        assert result.spent_usd == pytest.approx(result.cost_reconciliation["ledger_total_usd"])

    def test_阶段失败成本照计并终态_failed(self, migrated_engine, tmp_path, mock_gateway):
        """网关失败：运营表 failed 行（actual == estimated 照计）+ 节点 FAILED。"""
        from core.llm_gateway.gateway import PermanentBackendError

        class _FailingBackend:
            def __init__(self, inner):
                self.call_count = 0
                self._inner = inner

            def complete(self, prompt, *, model, temperature, max_tokens):
                self.call_count += 1
                if "script 阶段" in prompt:
                    raise PermanentBackendError("后端 4xx（PG 集成注入）")
                return self._inner.complete(
                    prompt, model=model, temperature=temperature, max_tokens=max_tokens
                )

        from core.llm_gateway.gateway import LLMGateway

        gateway = LLMGateway(
            _FailingBackend(mock_gateway.backend),
            price_book=_pg_config().model_prices,
            sleep=lambda _: None,
        )
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        result = _pg_run(migrated_engine, artifacts, "pg-r2", gateway)
        assert [job["status"] for job in result.jobs] == ["inserted", "inserted", "failed"]
        with migrated_engine.connect() as conn:
            rows = {
                row.stage: row
                for row in conn.execute(
                    select(screenplay_jobs).where(screenplay_jobs.c.round_id == "pg-r2")
                )
            }
        failed = rows["script"]
        assert failed.status == "failed"
        assert failed.error and "网关失败" in failed.error
        assert failed.actual_cost_usd == pytest.approx(failed.estimated_cost_usd)  # 成本照计
        assert result.cost_reconciliation["consistent"] is True
