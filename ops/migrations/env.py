"""Alembic 环境配置。

连接串读取环境变量 CINEFLOW_PG_DSN（应使用迁移专用账号，与应用账号 cineflow_app 分离）；
target_metadata 指向 core.tree.db，供 autogenerate 与 schema 一致性断言使用。
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 迁移账号与应用账号分离：迁移用 DSN 由环境注入，默认指向本地开发库
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get(
        "CINEFLOW_PG_DSN", "postgresql+psycopg://cineflow:cineflow@localhost:5432/cineflow"
    ),
)

try:
    from core.tree.db import metadata as target_metadata
except ImportError:  # core.tree.db 尚未落地（迁移框架先于表定义初始化）
    target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
