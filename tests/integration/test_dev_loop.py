"""dev 轮次循环 PG 集成测试（功能 017 / T1719，真实 PostgreSQL）。

- `alembic upgrade head` 真实执行 0010：`dev_jobs` 建表（字段/`status` 枚举 CHECK/唯一键
  `(round_id, params_hash)`（单一产出、**无 stage 列**）/`actual ≤ estimated` CHECK/64 位
  工件哈希 CHECK/应用账号完整读写）；
- 唯一键冲突拒绝（幂等键的 DB 层底座）；
- 两段式落盘全链路：运营表可变中间态（generated → inserted，`inputs_json` 为规范化 JSON、
  缓存键/响应哈希落盘）→ 节点一次性 INSERT 冻结；同 round_id 二次触发幂等重建
  （0 重复行、0 重复生成、0 重复扣费）；
- 成本三方对账（运营表扣减 + 评估器计费增量 == 树内成本合计 == 本轮 spent）；
- 网关失败 → FAILED 节点 + 成本照计（原则二）。

PG 不可达则整体跳过；DSN 惯例见 tests/integration/conftest.py。
"""

import json
import os
from pathlib import Path

import blake3
import pytest
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from agents.dev.config import DevConfig
from agents.dev.db import dev_jobs
from agents.dev.loop import run_dev_round
from core.evaluators.quantize import quantize_score
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway, PermanentBackendError
from core.tree.artifacts import LocalArtifactStore
from core.tree.models import NodeStatus
from core.tree.store import create_tree_store

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)
INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}

_JOB = {
    "job_id": "pg-dev-j1",
    "round_id": "pg-dev-r0",
    "policy_version": "a1b2c3d4e5f6",
    "inputs_json": json.dumps(INPUTS, sort_keys=True, ensure_ascii=False),
    "params_hash": "aa" * 32,
    "cache_key": None,
    "response_hash": None,
    "status": "pending",
    "estimated_cost_usd": 0.5,
    "actual_cost_usd": None,
    "artifact_hash": None,
    "error": None,
    "created_at": "2026-09-23T00:00:00Z",
}


