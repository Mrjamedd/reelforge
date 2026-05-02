"""Store upload validation metadata

Revision ID: 003_upload_validation_metadata
Revises: 002_user_scoped_workspace
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "003_upload_validation_metadata"
down_revision = "002_user_scoped_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("source_metadata", postgresql.JSONB()))
    op.add_column("uploads", sa.Column("validation_warnings", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("uploads", "validation_warnings")
    op.drop_column("uploads", "source_metadata")
