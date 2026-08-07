"""User accounts: email verification, app settings

Revision ID: 004_accounts_and_settings
Revises: 003_upload_validation_metadata
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_accounts_and_settings"
down_revision = "003_upload_validation_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add email verification flag to admin_users (default True for existing accounts)
    op.add_column(
        "admin_users",
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default="true"),
    )

    # Email verification tokens table
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_users.id"), nullable=False, index=True),
        sa.Column("token", sa.String(6), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Per-user app settings (encrypted platform credentials)
    op.create_table(
        "user_app_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_users.id"), nullable=False, index=True),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("admin_user_id", name="uq_user_app_settings_user"),
    )


def downgrade() -> None:
    op.drop_table("user_app_settings")
    op.drop_table("email_verification_tokens")
    op.drop_column("admin_users", "is_email_verified")
