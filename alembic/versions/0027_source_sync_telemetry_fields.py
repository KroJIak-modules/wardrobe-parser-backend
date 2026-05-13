"""source sync telemetry fields

Revision ID: 0027_source_sync_telemetry_fields
Revises: 0026_source_attribute_visibility_flags
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0027_source_sync_telemetry_fields"
down_revision = "0026_source_attribute_visibility_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parser_source", sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("parser_source", sa.Column("last_sync_duration_sec", sa.Integer(), nullable=True))
    op.add_column("parser_source", sa.Column("last_sync_status", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("parser_source", "last_sync_status")
    op.drop_column("parser_source", "last_sync_duration_sec")
    op.drop_column("parser_source", "last_sync_at")
