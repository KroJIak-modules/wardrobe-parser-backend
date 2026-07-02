"""Fix public catalog filter keyword rules.

Revision ID: 0087_fix_public_catalog_filter_keywords
Revises: 0086_drop_pricing_settings_business_defaults
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0087_fix_public_catalog_filter_keywords"
down_revision = "0086_drop_pricing_settings_business_defaults"
branch_labels = None
depends_on = None


LOCAL_KEYWORDS_BY_SLUG: dict[str, list[str]] = {
    "futbolki-i-longslivy": ["t shirt", "t shirts", "tee", "tees", "long sleeve", "long sleeves"],
    "futbolki-i-topy": ["top", "tops", "tank", "tanks", "camisole", "camisoles", "corset", "corsets", "bodysuit", "bodysuits"],
    "rubashki-i-polo": ["shirt", "shirts", "shirting", "polo", "polos", "button up", "button down"],
    "rubashki-i-bluzy": ["shirt", "shirts", "shirting", "blouse", "blouses", "button up", "button down"],
    "svitshoty-i-hudi": ["hoodie", "hoodies", "sweatshirt", "sweatshirts", "sweater", "sweaters", "cardigan", "cardigans", "knitwear", "jumper", "pullover"],
    "platya": ["dress", "dresses", "mini dress", "midi dress", "maxi dress", "gown", "jumpsuit", "romper", "playsuit"],
    "verhnyaya-odezhda": ["outerwear", "jacket", "jackets", "coat", "coats", "blazer", "blazers", "parka", "puffer", "windbreaker", "fleece"],
    "dzhinsy-i-shtany": ["bottom", "bottoms", "pants", "trousers", "jeans", "joggers", "leggings", "denim", "baggy jeans", "slacks", "chinos"],
    "shorty": ["short", "shorts", "shorts swimwear"],
    "shorty-i-yubki": ["short", "shorts", "skirt", "skirts", "mini skirt", "midi skirt", "maxi skirt"],
    "krossovki-i-kedy": ["sneaker", "sneakers", "trainer", "trainers", "shoe", "shoes", "footwear"],
    "botinki-i-sapogi": ["boot", "boots", "ankle boots", "chelsea boots", "combat boots", "cowboy boots", "platform boots"],
    "tufli": ["heel", "heels", "pump", "pumps", "sandal", "sandals", "flat", "flats", "loafer", "loafers", "mule", "mules", "wedge", "wedges"],
    "ukrasheniya": ["jewelry", "jewellery", "jewelery", "earring", "earrings", "necklace", "bracelet", "ring", "rings"],
    "sumki": ["bag", "bags", "backpack", "backpacks", "handbag", "handbags", "shoulder bag", "shoulder bags", "crossbody", "messenger", "clutch", "purse"],
    "remni": ["belt", "belts"],
    "golovnye-ubory": ["hat", "hats", "cap", "caps", "beanie", "beanies"],
    "ochki": ["sunglasses", "glasses", "eyewear"],
    "drugoe": ["accessory", "accessories", "misc", "other"],
}

TITLE_KEYWORDS_BY_SLUG: dict[str, list[str]] = {
    "futbolki-i-longslivy": ["t shirt", "t shirts", "tee", "tees", "long sleeve", "long sleeves"],
    "futbolki-i-topy": ["top", "tops", "tank", "tanks", "camisole", "corset", "corsets", "bodysuit", "bralette", "bra"],
    "rubashki-i-polo": ["shirt", "shirts", "polo", "polos"],
    "rubashki-i-bluzy": ["shirt", "shirts", "blouse", "blouses"],
    "svitshoty-i-hudi": ["hoodie", "hoodies", "sweatshirt", "sweatshirts", "sweater", "sweaters", "cardigan", "cardigans", "knit", "knitwear", "jumper", "pullover"],
    "platya": ["dress", "dresses", "gown", "jumpsuit", "romper", "playsuit"],
    "verhnyaya-odezhda": ["jacket", "jackets", "coat", "coats", "blazer", "blazers", "parka", "puffer", "windbreaker", "overshirt", "fleece"],
    "dzhinsy-i-shtany": ["pants", "trousers", "jeans", "joggers", "leggings", "denim"],
    "shorty": ["short", "shorts"],
    "shorty-i-yubki": ["short", "shorts", "skirt", "skirts"],
    "krossovki-i-kedy": ["sneaker", "sneakers", "trainer", "trainers", "shoe", "shoes"],
    "botinki-i-sapogi": ["boot", "boots"],
    "tufli": ["heel", "heels", "pump", "pumps", "sandal", "sandals", "flat", "flats", "loafer", "loafers", "mule", "mules", "wedge", "wedges"],
    "ukrasheniya": ["jewelry", "jewellery", "jewelery", "earring", "earrings", "necklace", "bracelet", "ring", "rings"],
    "sumki": ["bag", "bags", "backpack", "backpacks", "handbag", "handbags", "shoulder bag", "crossbody", "messenger", "clutch", "purse"],
    "remni": ["belt", "belts"],
    "golovnye-ubory": ["hat", "hats", "cap", "caps", "beanie", "beanies"],
    "ochki": ["sunglasses", "glasses", "eyewear"],
}


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _request_filter_assignment_rebuild(bind: Connection) -> None:
    if not _table_exists(bind, "filter_assignment_runtime_state"):
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO filter_assignment_runtime_state (
                id,
                target_revision,
                applied_revision,
                rebuild_requested_at,
                last_error
            )
            VALUES (1, 1, 0, now(), NULL)
            ON CONFLICT (id) DO UPDATE
            SET
                target_revision = GREATEST(
                    filter_assignment_runtime_state.target_revision,
                    filter_assignment_runtime_state.applied_revision
                ) + 1,
                rebuild_requested_at = now(),
                last_error = NULL
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "filters"):
        return

    slug_to_filter_id = {
        str(slug): int(filter_id)
        for filter_id, slug in bind.execute(sa.text("SELECT id, slug FROM filters"))
        if slug is not None
    }

    local_rows: list[dict[str, object]] = []
    title_rows: list[dict[str, object]] = []
    affected_filter_ids: list[int] = []
    for slug in sorted(set(LOCAL_KEYWORDS_BY_SLUG) | set(TITLE_KEYWORDS_BY_SLUG)):
        filter_id = slug_to_filter_id.get(slug)
        if filter_id is None:
            continue
        affected_filter_ids.append(filter_id)
        local_rows.extend(
            {"filter_id": filter_id, "keyword": keyword}
            for keyword in LOCAL_KEYWORDS_BY_SLUG.get(slug, [])
        )
        title_rows.extend(
            {"filter_id": filter_id, "keyword": keyword}
            for keyword in TITLE_KEYWORDS_BY_SLUG.get(slug, [])
        )

    if not affected_filter_ids:
        return

    bind.execute(
        sa.text("DELETE FROM filter_local_category_keywords WHERE filter_id = ANY(:filter_ids)"),
        {"filter_ids": affected_filter_ids},
    )
    bind.execute(
        sa.text("DELETE FROM filter_title_keywords WHERE filter_id = ANY(:filter_ids)"),
        {"filter_ids": affected_filter_ids},
    )
    if local_rows:
        op.bulk_insert(
            sa.table(
                "filter_local_category_keywords",
                sa.column("filter_id", sa.BigInteger()),
                sa.column("keyword", sa.Text()),
            ),
            local_rows,
        )
    if title_rows:
        op.bulk_insert(
            sa.table(
                "filter_title_keywords",
                sa.column("filter_id", sa.BigInteger()),
                sa.column("keyword", sa.Text()),
            ),
            title_rows,
        )
    _request_filter_assignment_rebuild(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "filters"):
        return
    slugs = tuple(sorted(set(LOCAL_KEYWORDS_BY_SLUG) | set(TITLE_KEYWORDS_BY_SLUG)))
    if not slugs:
        return
    bind.execute(
        sa.text(
            """
            DELETE FROM filter_local_category_keywords
            WHERE filter_id IN (SELECT id FROM filters WHERE slug = ANY(:slugs))
            """
        ),
        {"slugs": list(slugs)},
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM filter_title_keywords
            WHERE filter_id IN (SELECT id FROM filters WHERE slug = ANY(:slugs))
            """
        ),
        {"slugs": list(slugs)},
    )
