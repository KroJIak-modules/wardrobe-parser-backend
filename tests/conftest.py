from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


def _quoted_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _maintenance_url(database_url: str) -> URL:
    url = make_url(database_url)
    maintenance_db = "postgres" if (url.database or "").strip() != "postgres" else "template1"
    return url.set(database=maintenance_db)


def _drop_database(admin_engine, database_name: str) -> None:
    ident = _quoted_ident(database_name)
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database_name
                  AND pid <> pg_backend_pid()
                """
            ),
            {"database_name": database_name},
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {ident}"))


def _create_database(admin_engine, database_name: str) -> None:
    ident = _quoted_ident(database_name)
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE DATABASE {ident}"))


def _run_migrations(database_url: str) -> None:
    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _bootstrap_seed_data() -> None:
    from app.core.database import SessionLocal
    from app.services.auth.admin_accounts_service import AdminAccountsService
    from app.services.catalog.catalog_defaults_service import CatalogDefaultsService
    from app.services.catalog.source_registry_service import SourceRegistryService
    from app.services.settings.pricing_service import PricingSettingsService

    db = SessionLocal()
    try:
        PricingSettingsService(db).reset_to_seed()
        AdminAccountsService(db).ensure_superadmin_user()
        registry = SourceRegistryService(db)
        registry.ensure_manual_source()
        CatalogDefaultsService(db).ensure()
        if not any(
            str(source.key or "").strip().lower() != SourceRegistryService.MANUAL_SOURCE_KEY
            for source in registry.list_all()
        ):
            registry.seed_from_payload(
                [
                    {
                        "key": "seed-source.example",
                        "name": "Seed Source",
                        "url": "https://seed-source.example/",
                        "adapter_key": "seed-test-adapter",
                        "config": {"mode": "auto"},
                        "enabled": True,
                        "sync_enabled": True,
                    }
                ]
            )
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database() -> None:
    from app.core import database as app_database
    from app.core.config import settings

    original_database_url = str(settings.database_url)
    original_engine = app_database.engine

    test_database_name = f"wardrobe_test_{uuid4().hex}"
    test_database_url = make_url(original_database_url).set(database=test_database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(_maintenance_url(original_database_url))

    os.environ["DATABASE_URL"] = test_database_url
    object.__setattr__(settings, "database_url", test_database_url)

    _drop_database(admin_engine, test_database_name)
    _create_database(admin_engine, test_database_name)
    _run_migrations(test_database_url)

    test_engine = create_engine(test_database_url)
    app_database.SessionLocal.configure(bind=test_engine)
    app_database.engine = test_engine

    _bootstrap_seed_data()

    try:
        yield
    finally:
        app_database.SessionLocal.remove() if hasattr(app_database.SessionLocal, "remove") else None
        app_database.SessionLocal.configure(bind=original_engine)
        app_database.engine = original_engine
        object.__setattr__(settings, "database_url", original_database_url)
        os.environ["DATABASE_URL"] = original_database_url

        test_engine.dispose()
        original_engine.dispose()
        _drop_database(admin_engine, test_database_name)
        admin_engine.dispose()
