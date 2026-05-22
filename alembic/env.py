from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text
from alembic import context
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import Base
from app.models import *
from app.core.config import settings

config = context.config
_db_url = settings.database_url or (
    f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
)
config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        version_table="alembic_version_backend",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Older environments may have alembic_version_backend.version_num as varchar(32),
        # while our revision ids are longer; widen once before migration steps.
        try:
            connection.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_name = 'alembic_version_backend'
                              AND column_name = 'version_num'
                              AND character_maximum_length IS NOT NULL
                              AND character_maximum_length < 64
                        ) THEN
                            ALTER TABLE alembic_version_backend
                            ALTER COLUMN version_num TYPE varchar(64);
                        END IF;
                    END $$;
                    """
                )
            )
            connection.commit()
        except Exception:
            # Table may not exist on first migration run yet; ignore safely.
            connection.rollback()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_backend",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
