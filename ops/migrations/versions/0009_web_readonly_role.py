"""迁移 0009：只读角色 cineflow_web（宪章 v1.1.0 原则五新条款的权限层落点）

只读三重机检之二 = **角色的物理保证**：即使前端代码写错，DB 也拒绝写。

- 角色 cineflow_web（LOGIN，最小特权 NOSUPERUSER/NOCREATEDB/NOCREATEROLE）；
  口令经环境变量 `CINEFLOW_WEB_PASSWORD` 注入：迁移文件内无任何明文口令，口令只在**运行时**
  由环境变量拼成 ALTER ROLE 语句（单引号加倍转义，见 `password_statement`）；环境变量缺省即
  不装配口令语句（由运维另行注入，不静默用默认值）。
  为什么不用绑定参数：PostgreSQL 的 ALTER ROLE 是工具语句，服务端不接受占位符
  （实测 psycopg 报 `syntax error at or near "$1"`）——故在客户端做安全转义。
- 全表仅 SELECT：GRANT SELECT ON ALL TABLES + 显式 REVOKE 六个写动词（不依赖默认拒绝）；
- 未来表默认只 SELECT：ALTER DEFAULT PRIVILEGES 双侧（授予 SELECT + 回收写动词）；
- schema USAGE 显式授予、CREATE 权限回收（一期 CI 教训，同 0001/0004：不能依赖 initdb
  给 PUBLIC 的默认授权）；
- 不触碰业务表、不动应用账号 cineflow_app 的既有授权。

**PG-only**：SQLite 无角色语义（无 CREATE ROLE / GRANT / 默认权限），本迁移与单测
（tests/unit/test_migration_0009.py）的断言均为 PG 专用的 SQL 文本与迁移纪律；
权限**行为**断言（只读角色写被拒、全表仅 SELECT）在真实 PG 集成测试 T1311。

修订 ID: 0009_web_readonly_role
父修订: 0008_screenplay_jobs
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_web_readonly_role"
down_revision: str | Sequence[str] | None = "0008_screenplay_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 口令环境变量名（web.dsn_env 指向的 DSN 里用同一口令；明文不入库文件）
PASSWORD_ENV = "CINEFLOW_WEB_PASSWORD"
ROLE = "cineflow_web"
SCHEMA = "public"

# 六个写动词：TRUNCATE 与 REFERENCES/TRIGGER 也一并回收（只读语义不留缝）
_WRITE_VERBS = "INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"

_CREATE_ROLE = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
        CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;
"""

# ALTER ROLE 是 PG 工具语句（不接受绑定参数），故口令在客户端按单引号加倍规则转义后成句
_PASSWORD_PREFIX = f"ALTER ROLE {ROLE} PASSWORD '"

_UPGRADE_STATEMENTS = (
    _CREATE_ROLE,
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE}",
    f"REVOKE CREATE ON SCHEMA {SCHEMA} FROM {ROLE}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {ROLE}",
    f"REVOKE {_WRITE_VERBS} ON ALL TABLES IN SCHEMA {SCHEMA} FROM {ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES TO {ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} REVOKE {_WRITE_VERBS} ON TABLES FROM {ROLE}",
)

_DOWNGRADE_STATEMENTS = (
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} REVOKE ALL ON TABLES FROM {ROLE}",
    f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {ROLE}",
    f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {ROLE}",
    f"DROP ROLE IF EXISTS {ROLE}",
)


def password_statement(password: str) -> str:
    """口令语句装配（PG 单引号加倍转义——注入安全由转义保证，口令不入迁移文件）。"""
    escaped = str(password).replace("'", "''")
    return f"{_PASSWORD_PREFIX}{escaped}'"


def _resolved_password(password: str | None) -> str | None:
    """口令解析：显式参数优先，否则取环境变量；空串 = 未配置。"""
    if password is not None:
        return password or None
    return os.environ.get(PASSWORD_ENV, "") or None


def upgrade_statements(password: str | None = None) -> list[tuple[str, dict]]:
    """升级语句装配（SQL 文本, 绑定参数）——纯函数，便于单测机检（不连库）。

    口令语句仅在解析到口令时装配（环境变量缺省 = 交给运维另行注入）。
    """
    statements = [(sql, {}) for sql in _UPGRADE_STATEMENTS]
    resolved = _resolved_password(password)
    if resolved:
        statements.insert(1, (password_statement(resolved), {}))
    return statements


def downgrade_statements() -> list[tuple[str, dict]]:
    """降级语句装配：先回收授权（含默认权限）再删角色（有依赖时删角色会失败）。"""
    return [(sql, {}) for sql in _DOWNGRADE_STATEMENTS]


def upgrade() -> None:
    for sql, params in upgrade_statements():
        if params:
            op.get_bind().execute(sa.text(sql), params)
        else:
            op.execute(sql)


def downgrade() -> None:
    for sql, _ in downgrade_statements():
        op.execute(sql)
