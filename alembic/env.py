from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os

# ✅ 이 줄 추가: settings.py 또는 환경 변수에서 가져오기
from settings import DATABASE_URL

# Alembic Config
config = context.config

# ✅ 환경 변수 기반 DB URL을 수동으로 설정
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# 로깅 설정
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ✅ Base metadata 지정 (autogenerate용)
from models import Base  # <- 반드시 models.py에서 Base 가져오기
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
