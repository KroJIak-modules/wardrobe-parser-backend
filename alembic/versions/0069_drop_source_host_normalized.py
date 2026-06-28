"""drop redundant source host_normalized column

Revision ID: 0069_drop_source_host_normalized
Revises: 0068_add_source_adapter_and_parser_config
Create Date: 2026-06-24 11:30:00.000000
"""

from __future__ import annotations

from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import op


revision = "0069_drop_source_host_normalized"
down_revision = "0068_add_source_adapter_and_parser_config"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _normalize_host(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = str(parsed.hostname or parsed.netloc or parsed.path or "").strip().lower()
    except Exception:
        host = value.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "sources", "host_normalized"):
        if _has_index(bind, "sources", "ix_sources_host_normalized"):
            op.drop_index("ix_sources_host_normalized", table_name="sources")
        op.drop_column("sources", "host_normalized")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "sources", "host_normalized"):
        op.add_column("sources", sa.Column("host_normalized", sa.String(length=255), nullable=True))
        rows = bind.execute(sa.text("SELECT id, base_url FROM sources")).mappings().all()
        for row in rows:
            bind.execute(
                sa.text("UPDATE sources SET host_normalized = :host WHERE id = :id"),
                {"id": row["id"], "host": _normalize_host(row["base_url"])},
            )
        op.alter_column("sources", "host_normalized", existing_type=sa.String(length=255), nullable=False)
        if not _has_index(bind, "sources", "ix_sources_host_normalized"):
            op.create_index("ix_sources_host_normalized", "sources", ["host_normalized"], unique=False)
