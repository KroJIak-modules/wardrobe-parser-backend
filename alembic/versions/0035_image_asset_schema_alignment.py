"""align image_asset schema with runtime model

Revision ID: 0035_image_asset_schema_alignment
Revises: 0034_add_description_visible_override
Create Date: 2026-05-30 14:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_image_asset_schema_alignment"
down_revision = "0034_add_description_visible_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE image_asset ADD COLUMN IF NOT EXISTS source_url VARCHAR(2048)")
    op.execute("ALTER TABLE image_asset ADD COLUMN IF NOT EXISTS storage_mode VARCHAR(50)")
    op.execute("ALTER TABLE image_asset ADD COLUMN IF NOT EXISTS stored_path VARCHAR(2048)")

    # Backfill data for rows created by legacy schema.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'image_asset'
                  AND column_name = 'storage_rel_path'
            ) THEN
                EXECUTE '
                    UPDATE image_asset
                    SET stored_path = storage_rel_path
                    WHERE stored_path IS NULL
                ';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE image_asset
        SET storage_mode = 'stored_file'
        WHERE storage_mode IS NULL
        """
    )
    op.execute(
        """
        UPDATE image_asset
        SET source_url = CASE
            WHEN stored_path IS NOT NULL AND stored_path <> '' THEN CONCAT('stored://legacy/', stored_path)
            ELSE CONCAT('stored://legacy/id/', id::text)
        END
        WHERE source_url IS NULL OR source_url = ''
        """
    )

    op.alter_column("image_asset", "source_url", existing_type=sa.String(length=2048), nullable=False)
    op.alter_column("image_asset", "storage_mode", existing_type=sa.String(length=50), nullable=False)


def downgrade() -> None:
    # No-op downgrade: removing these columns can break runtime on mixed-version deploys.
    pass
