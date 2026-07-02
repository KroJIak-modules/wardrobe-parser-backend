"""Normalize filter slugs from filter titles.

Revision ID: 0081_filter_title_slugs
Revises: 0080_add_site_content
Create Date: 2026-07-01
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "0081_filter_title_slugs"
down_revision = "0080_add_site_content"
branch_labels = None
depends_on = None


_TRANSLIT = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def _slugify(raw: str) -> str:
    value = str(raw or "").strip().lower().translate(_TRANSLIT)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:255] or "filter"


def _unique_slug(base: str, used: set[str]) -> str:
    candidate = base or "filter"
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while f"{candidate}-{index}" in used:
        index += 1
    final = f"{candidate}-{index}"
    used.add(final)
    return final


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, title, slug FROM filters ORDER BY id ASC")).mappings().all()
    used: set[str] = set()
    replacements: list[tuple[int, str, str]] = []
    for row in rows:
        old_slug = str(row["slug"] or "").strip()
        new_slug = _unique_slug(_slugify(str(row["title"] or "")), used)
        if old_slug != new_slug:
            replacements.append((int(row["id"]), old_slug, new_slug))

    for filter_id, _old_slug, _new_slug in replacements:
        bind.execute(
            sa.text("UPDATE filters SET slug = :slug WHERE id = :id"),
            {"id": filter_id, "slug": f"__tmp_filter_slug_{filter_id}"},
        )

    for filter_id, old_slug, new_slug in replacements:
        bind.execute(
            sa.text("UPDATE filters SET slug = :slug WHERE id = :id"),
            {"id": filter_id, "slug": new_slug},
        )
        bind.execute(
            sa.text("UPDATE product_filter_assignments SET filter_slug = :new_slug WHERE filter_slug = :old_slug"),
            {"old_slug": old_slug, "new_slug": new_slug},
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO filter_assignment_runtime_state (
                id,
                target_revision,
                applied_revision,
                rebuild_requested_at,
                created_at,
                updated_at
            )
            VALUES (1, 1, 0, now(), now(), now())
            ON CONFLICT (id) DO UPDATE
            SET
                target_revision = GREATEST(
                    filter_assignment_runtime_state.target_revision,
                    filter_assignment_runtime_state.applied_revision
                ) + 1,
                rebuild_requested_at = now(),
                last_error = NULL,
                updated_at = now()
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade is not supported for filter slug normalization")
