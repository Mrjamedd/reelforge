"""Pydantic schemas for request validation and API responses."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, field_validator

from app.models.models import JobStatus, Platform, PrivacyLevel


# ─── Auth ─────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Platform Account ─────────────────────────────────────────────────────────


class PlatformAccountOut(BaseModel):
    id: uuid.UUID
    platform: Platform
    platform_username: str | None
    token_expires_at: datetime | None
    connected_at: datetime

    model_config = {"from_attributes": True}


class PlatformStatusOut(BaseModel):
    platform: Platform
    connected: bool
    account: PlatformAccountOut | None
    configured: bool  # whether env credentials are set
    pending_approval: bool  # whether platform app review is needed


# ─── Upload ───────────────────────────────────────────────────────────────────


class UploadOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    storage_key: str
    thumbnail_key: str | None
    mime_type: str
    file_size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Publish Job ──────────────────────────────────────────────────────────────


class PublishJobCreate(BaseModel):
    upload_id: uuid.UUID
    platform: Platform
    title: str | None = None
    caption: str | None = None
    hashtags: str | None = None  # comma-separated
    privacy: PrivacyLevel | None = PrivacyLevel.PUBLIC
    scheduled_for: datetime | None = None  # UTC; None = publish immediately

    @field_validator("hashtags")
    @classmethod
    def clean_hashtags(cls, v: str | None) -> str | None:
        if v:
            # Normalize: strip spaces, ensure # prefix
            tags = [t.strip().lstrip("#") for t in v.split(",") if t.strip()]
            return ",".join(tags)
        return v


class BulkPublishRequest(BaseModel):
    """Create multiple publish jobs from a single upload at once."""
    upload_id: uuid.UUID
    jobs: list[PublishJobCreate]


class PublishJobOut(BaseModel):
    id: uuid.UUID
    upload_id: uuid.UUID
    platform: Platform
    status: JobStatus
    title: str | None
    caption: str | None
    hashtags: str | None
    privacy: PrivacyLevel | None
    scheduled_for: datetime | None
    platform_post_id: str | None
    platform_post_url: str | None
    attempt_count: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Audit Log ────────────────────────────────────────────────────────────────


class AuditLogOut(BaseModel):
    id: uuid.UUID
    publish_job_id: uuid.UUID
    from_status: JobStatus | None
    to_status: JobStatus
    message: str | None
    api_response_summary: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Dashboard Summary ────────────────────────────────────────────────────────


class DashboardStats(BaseModel):
    total_uploads: int
    total_jobs: int
    jobs_by_status: dict[str, int]
    recent_jobs: list[PublishJobOut]
