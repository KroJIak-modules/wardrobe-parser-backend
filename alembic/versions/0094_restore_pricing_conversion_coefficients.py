"""restore pricing conversion coefficients

Revision ID: 0094_restore_pricing_conversion_coefficients
Revises: 0093_remove_truncated_seed_site_notification
Create Date: 2026-07-02
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0094_restore_pricing_conversion_coefficients"
down_revision = "0093_remove_truncated_seed_site_notification"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _resolve_admin_defaults_path() -> Path:
    explicit_path = str(os.getenv("ADMIN_DEFAULTS_CONFIG_PATH", "") or "").strip()
    if explicit_path:
        return Path(explicit_path)
    current_file = Path(__file__).resolve()
    container_path = current_file.parents[2] / "shared-config" / "sources.json"
    if container_path.exists():
        return container_path
    return current_file.parents[3] / "service" / "config" / "sources.json"


def _load_seed_pricing_settings() -> dict:
    payload = json.loads(_resolve_admin_defaults_path().read_text(encoding="utf-8"))
    defaults = payload.get("admin_defaults") if isinstance(payload, dict) else None
    pricing = defaults.get("pricing_settings") if isinstance(defaults, dict) else None
    if not isinstance(pricing, dict):
        raise ValueError("pricing_settings section is missing in admin defaults config")
    return pricing


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "pricing_settings"):
        return
    pricing_seed = _load_seed_pricing_settings()
    seed_eur_to_usd_rate = float(pricing_seed.get("eur_to_usd_rate") or 0.0)
    seed_gbp_to_usd_rate = float(pricing_seed.get("gbp_to_usd_rate") or 0.0)
    seed_jpy_to_usd_rate = float(pricing_seed.get("jpy_to_usd_rate") or 0.0)

    if not _column_exists(bind, "pricing_settings", "eur_to_usd_rate"):
        op.add_column("pricing_settings", sa.Column("eur_to_usd_rate", sa.Numeric(12, 6), nullable=True))
    if not _column_exists(bind, "pricing_settings", "gbp_to_usd_rate"):
        op.add_column("pricing_settings", sa.Column("gbp_to_usd_rate", sa.Numeric(12, 6), nullable=True))
    if not _column_exists(bind, "pricing_settings", "jpy_to_usd_rate"):
        op.add_column("pricing_settings", sa.Column("jpy_to_usd_rate", sa.Numeric(12, 8), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE pricing_settings
            SET eur_to_usd_rate = CASE
                WHEN COALESCE(usd_to_rub_rate, 0) > 0 AND COALESCE(eur_to_rub_rate, 0) > 0
                    THEN eur_to_rub_rate / usd_to_rub_rate
                ELSE :seed_eur_to_usd_rate
            END
            WHERE eur_to_usd_rate IS NULL OR eur_to_usd_rate <= 0
            """
        ),
        {"seed_eur_to_usd_rate": seed_eur_to_usd_rate},
    )
    bind.execute(
        sa.text(
            """
            UPDATE pricing_settings
            SET gbp_to_usd_rate = :seed_gbp_to_usd_rate
            WHERE gbp_to_usd_rate IS NULL OR gbp_to_usd_rate <= 0
            """
        ),
        {"seed_gbp_to_usd_rate": seed_gbp_to_usd_rate},
    )
    bind.execute(
        sa.text(
            """
            UPDATE pricing_settings
            SET jpy_to_usd_rate = :seed_jpy_to_usd_rate
            WHERE jpy_to_usd_rate IS NULL OR jpy_to_usd_rate <= 0
            """
        ),
        {"seed_jpy_to_usd_rate": seed_jpy_to_usd_rate},
    )

    op.alter_column("pricing_settings", "eur_to_usd_rate", existing_type=sa.Numeric(12, 6), nullable=False)
    op.alter_column("pricing_settings", "gbp_to_usd_rate", existing_type=sa.Numeric(12, 6), nullable=False)
    op.alter_column("pricing_settings", "jpy_to_usd_rate", existing_type=sa.Numeric(12, 8), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "pricing_settings") and _column_exists(bind, "pricing_settings", "jpy_to_usd_rate"):
        op.drop_column("pricing_settings", "jpy_to_usd_rate")
    if _table_exists(bind, "pricing_settings") and _column_exists(bind, "pricing_settings", "gbp_to_usd_rate"):
        op.drop_column("pricing_settings", "gbp_to_usd_rate")
    if _table_exists(bind, "pricing_settings") and _column_exists(bind, "pricing_settings", "eur_to_usd_rate"):
        op.drop_column("pricing_settings", "eur_to_usd_rate")
