"""Normalize men/women category slugs to Russian-translit style.

Revision ID: 0010_category_slug_ru_style
Revises: 0009_category_index_snapshot
Create Date: 2026-04-16 00:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_category_slug_ru_style"
down_revision = "0009_category_index_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Phase 1: move men/women prefixed slugs to temporary namespace to avoid unique collisions.
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = regexp_replace(slug, '^men-', 'tmp-men-')
        WHERE deleted_at IS NULL
          AND slug ~ '^men-'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = regexp_replace(slug, '^women-', 'tmp-women-')
        WHERE deleted_at IS NULL
          AND slug ~ '^women-'
    """))

    # Phase 2: rename key branch nodes to fully transliterated Russian names.
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'muzhskoe-odezhda'
        WHERE deleted_at IS NULL
          AND slug = 'tmp-men-clothes'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'muzhskoe-obuv'
        WHERE deleted_at IS NULL
          AND slug = 'tmp-men-shoes'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'muzhskoe-aksessuary'
        WHERE deleted_at IS NULL
          AND slug = 'tmp-men-accessories'
    """))

    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'zhenskoe-odezhda'
        WHERE deleted_at IS NULL
          AND slug = 'tmp-women-clothes'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'zhenskoe-obuv'
        WHERE deleted_at IS NULL
          AND slug = 'tmp-women-shoes'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'zhenskoe-aksessuary'
        WHERE deleted_at IS NULL
          AND slug = 'tmp-women-accessories'
    """))

    # Phase 3: rename the rest by prefix replacement.
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = regexp_replace(slug, '^tmp-men-', 'muzhskoe-')
        WHERE deleted_at IS NULL
          AND slug ~ '^tmp-men-'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = regexp_replace(slug, '^tmp-women-', 'zhenskoe-')
        WHERE deleted_at IS NULL
          AND slug ~ '^tmp-women-'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'men-clothes'
        WHERE deleted_at IS NULL
          AND slug = 'muzhskoe-odezhda'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'men-shoes'
        WHERE deleted_at IS NULL
          AND slug = 'muzhskoe-obuv'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'men-accessories'
        WHERE deleted_at IS NULL
          AND slug = 'muzhskoe-aksessuary'
    """))

    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'women-clothes'
        WHERE deleted_at IS NULL
          AND slug = 'zhenskoe-odezhda'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'women-shoes'
        WHERE deleted_at IS NULL
          AND slug = 'zhenskoe-obuv'
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = 'women-accessories'
        WHERE deleted_at IS NULL
          AND slug = 'zhenskoe-aksessuary'
    """))

    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = regexp_replace(slug, '^muzhskoe-', 'men-')
        WHERE deleted_at IS NULL
          AND slug ~ '^muzhskoe-'
          AND slug NOT IN ('muzhskoe', 'muzhskoe-odezhda', 'muzhskoe-obuv', 'muzhskoe-aksessuary')
    """))
    conn.execute(sa.text("""
        UPDATE parser_category
        SET slug = regexp_replace(slug, '^zhenskoe-', 'women-')
        WHERE deleted_at IS NULL
          AND slug ~ '^zhenskoe-'
          AND slug NOT IN ('zhenskoe', 'zhenskoe-odezhda', 'zhenskoe-obuv', 'zhenskoe-aksessuary')
    """))