@pytest.fixture(scope="module")
def migrated_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0010）→ downgrade base。"""
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
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


class Test迁移0010真实执行:
    def test_建表字段与约束(self, migrated_engine):
        with migrated_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'dev_jobs'"
                    )
                )
            }
        # 单一产出：**无 stage 列**（009 为 (round_id, stage, params_hash)，本环节删去该列）
        assert columns == {
            "job_id",
            "round_id",
            "policy_version",
            "inputs_json",
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
        """唯一键 (round_id, params_hash)：同轮次同参数重复提交直接命中约束（幂等底座）。"""
        with migrated_engine.begin() as conn:
            conn.execute(insert(dev_jobs).values(**_JOB))
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(insert(dev_jobs).values(**{**_JOB, "job_id": "pg-dev-j2"}))

    def test_非法状态被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(dev_jobs).values(
                        **{
                            **_JOB,
                            "job_id": "pg-dev-j3",
                            "params_hash": "bb" * 32,
                            "status": "draft",
                        }
                    )
                )

    def test_实际成本超预估被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(dev_jobs).values(
                        **{
                            **_JOB,
                            "job_id": "pg-dev-j4",
                            "params_hash": "cc" * 32,
                            "actual_cost_usd": 0.6,
                        }
                    )
                )

    def test_工件哈希长度非法被_CHECK_拒绝(self, migrated_engine):
        with pytest.raises(IntegrityError):
            with migrated_engine.begin() as conn:
                conn.execute(
                    insert(dev_jobs).values(
                        **{
                            **_JOB,
                            "job_id": "pg-dev-j5",
                            "params_hash": "dd" * 32,
                            "artifact_hash": "short",
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
                        "WHERE table_name = 'dev_jobs' AND grantee = 'cineflow_app'"
                    )
                )
            }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= privileges


class _FailingBackend:
    """网关后端桩：按提示词命中即抛 4xx（不重试）——FAILED 路径的成本照计验证。"""

    def __init__(self, inner) -> None:
        self.call_count = 0
        self._inner = inner

    def complete(self, prompt, *, model, temperature, max_tokens):
        self.call_count += 1
        if "dir-return" in prompt:
            raise PermanentBackendError("后端 4xx（PG 集成注入）")
        return self._inner.complete(
            prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )


def _policy_of(source_text: str, version: str):
    namespace: dict = {"__name__": "dev_pg_policy"}
    exec(compile(source_text, "<dev-pg-policy>", "exec"), namespace)  # noqa: S102 - 夹具源码
    policy = namespace["Policy"]()
    policy.policy_version = version
    return policy


def _pg_config() -> DevConfig:
    return DevConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")


def _pg_run(engine, artifacts, round_id, gateway, *evaluators, policy_source, version="pg-dev-v1"):
    return run_dev_round(
        round_id=round_id,
        policy=_policy_of(policy_source, version),
        store=create_tree_store(engine),
        artifacts=artifacts,
        engine=engine,
        gateway=gateway,
        config=_pg_config(),
        inputs=INPUTS,
        evaluators=list(evaluators),
    )


class Test两段式落盘全链路:
    def test_中间态到一次性插入与幂等重建(
        self, migrated_engine, tmp_path, dev_policy_source, dev_config, dev_stub_evaluators
    ):
        config = _pg_config()
        gateway = LLMGateway(MockBackend(), price_book=config.model_prices, sleep=lambda _: None)
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        source = dev_policy_source("compliant")
        result = _pg_run(
            migrated_engine,
            artifacts,
            "pg-dev-r1",
            gateway,
            *dev_stub_evaluators,
            policy_source=source,
        )
        entry_count = config.slate_entries[1]
        assert result.job["status"] == "inserted"
        assert result.policy_version == "pg-dev-v1"
        assert gateway.call_count == entry_count  # 逐条目一次生成（缓存键落盘可复核）

        with migrated_engine.connect() as conn:
            rows = conn.execute(select(dev_jobs).where(dev_jobs.c.round_id == "pg-dev-r1")).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "inserted"
        assert row.policy_version == "pg-dev-v1"
        assert row.artifact_hash == result.job["artifact_hash"]
        assert row.artifact_hash is not None and len(row.artifact_hash) == 64
        assert row.cache_key is not None and row.response_hash is not None
        assert row.actual_cost_usd <= row.estimated_cost_usd
        # inputs_json 即规范化立项约束 + 形态参数 JSON，params_hash 与其逐字节一致（可复核）
        params = json.loads(row.inputs_json)
        assert row.inputs_json == json.dumps(params, sort_keys=True, ensure_ascii=False)
        assert params["inputs"] == INPUTS
        assert params["match_key"]["policy_version"] == "pg-dev-v1"
        assert row.params_hash == blake3.blake3(row.inputs_json.encode()).hexdigest()

        store = create_tree_store(migrated_engine)
        nodes = store.nodes_of(result.tree_id)
        children = [node for node in nodes if node.parent_id is not None]
        assert len(children) == 1  # 单一产出：锚点 + 一个立项组合节点
        node = children[0]
        assert node.status is NodeStatus.EVALUATED
        assert node.policy_version == "pg-dev-v1"
        assert node.artifact_hash == row.artifact_hash
        assert node.score == quantize_score(0.6)  # 桩代理 0.6 → 适用权重归一点定为 0.6
        # 分量键带版本（evaluator_id@version，原则一），与形态权重裸键集对齐
        assert {key.rsplit("@", 1)[0] for key in node.eval_breakdown} == set(
            dev_config.evaluator_weights
        )
        # 回放匹配槽：只含策略可复现的结构键（无生成产物摘要）
        assert node.observation_context["gen_params"] == {
            "policy_version": "pg-dev-v1",
            "model": config.model,
            "temperature": 0.0,
            "max_tokens": config.max_tokens,
            "constraint_digest": node.observation_context["gen_params"]["constraint_digest"],
            "slate_range": [config.slate_entries[0], config.slate_entries[1]],
        }
        assert len(node.observation_context["gen_params"]["constraint_digest"]) == 64
        assert node.observation_context["cache_key"]
        assert len(node.observation_context["calls"]) == entry_count

        # 轮次锚点：输入摘要与组合计划结论存档（供审计与回放重算取数）
        root = next(item for item in nodes if item.parent_id is None)
        assert root.observation_context["inputs"] == INPUTS
        assert root.observation_context["round_id"] == "pg-dev-r1"

        # 幂等重建：同 round_id 二次触发 0 重复行、0 重复生成
        second = _pg_run(
            migrated_engine,
            artifacts,
            "pg-dev-r1",
            gateway,
            *dev_stub_evaluators,
            policy_source=source,
        )
        assert second.job == result.job
        assert gateway.call_count == entry_count  # 0 重复生成
        assert len(store.nodes_of(result.tree_id)) == 2  # 0 重复节点
        with migrated_engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(dev_jobs).where(dev_jobs.c.round_id == "pg-dev-r1")
            ).scalar()
        assert total == 1  # 0 重复行
        assert second.cost_reconciliation == {}  # 重建路径对账字段留空（不伪造"一致"）

        # 成本对账：运营表扣减 + 评估器计费增量 == 树内成本 == 本轮 spent
        assert result.cost_reconciliation["consistent"] is True
        tree_total = sum(
            item.cost.generation_api_cost_usd for item in store.nodes_of(result.tree_id)
        )
        assert tree_total == pytest.approx(
            result.cost_reconciliation["ledger_total_usd"]
            + result.cost_reconciliation["evaluator_cost_usd"]
        )
        assert result.spent_usd == pytest.approx(result.cost_reconciliation["ledger_total_usd"])

    def test_网关失败成本照计并终态_failed(
        self, migrated_engine, tmp_path, dev_policy_source, dev_stub_evaluators
    ):
        """网关失败：运营表 failed 行（actual == estimated 照计）+ 节点 FAILED。"""
        config = _pg_config()
        gateway = LLMGateway(
            _FailingBackend(MockBackend()), price_book=config.model_prices, sleep=lambda _: None
        )
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        result = _pg_run(
            migrated_engine,
            artifacts,
            "pg-dev-r2",
            gateway,
            *dev_stub_evaluators,
            policy_source=dev_policy_source("compliant"),
        )
        assert result.job["status"] == "failed"
        assert "网关失败" in result.job["reason"]
        with migrated_engine.connect() as conn:
            row = conn.execute(select(dev_jobs).where(dev_jobs.c.round_id == "pg-dev-r2")).one()
        assert row.status == "failed"
        assert row.error and "网关失败" in row.error
        assert row.actual_cost_usd == pytest.approx(row.estimated_cost_usd)  # 成本照计
        node = next(
            item
            for item in create_tree_store(migrated_engine).nodes_of(result.tree_id)
            if item.parent_id is not None
        )
        assert node.status is NodeStatus.FAILED
        assert node.cost.generation_api_cost_usd == pytest.approx(row.estimated_cost_usd)
        assert result.cost_reconciliation["consistent"] is True
