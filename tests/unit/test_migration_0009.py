"""迁移 0009_web_readonly_role 单测（功能 013 / T1303，先于实现编写）。

**PG-only**：SQLite 无角色语义（无 CREATE ROLE / GRANT / 默认权限），故本文件的单测做
**SQL 文本与迁移纪律**断言：
- 修订链（down_revision=0008）+ PG 专用语句（角色创建、全表 SELECT、写动词 REVOKE、
  默认权限只 SELECT、schema USAGE、无 DDL 权限）；
- 口令经环境变量注入：迁移文件内**不含**任何明文口令，ALTER ROLE ... PASSWORD 走
  绑定参数（口令不落盘、不进语句文本、不进迁移文件）。

权限的**行为**断言（cineflow_web 对全部表仅 SELECT、以该角色写被 DB 拒绝）在 T1311 的真实
PG 集成测试（tests/integration/test_web_pg.py）——单测不假装验证了 DB 权限行为。
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "ops" / "migrations" / "versions" / "0009_web_readonly_role.py"

ROLE = "cineflow_web"
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


@pytest.fixture(scope="module")
def migration():
    """加载迁移模块（不执行迁移）——供修订链、常量与语句装配断言。"""
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def upgrade_sql(migration) -> str:
    """装配后的升级语句文本（不含绑定参数值）。"""
    return "\n".join(sql for sql, _ in migration.upgrade_statements())


class Test修订链与PG纪律:
    def test_修订链(self, migration):
        assert migration.revision == "0009_web_readonly_role"
        assert migration.down_revision == "0008_screenplay_jobs"
        assert migration.branch_labels is None
        assert migration.depends_on is None

    def test_模块注明_PG_only(self, source):
        """显式声明 PG-only：SQLite 无角色语义，权限行为只在真实 PG 断言（T1311）。"""
        assert "PG-only" in source

    def test_不引入应用层依赖(self, source):
        """运维面迁移不为只读角色引入应用层模块依赖。"""
        for banned in ("from core", "import core", "from agents", "import agents", "dreaming."):
            assert banned not in source


class Test角色创建:
    def test_角色存在性守卫与最小特权(self, upgrade_sql):
        """幂等守卫（DO 块 + IF NOT EXISTS）+ 显式最小特权属性。"""
        assert f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}')" in upgrade_sql
        assert f"CREATE ROLE {ROLE} LOGIN" in upgrade_sql
        for attribute in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE"):
            assert attribute in upgrade_sql

    def test_口令经环境变量_不落盘(self, migration, source):
        """口令只经环境变量与绑定参数注入：文件与语句文本均无明文口令。"""
        assert migration.PASSWORD_ENV == "CINEFLOW_WEB_PASSWORD"
        assert "PASSWORD '" not in source  # 无字面量口令（不复刻 0001 的 app 口令形态）
        password_statements = [
            (sql, params)
            for sql, params in migration.upgrade_statements("s3cret-not-in-repo")
            if "PASSWORD" in sql
        ]
        assert len(password_statements) == 1
        sql, params = password_statements[0]
        assert f"ALTER ROLE {ROLE} PASSWORD" in sql
        assert ":password" in sql
        assert params == {"password": "s3cret-not-in-repo"}

    def test_未设环境变量时不装配口令语句(self, migration, monkeypatch):
        """口令缺省（环境变量未设）→ 不生成任何口令语句（由运维另行注入，不静默用默认值）。"""
        monkeypatch.delenv(migration.PASSWORD_ENV, raising=False)
        assert not any("PASSWORD" in sql for sql, _ in migration.upgrade_statements())
        monkeypatch.setenv(migration.PASSWORD_ENV, "s3cret-not-in-repo")
        password_statements = [
            (sql, params) for sql, params in migration.upgrade_statements() if "PASSWORD" in sql
        ]
        assert len(password_statements) == 1
        sql, params = password_statements[0]
        assert "s3cret-not-in-repo" not in sql  # 明文不入语句文本
        assert params == {"password": "s3cret-not-in-repo"}


class Test授权纪律:
    def test_全表只读(self, upgrade_sql):
        assert f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}" in upgrade_sql

    def test_写动词显式回收(self, upgrade_sql):
        """三重机检之二的角色层落点：写权限被显式回收（不依赖默认拒绝）。"""
        revokes = [line for line in upgrade_sql.splitlines() if line.startswith("REVOKE")]
        assert revokes, "缺少写权限回收语句"
        write_revoke = [
            line for line in revokes if f"ON ALL TABLES IN SCHEMA public FROM {ROLE}" in line
        ]
        assert len(write_revoke) == 1
        for verb in WRITE_VERBS:
            assert verb in write_revoke[0]

    def test_schema_usage_显式授予(self, upgrade_sql):
        """一期 CI 教训：不能依赖 initdb 给 PUBLIC 的默认授权（同 0001/0004）。"""
        assert f"GRANT USAGE ON SCHEMA public TO {ROLE}" in upgrade_sql

    def test_schema_建表权限回收(self, upgrade_sql):
        """只读角色不得建表（无 DDL 语义）。"""
        assert f"REVOKE CREATE ON SCHEMA public FROM {ROLE}" in upgrade_sql

    def test_默认权限只_SELECT(self, upgrade_sql):
        """未来表默认只 SELECT：授予 SELECT + 显式回收写动词（ALTER DEFAULT PRIVILEGES）。"""
        assert (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE}"
            in upgrade_sql
        )
        default_revokes = [
            line
            for line in upgrade_sql.splitlines()
            if line.startswith("ALTER DEFAULT PRIVILEGES") and "REVOKE" in line
        ]
        assert len(default_revokes) == 1
        for verb in WRITE_VERBS:
            assert verb in default_revokes[0]
        assert f"ON TABLES FROM {ROLE}" in default_revokes[0]

    def test_不触碰表与既有账号(self, upgrade_sql):
        """0009 只做角色与授权：不改表、不动应用账号 cineflow_app 的既有授权。"""
        assert "cineflow_app" not in upgrade_sql
        for statement in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "INDEX"):
            assert statement not in upgrade_sql


class Test语句装配:
    def test_升级语句为_sql_与绑定参二元组(self, migration):
        statements = migration.upgrade_statements()
        assert statements, "升级语句集合不得为空"
        for sql, params in statements:
            assert isinstance(sql, str) and sql.strip()
            assert isinstance(params, dict)
            for value in params.values():
                assert value not in sql  # 参数值不拼进 SQL 文本

    def test_降级语句先回收再删角色(self, migration):
        sql_text = "\n".join(sql for sql, _ in migration.downgrade_statements())
        assert f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}" in sql_text
        assert f"REVOKE ALL ON SCHEMA public FROM {ROLE}" in sql_text
        assert sql_text.index(
            f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}"
        ) < sql_text.index(f"DROP ROLE IF EXISTS {ROLE}")

    def test_upgrade_与_downgrade_可调用(self, migration):
        assert callable(migration.upgrade)
        assert callable(migration.downgrade)
