"""
SQLAlchemy ORM models for ReelForge.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Enums ────────────────────────────────────────────────────────────────────


class Platform(str, enum.Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    POSTED = "posted"
    FAILED = "failed"
    REQUIRES_MANUAL = "requires_manual"  # e.g. TikTok creator mode
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class PrivacyLevel(str, enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNLISTED = "unlisted"
    FRIENDS = "friends"  # TikTok


# ─── Admin User ───────────────────────────────────────────────────────────────


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# ─── Platform Accounts ────────────────────────────────────────────────────────


class PlatformAccount(Base):
    """Stores OAuth credentials for a connected platform account."""

    __tablename__ = "platform_accounts"
    __table_args__ = (
        # For now, one connected account per platform.
        # Remove this constraint to support multiple accounts per platform.
        UniqueConstraint("platform", name="uq_platform_account_one_per_platform"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False, index=True)
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform_username: Mapped[str | None] = mapped_column(String(255))
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)  # space-separated
    extra_data: Mapped[dict | None] = mapped_column(JSONB)  # platform-specific extras
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    publish_jobs: Mapped[list["PublishJob"]] = relationship(back_populates="platform_account")


# ─── Upload ───────────────────────────────────────────────────────────────────


class Upload(Base):
    """Represents an uploaded video file, stored once."""

    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)  # path/key in storage
    thumbnail_key: Mapped[str | None] = mapped_column(String(1000))
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column()
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    publish_jobs: Mapped[list["PublishJob"]] = relationship(back_populates="upload")
    uploaded_by: Mapped["AdminUser"] = relationship()


# ─── Publish Job ──────────────────────────────────────────────────────────────


class PublishJob(Base):
    """One publish attempt to one platform for one upload."""

    __tablename__ = "publish_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploads.id"), nullable=False, index=True
    )
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_accounts.id"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.QUEUED, nullable=False, index=True
    )

    # Metadata for this specific platform post
    title: Mapped[str | None] = mapped_column(String(500))
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[str | None] = mapped_column(Text)  # comma-separated
    privacy: Mapped[PrivacyLevel | None] = mapped_column(Enum(PrivacyLevel))

    # Scheduling
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # UTC
    celery_task_id: Mapped[str | None] = mapped_column(String(255))

    # Platform response
    platform_post_id: Mapped[str | None] = mapped_column(String(500))  # ID returned by platform
    platform_post_url: Mapped[str | None] = mapped_column(String(1000))

    # Idempotency key prevents double-posting
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, default=lambda: str(uuid.uuid4())
    )

    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    upload: Mapped["Upload"] = relationship(back_populates="publish_jobs")
    platform_account: Mapped["PlatformAccount"] = relationship(back_populates="publish_jobs")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="publish_job")


# ─── Audit Log ────────────────────────────────────────────────────────────────


class AuditLog(Base):
    """Immutable append-only history of every publish job state transition."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    publish_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_jobs.id"), nullable=False, index=True
    )
    from_status: Mapped[JobStatus | None] = mapped_column(Enum(JobStatus))
    to_status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    api_response_summary: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    publish_job: Mapped["PublishJob"] = relationship(back_populates="audit_logs")
