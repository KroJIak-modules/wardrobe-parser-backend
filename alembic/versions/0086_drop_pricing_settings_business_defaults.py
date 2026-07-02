"""Drop business defaults from pricing settings.

Revision ID: 0086_drop_pricing_settings_business_defaults
Revises: 0085_add_pricing_svc_rules
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import json
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0086_drop_pricing_settings_business_defaults"
down_revision = "0085_add_pricing_svc_rules"
branch_labels = None
depends_on = None


_PRICING_COLUMNS = (
    "markup_multiplier",
    "weight_tolerance",
    "customs_threshold_eur",
    "customs_duty_rate",
    "eur_to_rub_rate",
    "usd_to_rub_rate",
    "usdt_to_rub_rate",
    "usdt_extra_rub",
    "payment_fee_rate",
    "customs_processing_rate",
    "customs_fixed_rub",
    "tax_rate",
    "final_rounding_mode",
)


def _shared_config_path() -> Path:
    repo_path = Path(__file__).resolve().parents[3] / "service" / "config" / "sources.json"
    container_path = Path(__file__).resolve().parents[2] / "shared-config" / "sources.json"
    return container_path if container_path.exists() else repo_path


def _load_pricing_defaults() -> dict[str, object]:
    payload = json.loads(_shared_config_path().read_text(encoding="utf-8"))
    defaults = payload.get("admin_defaults") if isinstance(payload, dict) else None
    pricing = defaults.get("pricing_settings") if isinstance(defaults, dict) else None
    if not isinstance(pricing, dict):
        raise RuntimeError("pricing_settings section is missing in shared sources config")
    return pricing


def _server_default_literal(value: object) -> sa.TextClause:
    if isinstance(value, str):
        return sa.text("'" + value.replace("'", "''") + "'")
    return sa.text(str(value))


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "pricing_settings"):
        return
    for column_name in _PRICING_COLUMNS:
        if _column_exists(bind, "pricing_settings", column_name):
            op.alter_column("pricing_settings", column_name, server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "pricing_settings"):
        return
    pricing = _load_pricing_defaults()
    defaults = {
        "markup_multiplier": _server_default_literal(pricing["markup_multiplier"]),
        "weight_tolerance": _server_default_literal(pricing["weight_tolerance"]),
        "customs_threshold_eur": _server_default_literal(pricing["customs_threshold_eur"]),
        "customs_duty_rate": _server_default_literal(pricing["customs_duty_rate"]),
        "eur_to_rub_rate": _server_default_literal(pricing["eur_to_rub_rate"]),
        "usd_to_rub_rate": _server_default_literal(pricing["usd_to_rub_rate"]),
        "usdt_to_rub_rate": _server_default_literal(pricing["usdt_to_rub_rate"]),
        "usdt_extra_rub": _server_default_literal(pricing["usdt_extra_rub"]),
        "payment_fee_rate": _server_default_literal(pricing["payment_fee_rate"]),
        "customs_processing_rate": _server_default_literal(pricing["customs_processing_rate"]),
        "customs_fixed_rub": _server_default_literal(pricing["customs_fixed_rub"]),
        "tax_rate": _server_default_literal(pricing["tax_rate"]),
        "final_rounding_mode": _server_default_literal(pricing["final_rounding_mode"]),
    }
    for column_name, server_default in defaults.items():
        if _column_exists(bind, "pricing_settings", column_name):
            op.alter_column("pricing_settings", column_name, server_default=server_default)
