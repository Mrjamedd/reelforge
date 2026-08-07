"""User-scoped accounts and staging workspace

Revision ID: 002_user_scoped_workspace
Revises: 001_initial
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002_user_scoped_workspace"
down_revision = "001_initial"
branch_labels = None
depends_on = None

privacy_enum = postgresql.ENUM(
    "public", "private", "unlisted", "friends", name="privacylevel", create_type=False
)


def upgrade() -> None:
    op.add_column(
        "platform_accounts",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.execute(
        """
        UPDATE platform_accounts
        SET owner_id = (SELECT id FROM admin_users ORDER BY created_at ASC LIMIT 1)
        WHERE owner_id IS NULL
        """
    )

    op.alter_column("platform_accounts", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_platform_accounts_owner_id_admin_users",
        "platform_accounts",
        "admin_users",
        ["owner_id"],
        ["id"],
    )
    op.create_index("ix_platform_accounts_owner_id", "platform_accounts", ["owner_id"])
    op.drop_constraint(
        "uq_platform_account_one_per_platform", "platform_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_platform_account_owner_platform",
        "platform_accounts",
        ["owner_id", "platform"],
    )

    op.create_table(
        "publish_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("default_title", sa.String(500)),
        sa.Column("default_caption", sa.Text()),
        sa.Column("default_hashtags", sa.Text()),
        sa.Column("default_privacy", privacy_enum),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("admin_user_id", name="uq_publish_profile_user"),
    )
    op.create_index("ix_publish_profiles_admin_user_id", "publish_profiles", ["admin_user_id"])

    op.create_table(
        "staged_publishes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True)),
        sa.Column("selected_platforms", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["upload_id"], ["uploads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("admin_user_id", name="uq_staged_publish_user"),
    )
    op.create_index("ix_staged_publishes_admin_user_id", "staged_publishes", ["admin_user_id"])
    op.create_index("ix_staged_publishes_upload_id", "staged_publishes", ["upload_id"])


def downgrade() -> None:
    op.drop_index("ix_staged_publishes_upload_id", table_name="staged_publishes")
    op.drop_index("ix_staged_publishes_admin_user_id", table_name="staged_publishes")
    op.drop_table("staged_publishes")

    op.drop_index("ix_publish_profiles_admin_user_id", table_name="publish_profiles")
    op.drop_table("publish_profiles")

    op.drop_constraint(
        "uq_platform_account_owner_platform", "platform_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_platform_account_one_per_platform", "platform_accounts", ["platform"]
    )
    op.drop_index("ix_platform_accounts_owner_id", table_name="platform_accounts")
    op.drop_constraint(
        "fk_platform_accounts_owner_id_admin_users", "platform_accounts", type_="foreignkey"
    )
    op.drop_column("platform_accounts", "owner_id")
