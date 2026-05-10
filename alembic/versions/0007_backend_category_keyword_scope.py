"""add category keyword scope

Revision ID: 0007_category_kw_scope
Revises: 0006_backend_drop_raw_data
Create Date: 2026-04-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0007_category_kw_scope"
down_revision = "0006_backend_drop_raw_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "parser_category_keyword" not in inspector.get_table_names():
        return
    op.add_column(
        "parser_category_keyword",
        sa.Column("keyword_scope", sa.String(length=16), nullable=False, server_default="local"),
    )
    op.drop_constraint("uq_parser_category_keyword", "parser_category_keyword", type_="unique")
    op.create_unique_constraint(
        "uq_parser_category_keyword_scope",
        "parser_category_keyword",
        ["category_id", "keyword", "keyword_scope"],
    )
    op.create_index(
        "idx_parser_category_keyword_scope",
        "parser_category_keyword",
        ["keyword_scope"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "parser_category_keyword" not in inspector.get_table_names():
        return
    op.drop_index("idx_parser_category_keyword_scope", table_name="parser_category_keyword")
    op.drop_constraint("uq_parser_category_keyword_scope", "parser_category_keyword", type_="unique")
    op.create_unique_constraint(
        "uq_parser_category_keyword",
        "parser_category_keyword",
        ["category_id", "keyword"],
    )
    op.drop_column("parser_category_keyword", "keyword_scope")
