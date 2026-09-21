"""web 只读角色集成测试（功能 013 / T1311，真实 PostgreSQL）。

单测（tests/unit/test_migration_0009.py）只能断言 SQL 文本与迁移纪律；**权限的行为**必须
在真实 PG 上证明（SQLite 无角色语义）：

- 迁移 0009 执行后：`cineflow_web` 对 public 下**全部表**仅有 SELECT（information_schema 断言）；
- 以该角色 INSERT/UPDATE/DELETE 与 DDL（建表）→ DB 权限拒绝（角色层物理保证：即使前端
  代码写错，DB 也拒绝写）；
- **未来表默认只 SELECT**（ALTER DEFAULT PRIVILEGES 生效：迁移后新建的表同样可读不可写）；
- `web/queries.py` 以该角色的真实 DSN 读树（树清单/节点详情/谱系返回真实数据）。

PG 不可达则整体跳过（本地无 Docker 的开发机）；DSN 惯例见 tests/integration/conftest.py。
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from web import queries

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DSN = os.environ.get(
    "CINEFLOW_PG_TEST_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
)
WEB_PASSWORD = "cineflow-web-test-pw"
WEB_ROLE = "cineflow_web"
# 注意：SQLAlchemy 的 str(URL) 会把口令渲染成 `***`，这里必须显式保留口令
WEB_DSN = (
    make_url(PG_DSN)
    .set(username=WEB_ROLE, password=WEB_PASSWORD)
    .render_as_string(hide_password=False)
)
WEB_DSN_ENV = "CINEFLOW_WEB_PG_TEST_DSN"
PROBE_TABLE = "web_default_privileges_probe"


@pytest.fixture(scope="module")
def pg_web_engine():
    """独立生命周期：schema 重置 → alembic upgrade head（含 0009 只读角色）→ downgrade base。"""
    engine = create_engine(PG_DSN)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 无 PG 环境一律跳过
        pytest.skip(f"PostgreSQL 不可用，跳过集成测试：{exc}")

    os.environ["CINEFLOW_PG_DSN"] = PG_DSN
    os.environ["CINEFLOW_WEB_PASSWORD"] = WEB_PASSWORD
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "ops" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", PG_DSN)
    with engine.begin() as conn:
        # 整 schema 重置（重建后补 PUBLIC USAGE，同一期 CI 教训）
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


@pytest.fixture(scope="module")
def seeded_pg_engine(pg_web_engine):
    """夹具树落盘（应用账号写）→ 供只读角色读取。"""
    from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
    from core.tree.store import create_tree_store

    store = create_tree_store(pg_web_engine)
    tree = DiscoveryTree(
        tree_id="tree-pg-web-1",
        project_id="proj-pg",
        agent_id="visual",
        policy_version="abc123def456",
        root_id="tree-pg-web-1-n0",
        node_ids=["tree-pg-web-1-n0", "tree-pg-web-1-n1"],
        config_snapshot={"evaluator_weights": {"proxy.aesthetic": 1.0}, "form": "visual"},
    )
    store.create_tree(tree)
    for index, score in enumerate((0.4, 0.8)):
        store.append_node(
            TreeNode(
                node_id=f"tree-pg-web-1-n{index}",
                tree_id=tree.tree_id,
                parent_id=None if index == 0 else tree.root_id,
                depth=index,
                agent_id="visual",
                policy_version=tree.policy_version,
                prompt="PG 夹具提示词" if index == 0 else "",
                observation_context={"gen_params": {"temperature": 0.4}},
                artifact_hash=f"{index + 1:064x}",
                eval_breakdown={"proxy.aesthetic@1.0.0": {"score": score, "diagnostics": {}}},
                score=score,
                cost=CostRecord(
                    llm_calls=1, generation_api_calls=1, generation_api_cost_usd=0.25 * (index + 1)
                ),
                status=NodeStatus.EVALUATED,
                created_at=2000.0 + index,
            )
        )
    return pg_web_engine


@pytest.fixture()
def web_role_config(web_config, monkeypatch, tmp_path):
    """只读角色 DSN 的真实 PG 配置（文件化目录仍走临时夹具）。"""
    from dataclasses import replace

    monkeypatch.setenv(WEB_DSN_ENV, WEB_DSN)
    return replace(web_config, dsn_env=WEB_DSN_ENV, export_dir=str(tmp_path / "dist"))


def _role_engine():
    return create_engine(WEB_DSN)


class Test只读角色权限:
    def test_角色为最小特权登录角色(self, pg_web_engine):
        with pg_web_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles"
                    " WHERE rolname = :role"
                ),
                {"role": WEB_ROLE},
            ).one()
        assert tuple(row) == (False, False, False, True)

    def test_全部表仅有_select(self, pg_web_engine):
        with pg_web_engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public'"
                    )
                )
            }
            grants = {}
            for table_name, privilege in conn.execute(
                text(
                    "SELECT table_name, privilege_type FROM information_schema.table_privileges"
                    " WHERE grantee = :role AND table_schema = 'public'"
                ),
                {"role": WEB_ROLE},
            ):
                grants.setdefault(table_name, set()).add(privilege)
        assert tables, "public 下应有迁移建出的表"
        assert set(grants) == tables, "只读角色必须被授权到全部表（含 alembic_version）"
        for table_name, privileges in grants.items():
            assert privileges == {"SELECT"}, f"{table_name} 的权限不止 SELECT：{privileges}"

    @pytest.mark.parametrize(
        "statement",
        [
            "INSERT INTO discovery_trees (tree_id, project_id, agent_id, policy_version,"
            " root_id, node_ids, config_snapshot) VALUES ('x', 'p', 'a', 'v', 'r', '[]'::jsonb,"
            " '{}'::jsonb)",
            "UPDATE discovery_trees SET project_id = 'other'",
            "DELETE FROM discovery_trees",
            "INSERT INTO tree_nodes (node_id, tree_id, parent_id, depth, agent_id, policy_version,"
            " prompt, observation_context, artifact_hash, eval_breakdown, score, status, cost,"
            " created_at) VALUES ('n', 'x', NULL, 0, 'a', 'v', '', '{}'::jsonb, :hash,"
            " '{}'::jsonb, 0.5, 'evaluated', '{}'::jsonb, 0.0)",
        ],
        ids=["insert", "update", "delete", "insert_node"],
    )
    def test_写操作被_DB_拒绝(self, pg_web_engine, seeded_pg_engine, statement):
        role_engine = _role_engine()
        try:
            with pytest.raises(ProgrammingError, match="permission denied"):
                with role_engine.begin() as conn:
                    conn.execute(text(statement), {"hash": "ab" * 32})
        finally:
            role_engine.dispose()

    def test_建表被拒(self, pg_web_engine):
        role_engine = _role_engine()
        try:
            with pytest.raises(ProgrammingError, match="permission denied"):
                with role_engine.begin() as conn:
                    conn.execute(text(f"CREATE TABLE {PROBE_TABLE} (id int)"))
        finally:
            role_engine.dispose()

    def test_未来表默认只_select(self, pg_web_engine):
        """ALTER DEFAULT PRIVILEGES 生效：迁移后新建的表对只读角色同样可读不可写。"""
        with pg_web_engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
            conn.execute(text(f"CREATE TABLE {PROBE_TABLE} (id integer PRIMARY KEY, note text)"))
            conn.execute(text(f"INSERT INTO {PROBE_TABLE} (id, note) VALUES (1, 'probe')"))
        role_engine = _role_engine()
        try:
            with role_engine.connect() as conn:
                assert conn.execute(text(f"SELECT count(*) FROM {PROBE_TABLE}")).scalar() == 1
            with pytest.raises(ProgrammingError, match="permission denied"):
                with role_engine.begin() as conn:
                    conn.execute(text(f"INSERT INTO {PROBE_TABLE} (id) VALUES (2)"))
        finally:
            role_engine.dispose()


class Test只读角色真实读树:
    def test_健康检查_db_up(self, web_role_config, seeded_pg_engine, web_data_dir):
        assert queries.get_health(web_role_config)["db"] == "up"

    def test_树清单真实返回(self, web_role_config, seeded_pg_engine, web_data_dir):
        page = queries.list_trees(web_role_config)
        assert page["total"] == 1
        tree = page["items"][0]
        assert tree["tree_id"] == "tree-pg-web-1"
        assert tree["project_id"] == "proj-pg"
        assert tree["node_count"] == 2
        assert tree["form"] == "visual"
        assert tree["created_at"] == 2000.0

    def test_节点列表与详情真实返回(self, web_role_config, seeded_pg_engine, web_data_dir):
        page = queries.list_nodes(web_role_config, "tree-pg-web-1")
        assert [item["score"] for item in page["items"]] == [0.4, 0.8]
        assert page["items"][1]["cost_usd"] == 0.5
        detail = queries.get_node(web_role_config, "tree-pg-web-1-n1")
        assert detail["observation_keys"] == ["gen_params"]
        assert detail["eval_breakdown"] == [
            {"evaluator_key": "proxy.aesthetic@1.0.0", "score": 0.8, "diagnostics_keys": []}
        ]
        assert detail["artifact"]["hash"] == f"{2:064x}"

    def test_谱系真实返回(self, web_role_config, seeded_pg_engine, web_data_dir):
        payload = queries.get_lineage(web_role_config, "abc123def456")
        assert payload["trees"] == [
            {"tree_id": "tree-pg-web-1", "project_id": "proj-pg", "agent_id": "visual"}
        ]
        assert queries.get_lineage(web_role_config, "no-such-version") is None

    def test_只读查询后行数不变(self, web_role_config, seeded_pg_engine, web_data_dir):
        with seeded_pg_engine.connect() as conn:
            before = conn.execute(text("SELECT count(*) FROM tree_nodes")).scalar()
        queries.list_nodes(web_role_config, "tree-pg-web-1")
        queries.get_node(web_role_config, "tree-pg-web-1-n0")
        with seeded_pg_engine.connect() as conn:
            after = conn.execute(text("SELECT count(*) FROM tree_nodes")).scalar()
        assert (before, after) == (2, 2)

    def test_只读角色读不到未授权对象(self, web_role_config, seeded_pg_engine, web_data_dir):
        """未授权即不可见：只读角色不能读 pg_authid 等系统表（不是"数据库里的东西都能看"）。"""
        role_engine = _role_engine()
        try:
            with pytest.raises(ProgrammingError, match="permission denied"):
                with role_engine.connect() as conn:
                    conn.execute(text("SELECT rolpassword FROM pg_authid LIMIT 1"))
        finally:
            role_engine.dispose()
