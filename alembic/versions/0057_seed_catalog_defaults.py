"""seed catalog supplier defaults

Revision ID: 0057_seed_catalog_defaults
Revises: 0056_designer_logo_image_asset_id
Create Date: 2026-06-21 15:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0057_seed_catalog_defaults"
down_revision = "0056_designer_logo_image_asset_id"
branch_labels = None
depends_on = None


_MANUAL_SOURCE_KEY = "manual.local"
_DEFAULT_SUPPLIER_KEY = "eu"
_SUPPLIERS = (
    {
        "key": "usa",
        "name": "США",
        "provider_kind": "main",
        "parent_key": None,
        "rate_currency": "RUB",
        "rates": ((0.0, 0.5, 1400.0), (0.5, 1.0, 1650.0), (1.0, 1.5, 2250.0), (1.5, 2.0, 2900.0), (2.0, 2.5, 3500.0), (2.5, None, 4100.0)),
    },
    {
        "key": "usa-alt-1",
        "name": "ALT 1 США",
        "provider_kind": "alternate",
        "parent_key": "usa",
        "rate_currency": "RUB",
        "rates": ((0.0, 0.5, 1700.0), (0.5, 1.0, 3350.0), (1.0, 1.5, 4100.0), (1.5, 2.0, 4950.0), (2.0, 2.5, 5650.0), (2.5, None, 6500.0)),
    },
    {
        "key": "eu",
        "name": "ЕС",
        "provider_kind": "main",
        "parent_key": None,
        "rate_currency": "RUB",
        "rates": ((0.0, 0.5, 1100.0), (0.5, 1.0, 1500.0), (1.0, 1.5, 1900.0), (1.5, 2.0, 2300.0), (2.0, 2.5, 2700.0), (2.5, None, 3150.0)),
    },
    {
        "key": "eu-alt-1",
        "name": "ALT 1 ЕС",
        "provider_kind": "alternate",
        "parent_key": "eu",
        "rate_currency": "RUB",
        "rates": ((0.0, 0.5, 2300.0), (0.5, 1.0, 2750.0), (1.0, 1.5, 3750.0), (1.5, 2.0, 4800.0), (2.0, 2.5, 5800.0), (2.5, None, 6800.0)),
    },
    {
        "key": "uk",
        "name": "Великобритания",
        "provider_kind": "main",
        "parent_key": None,
        "rate_currency": "RUB",
        "rates": ((0.0, 0.5, 3400.0), (0.5, 1.0, 3900.0), (1.0, 1.5, 4400.0), (1.5, 2.0, 4900.0), (2.0, 2.5, 5450.0), (2.5, None, 5950.0)),
    },
)


def _get_supplier_id(conn, key: str) -> int | None:
    row = conn.execute(sa.text("SELECT id FROM suppliers WHERE key = :key"), {"key": key}).first()
    return int(row[0]) if row is not None else None


def _insert_supplier(conn, item: dict[str, object]) -> int:
    return int(
        conn.execute(
            sa.text(
                """
                INSERT INTO suppliers (key, name, provider_kind, parent_supplier_id, rate_currency, is_enabled)
                VALUES (:key, :name, :provider_kind, NULL, :rate_currency, TRUE)
                RETURNING id
                """
            ),
            {
                "key": item["key"],
                "name": item["name"],
                "provider_kind": item["provider_kind"],
                "rate_currency": item["rate_currency"],
            },
        ).scalar_one()
    )


def upgrade() -> None:
    conn = op.get_bind()

    supplier_ids: dict[str, int] = {}
    for item in _SUPPLIERS:
        supplier_id = _get_supplier_id(conn, str(item["key"]))
        if supplier_id is None:
            supplier_id = _insert_supplier(conn, item)
        supplier_ids[str(item["key"])] = supplier_id

    for item in _SUPPLIERS:
        supplier_id = supplier_ids[str(item["key"])]
        parent_key = item["parent_key"]
        parent_supplier_id = supplier_ids[str(parent_key)] if parent_key else None
        conn.execute(
            sa.text(
                """
                UPDATE suppliers
                SET parent_supplier_id = CASE
                        WHEN :parent_supplier_id IS NULL THEN NULL
                        WHEN parent_supplier_id IS NULL THEN :parent_supplier_id
                        ELSE parent_supplier_id
                    END,
                    provider_kind = CASE
                        WHEN provider_kind IS NULL OR btrim(provider_kind) = '' THEN :provider_kind
                        ELSE provider_kind
                    END,
                    rate_currency = CASE
                        WHEN rate_currency IS NULL OR btrim(rate_currency) = '' THEN :rate_currency
                        ELSE rate_currency
                    END,
                    is_enabled = COALESCE(is_enabled, TRUE)
                WHERE id = :supplier_id
                """
            ),
            {
                "supplier_id": supplier_id,
                "parent_supplier_id": parent_supplier_id,
                "provider_kind": item["provider_kind"],
                "rate_currency": item["rate_currency"],
            },
        )
        has_rates = conn.execute(
            sa.text("SELECT EXISTS(SELECT 1 FROM supplier_shipping_rates WHERE supplier_id = :supplier_id)"),
            {"supplier_id": supplier_id},
        ).scalar()
        if has_rates:
            continue
        for min_kg, max_kg, rub in item["rates"]:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO supplier_shipping_rates (supplier_id, min_weight_kg, max_weight_kg, price_rub)
                    VALUES (:supplier_id, :min_weight_kg, :max_weight_kg, :price_rub)
                    """
                ),
                {
                    "supplier_id": supplier_id,
                    "min_weight_kg": float(min_kg),
                    "max_weight_kg": (float(max_kg) if max_kg is not None else None),
                    "price_rub": float(rub),
                },
            )

    default_supplier_id = supplier_ids[_DEFAULT_SUPPLIER_KEY]
    conn.execute(
        sa.text(
            """
            UPDATE source_settings
            SET supplier_id = :default_supplier_id
            WHERE supplier_id IS NULL
              AND source_id IN (
                  SELECT id
                  FROM sources
                  WHERE key <> :manual_source_key
              )
            """
        ),
        {
            "default_supplier_id": default_supplier_id,
            "manual_source_key": _MANUAL_SOURCE_KEY,
        },
    )


def downgrade() -> None:
    # Intentional no-op: this is idempotent reference data backfill.
    pass
