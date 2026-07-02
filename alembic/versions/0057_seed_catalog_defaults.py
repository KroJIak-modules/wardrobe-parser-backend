"""seed catalog supplier defaults

Revision ID: 0057_seed_catalog_defaults
Revises: 0056_designer_logo_image_asset_id
Create Date: 2026-06-21 15:30:00.000000
"""

from __future__ import annotations

from alembic import op
import json
from pathlib import Path
import sqlalchemy as sa


revision = "0057_seed_catalog_defaults"
down_revision = "0056_designer_logo_image_asset_id"
branch_labels = None
depends_on = None


_MANUAL_SOURCE_KEY = "manual.local"


def _shared_config_path() -> Path:
    repo_path = Path(__file__).resolve().parents[3] / "service" / "config" / "sources.json"
    container_path = Path(__file__).resolve().parents[2] / "shared-config" / "sources.json"
    return container_path if container_path.exists() else repo_path


def _load_admin_defaults() -> dict[str, object]:
    payload = json.loads(_shared_config_path().read_text(encoding="utf-8"))
    defaults = payload.get("admin_defaults") if isinstance(payload, dict) else None
    if not isinstance(defaults, dict):
        raise RuntimeError("admin_defaults section is missing in shared sources config")
    return defaults


def _load_suppliers() -> tuple[list[dict[str, object]], str]:
    defaults = _load_admin_defaults()
    suppliers = defaults.get("suppliers")
    default_supplier_key = str(defaults.get("default_source_supplier_key") or "").strip()
    if not isinstance(suppliers, list) or not default_supplier_key:
        raise RuntimeError("shared admin defaults do not contain suppliers or default supplier key")
    normalized: list[dict[str, object]] = []
    for item in suppliers:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        provider_kind = str(item.get("provider_kind") or "").strip()
        rate_currency = str(item.get("rate_currency") or "").strip()
        if not key or not name or not provider_kind or not rate_currency:
            continue
        rates_raw = item.get("rates")
        rates: list[tuple[float, float | None, float]] = []
        if isinstance(rates_raw, list):
            for rate in rates_raw:
                if not isinstance(rate, dict):
                    continue
                min_kg = float(rate.get("min_kg") or 0.0)
                max_kg = float(rate["max_kg"]) if rate.get("max_kg") is not None else None
                rub = float(rate.get("rub") or 0.0)
                rates.append((min_kg, max_kg, rub))
        normalized.append(
            {
                "key": key,
                "name": name,
                "provider_kind": provider_kind,
                "parent_key": (str(item.get("parent_supplier_key") or "").strip() or None),
                "rate_currency": rate_currency,
                "rates": tuple(rates),
            }
        )
    return normalized, default_supplier_key


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
    suppliers_seed, default_supplier_key = _load_suppliers()

    supplier_ids: dict[str, int] = {}
    for item in suppliers_seed:
        supplier_id = _get_supplier_id(conn, str(item["key"]))
        if supplier_id is None:
            supplier_id = _insert_supplier(conn, item)
        supplier_ids[str(item["key"])] = supplier_id

    for item in suppliers_seed:
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

    default_supplier_id = supplier_ids[default_supplier_key]
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
