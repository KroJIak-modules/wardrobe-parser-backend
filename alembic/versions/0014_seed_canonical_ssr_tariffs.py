"""seed canonical SSR tariffs in database

Revision ID: 0014_seed_canonical_ssr
Revises: 0013_supplier_tariff_ranges
Create Date: 2026-04-16 17:05:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_seed_canonical_ssr"
down_revision = "0013_supplier_tariff_ranges"
branch_labels = None
depends_on = None


_TARIFFS = [
    {
        "key": "usa",
        "name": "США",
        "category": "main",
        "parent_key": None,
        "alt_position": 0,
        "rate_currency": "RUB",
        "legacy_keys": ["default", "us-main", "us-express-test"],
        "rates": [
            (0.0, 0.5, 1400.0),
            (0.5, 1.0, 1650.0),
            (1.0, 1.5, 2250.0),
            (1.5, 2.0, 2900.0),
            (2.0, 2.5, 3500.0),
            (2.5, None, 4100.0),
        ],
    },
    {
        "key": "usa-alt-1",
        "name": "ALT 1 США",
        "category": "alt",
        "parent_key": "usa",
        "alt_position": 1,
        "rate_currency": "RUB",
        "legacy_keys": ["us-alt"],
        "rates": [
            (0.0, 0.5, 1700.0),
            (0.5, 1.0, 3350.0),
            (1.0, 1.5, 4100.0),
            (1.5, 2.0, 4950.0),
            (2.0, 2.5, 5650.0),
            (2.5, None, 6500.0),
        ],
    },
    {
        "key": "eu",
        "name": "ЕС",
        "category": "main",
        "parent_key": None,
        "alt_position": 0,
        "rate_currency": "RUB",
        "legacy_keys": ["eu-main", "eu-priority-test"],
        "rates": [
            (0.0, 0.5, 1100.0),
            (0.5, 1.0, 1500.0),
            (1.0, 1.5, 1900.0),
            (1.5, 2.0, 2300.0),
            (2.0, 2.5, 2700.0),
            (2.5, None, 3150.0),
        ],
    },
    {
        "key": "eu-alt-1",
        "name": "ALT 1 ЕС",
        "category": "alt",
        "parent_key": "eu",
        "alt_position": 1,
        "rate_currency": "RUB",
        "legacy_keys": ["eu-alt", "eu-economy-test"],
        "rates": [
            (0.0, 0.5, 2300.0),
            (0.5, 1.0, 2750.0),
            (1.0, 1.5, 3750.0),
            (1.5, 2.0, 4800.0),
            (2.0, 2.5, 5800.0),
            (2.5, None, 6800.0),
        ],
    },
    {
        "key": "uk",
        "name": "Великобритания",
        "category": "main",
        "parent_key": None,
        "alt_position": 0,
        "rate_currency": "RUB",
        "legacy_keys": ["uk-main", "gb-main"],
        "rates": [
            (0.0, 0.5, 3400.0),
            (0.5, 1.0, 3900.0),
            (1.0, 1.5, 4400.0),
            (1.5, 2.0, 4900.0),
            (2.0, 2.5, 5450.0),
            (2.5, None, 5950.0),
        ],
    },
]


def _find_supplier_id(conn, key: str) -> int | None:
    row = conn.execute(sa.text("SELECT id FROM parser_supplier WHERE key = :key"), {"key": key}).first()
    if row is None:
        return None
    return int(row[0])


def upgrade() -> None:
    conn = op.get_bind()

    supplier_ids_by_key: dict[str, int] = {}
    keep_ids: set[int] = set()

    for item in _TARIFFS:
        supplier_id = _find_supplier_id(conn, item["key"])
        if supplier_id is None:
            for legacy_key in item["legacy_keys"]:
                supplier_id = _find_supplier_id(conn, legacy_key)
                if supplier_id is not None:
                    conn.execute(
                        sa.text("UPDATE parser_supplier SET key = :new_key WHERE id = :supplier_id"),
                        {"new_key": item["key"], "supplier_id": supplier_id},
                    )
                    break
        if supplier_id is None:
            supplier_id = int(
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO parser_supplier (key, name, category, parent_supplier_id, alt_position, rate_currency)
                        VALUES (:key, :name, :category, NULL, 0, :rate_currency)
                        RETURNING id
                        """
                    ),
                    {
                        "key": item["key"],
                        "name": item["name"],
                        "category": item["category"],
                        "rate_currency": item["rate_currency"],
                    },
                ).scalar_one()
            )
        supplier_ids_by_key[item["key"]] = supplier_id
        keep_ids.add(supplier_id)

    for item in _TARIFFS:
        supplier_id = supplier_ids_by_key[item["key"]]
        parent_key = item["parent_key"]
        parent_id = supplier_ids_by_key[parent_key] if parent_key else None
        conn.execute(
            sa.text(
                """
                UPDATE parser_supplier
                SET name = :name,
                    category = :category,
                    parent_supplier_id = :parent_supplier_id,
                    alt_position = :alt_position,
                    rate_currency = :rate_currency
                WHERE id = :supplier_id
                """
            ),
            {
                "supplier_id": supplier_id,
                "name": item["name"],
                "category": item["category"],
                "parent_supplier_id": parent_id,
                "alt_position": int(item["alt_position"]),
                "rate_currency": item["rate_currency"],
            },
        )
        conn.execute(
            sa.text("DELETE FROM parser_supplier_shipping_rate WHERE supplier_id = :supplier_id"),
            {"supplier_id": supplier_id},
        )
        for min_kg, max_kg, rub in item["rates"]:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO parser_supplier_shipping_rate (supplier_id, min_kg, max_kg, rate_rub)
                    VALUES (:supplier_id, :min_kg, :max_kg, :rate_rub)
                    """
                ),
                {
                    "supplier_id": supplier_id,
                    "min_kg": float(min_kg),
                    "max_kg": (float(max_kg) if max_kg is not None else None),
                    "rate_rub": float(rub),
                },
            )

    eu_supplier_id = supplier_ids_by_key["eu"]
    all_supplier_rows = conn.execute(sa.text("SELECT id FROM parser_supplier")).fetchall()
    extra_supplier_ids = [int(row[0]) for row in all_supplier_rows if int(row[0]) not in keep_ids]

    conn.execute(
        sa.text(
            """
            UPDATE parser_source
            SET supplier_id = :eu_supplier_id
            WHERE supplier_id IS NULL
            """
        ),
        {"eu_supplier_id": eu_supplier_id},
    )

    for supplier_id in extra_supplier_ids:
        conn.execute(
            sa.text(
                """
                UPDATE parser_source
                SET supplier_id = :eu_supplier_id
                WHERE supplier_id = :supplier_id
                """
            ),
            {"eu_supplier_id": eu_supplier_id, "supplier_id": supplier_id},
        )
        conn.execute(sa.text("DELETE FROM parser_supplier WHERE id = :supplier_id"), {"supplier_id": supplier_id})


def downgrade() -> None:
    # Data migration is intentionally not reverted automatically.
    pass

