"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2024-01-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # admin_users
    op.create_table(
        'admin_users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_admin_users_email', 'admin_users', ['email'])

    # platform_accounts
    op.create_table(
        'platform_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('platform', sa.Enum('tiktok', 'instagram', 'youtube', name='platform'), nullable=False),
        sa.Column('platform_user_id', sa.String(255), nullable=False),
        sa.Column('platform_username', sa.String(255)),
        sa.Column('access_token_encrypted', sa.Text(), nullable=False),
        sa.Column('refresh_token_encrypted', sa.Text()),
        sa.Column('token_expires_at', sa.DateTime(timezone=True)),
        sa.Column('scopes', sa.Text()),
        sa.Column('extra_data', postgresql.JSONB()),
        sa.Column('connected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', name='uq_platform_account_one_per_platform'),
    )
    op.create_index('ix_platform_accounts_platform', 'platform_accounts', ['platform'])

    # uploads
    op.create_table(
        'uploads',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('original_filename', sa.String(500), nullable=False),
        sa.Column('storage_key', sa.String(1000), nullable=False),
        sa.Column('thumbnail_key', sa.String(1000)),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=False),
        sa.Column('duration_seconds', sa.Float()),
        sa.Column('width', sa.Integer()),
        sa.Column('height', sa.Integer()),
        sa.Column('uploaded_by_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['admin_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    # publish_jobs
    op.create_table(
        'publish_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('upload_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('platform_account_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('platform', sa.Enum('tiktok', 'instagram', 'youtube', name='platform'), nullable=False),
        sa.Column('status', sa.Enum(
            'queued', 'uploading', 'processing', 'posted',
            'failed', 'requires_manual', 'scheduled', 'cancelled',
            name='jobstatus'
        ), nullable=False, server_default='queued'),
        sa.Column('title', sa.String(500)),
        sa.Column('caption', sa.Text()),
        sa.Column('hashtags', sa.Text()),
        sa.Column('privacy', sa.Enum('public', 'private', 'unlisted', 'friends', name='privacylevel')),
        sa.Column('scheduled_for', sa.DateTime(timezone=True)),
        sa.Column('celery_task_id', sa.String(255)),
        sa.Column('platform_post_id', sa.String(500)),
        sa.Column('platform_post_url', sa.String(1000)),
        sa.Column('idempotency_key', sa.String(255), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['upload_id'], ['uploads.id']),
        sa.ForeignKeyConstraint(['platform_account_id'], ['platform_accounts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_publish_jobs_upload_id', 'publish_jobs', ['upload_id'])
    op.create_index('ix_publish_jobs_platform', 'publish_jobs', ['platform'])
    op.create_index('ix_publish_jobs_status', 'publish_jobs', ['status'])

    # audit_logs
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('publish_job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('from_status', sa.Enum(
            'queued', 'uploading', 'processing', 'posted',
            'failed', 'requires_manual', 'scheduled', 'cancelled',
            name='jobstatus'
        )),
        sa.Column('to_status', sa.Enum(
            'queued', 'uploading', 'processing', 'posted',
            'failed', 'requires_manual', 'scheduled', 'cancelled',
            name='jobstatus'
        ), nullable=False),
        sa.Column('message', sa.Text()),
        sa.Column('api_response_summary', postgresql.JSONB()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['publish_job_id'], ['publish_jobs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_publish_job_id', 'audit_logs', ['publish_job_id'])


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('publish_jobs')
    op.drop_table('uploads')
    op.drop_table('platform_accounts')
    op.drop_table('admin_users')
    op.execute("DROP TYPE IF EXISTS jobstatus")
    op.execute("DROP TYPE IF EXISTS platform")
    op.execute("DROP TYPE IF EXISTS privacylevel")
