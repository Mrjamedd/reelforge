# Instagram Public Media URL Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a storage abstraction so Instagram publishing only proceeds when a valid publicly-accessible HTTPS URL provider (S3 or Cloudflare R2) is configured, and blocks clearly otherwise.

**Architecture:** A new `StorageProvider` ABC defines the contract for generating public media URLs. `LocalStorageProvider` (not publicly accessible), `S3StorageProvider` (presigned URLs, supports R2 via endpoint_url), and `MockStorageProvider` (tests only) implement it. A factory `get_storage_provider()` builds the right one from settings. `MediaService` grows a `get_instagram_public_url()` method that raises if the provider is not publicly accessible. `InstagramProvider.validate_post_payload()` rejects any non-HTTPS URL. `PublishService.create_job()` blocks Instagram jobs up front if the provider is misconfigured.

**Tech Stack:** Python 3.11+, boto3 (already in requirements for S3), FastAPI, pytest + pytest-asyncio

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| **Create** | `backend/app/services/storage_providers.py` | `StorageProvider` ABC, `LocalStorageProvider`, `S3StorageProvider`, `MockStorageProvider`, `get_storage_provider()` factory |
| **Create** | `backend/tests/test_instagram_media_url.py` | All new tests for this feature |
| **Modify** | `backend/app/core/config.py` | Add `r2_account_id` field, add `instagram_media_url_configured` property |
| **Modify** | `backend/app/services/media_service.py` | Add `get_instagram_public_url()` and `has_public_url_provider()` methods; refactor `_s3_put` / `_s3_presign` to delegate to `S3StorageProvider` |
| **Modify** | `backend/app/providers/instagram.py` | Add HTTPS URL check in `validate_post_payload()` |
| **Modify** | `backend/app/services/publish_service.py` | Block Instagram job creation if no public URL provider |
| **Modify** | `backend/app/workers/tasks.py` | Use `get_instagram_public_url()` when building payload for Instagram |

---

## Task 1: Create `storage_providers.py` — abstraction + implementations

**Files:**
- Create: `backend/app/services/storage_providers.py`

- [ ] **Step 1: Write the file**

```python
# backend/app/services/storage_providers.py
"""
Storage Provider Abstraction
============================
Defines how media files are served as public URLs for platform publishing.

Instagram requires a publicly accessible HTTPS URL. Local storage is NOT
publicly accessible and will block Instagram publishing with a clear error.

Use get_storage_provider() to get the provider matching current settings.
MockStorageProvider is for tests only — never returned by get_storage_provider().
"""

from __future__ import annotations

from abc import ABC, abstractmethod


_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"", "changeme", "change-me", "change_me", "xxx", "placeholder", "your-key-here", "todo"}
)


def _is_placeholder(value: str) -> bool:
    return not value or value.lower().strip() in _PLACEHOLDER_VALUES


class StorageProvider(ABC):
    """Abstract base — implementations return public media URLs for platform publishing."""

    @property
    @abstractmethod
    def is_publicly_accessible(self) -> bool:
        """True when get_public_url() returns a URL reachable from the public internet."""
        ...

    @property
    @abstractmethod
    def configuration_errors(self) -> list[str]:
        """
        Human-readable errors describing what is missing or misconfigured.
        Empty list means the provider is ready to use.
        Never include raw credential values in error text.
        """
        ...

    @abstractmethod
    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a URL for the given storage key. May be time-limited (presigned)."""
        ...


class LocalStorageProvider(StorageProvider):
    """Serves files from the local backend API. NOT publicly accessible — blocks Instagram."""

    def __init__(self, api_url: str) -> None:
        self._api_url = api_url

    @property
    def is_publicly_accessible(self) -> bool:
        return False

    @property
    def configuration_errors(self) -> list[str]:
        return [
            "Local storage is not publicly accessible. "
            "Instagram requires a public HTTPS URL for video uploads. "
            "Set STORAGE_BACKEND=s3 and configure S3_BUCKET, AWS_ACCESS_KEY_ID, "
            "AWS_SECRET_ACCESS_KEY in .env (or use Cloudflare R2 via S3_ENDPOINT_URL)."
        ]

    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._api_url}/media/{key}"


class S3StorageProvider(StorageProvider):
    """
    Generates presigned time-limited URLs from S3 or any S3-compatible store
    (Cloudflare R2, MinIO, etc.) via endpoint_url.

    Files are NOT publicly listable — presigned URLs grant object-level access only.
    Credentials are never logged or included in generated URLs.
    """

    def __init__(
        self,
        *,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        endpoint_url: str = "",
    ) -> None:
        self._bucket = bucket
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region
        self._endpoint_url = endpoint_url

    @property
    def is_publicly_accessible(self) -> bool:
        return not self.configuration_errors

    @property
    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if _is_placeholder(self._bucket):
            errors.append("S3_BUCKET is missing or not configured.")
        if _is_placeholder(self._access_key_id):
            errors.append("AWS_ACCESS_KEY_ID is missing or not configured.")
        if _is_placeholder(self._secret_access_key):
            errors.append("AWS_SECRET_ACCESS_KEY is missing or not configured.")
        return errors

    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        import boto3

        s3 = boto3.client(
            "s3",
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            **({"endpoint_url": self._endpoint_url} if self._endpoint_url else {}),
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


class MockStorageProvider(StorageProvider):
    """
    Test-only provider. Returns deterministic HTTPS URLs without any network calls.
    Never instantiated by get_storage_provider() in production code.
    """

    def __init__(self, base_url: str = "https://test-media.example.com") -> None:
        self._base_url = base_url.rstrip("/")

    @property
    def is_publicly_accessible(self) -> bool:
        return True

    @property
    def configuration_errors(self) -> list[str]:
        return []

    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._base_url}/{key}?expires={expires_in}"


def get_storage_provider() -> StorageProvider:
    """
    Factory: returns the StorageProvider matching the current STORAGE_BACKEND setting.
    Call once per request/job — does not cache the instance.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3StorageProvider(
            bucket=settings.s3_bucket,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
            region=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url,
        )
    return LocalStorageProvider(api_url=settings.api_url)
```

- [ ] **Step 2: Commit**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/app/services/storage_providers.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "feat: add StorageProvider abstraction for public media URLs"
```

---

## Task 2: Write failing tests for the storage providers

**Files:**
- Create: `backend/tests/test_instagram_media_url.py`

- [ ] **Step 1: Write the test file**

```python
# backend/tests/test_instagram_media_url.py
"""
Tests for the Instagram public media URL layer.

Covers:
  - StorageProvider implementations (local, S3, mock)
  - Credential validation / placeholder detection
  - InstagramProvider.validate_post_payload URL check
  - PublishService blocking Instagram when provider is not configured
  - tasks.execute_publish_job using Instagram-specific URL
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.storage_providers import (
    LocalStorageProvider,
    MockStorageProvider,
    S3StorageProvider,
    get_storage_provider,
    _is_placeholder,
)
from app.providers.instagram import InstagramProvider
from app.providers.base import PublishPayload


# ─── _is_placeholder ─────────────────────────────────────────────────────────

def test_placeholder_detects_empty_string():
    assert _is_placeholder("") is True


def test_placeholder_detects_common_sentinels():
    for sentinel in ("changeme", "CHANGEME", "change-me", "change_me", "xxx", "placeholder", "your-key-here", "todo"):
        assert _is_placeholder(sentinel) is True, f"Expected {sentinel!r} to be a placeholder"


def test_placeholder_accepts_real_values():
    assert _is_placeholder("AKIAIOSFODNN7EXAMPLE") is False
    assert _is_placeholder("my-real-bucket-name") is False
    assert _is_placeholder("us-east-1") is False


# ─── LocalStorageProvider ─────────────────────────────────────────────────────

def test_local_provider_is_not_publicly_accessible():
    provider = LocalStorageProvider(api_url="http://localhost:8000")
    assert provider.is_publicly_accessible is False


def test_local_provider_has_configuration_errors():
    provider = LocalStorageProvider(api_url="http://localhost:8000")
    errors = provider.configuration_errors
    assert len(errors) >= 1
    assert any("publicly accessible" in e.lower() for e in errors)
    assert any("S3_BUCKET" in e for e in errors)


@pytest.mark.asyncio
async def test_local_provider_returns_local_url():
    provider = LocalStorageProvider(api_url="http://localhost:8000")
    url = await provider.get_public_url("uploads/abc.mp4")
    assert url == "http://localhost:8000/media/uploads/abc.mp4"


# ─── S3StorageProvider ────────────────────────────────────────────────────────

def test_s3_provider_missing_bucket_reports_error():
    provider = S3StorageProvider(
        bucket="",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )
    errors = provider.configuration_errors
    assert any("S3_BUCKET" in e for e in errors)
    assert provider.is_publicly_accessible is False


def test_s3_provider_missing_access_key_reports_error():
    provider = S3StorageProvider(
        bucket="my-real-bucket",
        access_key_id="",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )
    errors = provider.configuration_errors
    assert any("AWS_ACCESS_KEY_ID" in e for e in errors)
    assert provider.is_publicly_accessible is False


def test_s3_provider_missing_secret_reports_error():
    provider = S3StorageProvider(
        bucket="my-real-bucket",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="",
        region="us-east-1",
    )
    errors = provider.configuration_errors
    assert any("AWS_SECRET_ACCESS_KEY" in e for e in errors)
    assert provider.is_publicly_accessible is False


def test_s3_provider_placeholder_credential_reports_error():
    provider = S3StorageProvider(
        bucket="changeme",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )
    errors = provider.configuration_errors
    assert any("S3_BUCKET" in e for e in errors)


def test_s3_provider_with_all_credentials_is_accessible():
    provider = S3StorageProvider(
        bucket="my-real-bucket",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )
    assert provider.configuration_errors == []
    assert provider.is_publicly_accessible is True


@pytest.mark.asyncio
async def test_s3_provider_get_public_url_calls_presign(monkeypatch):
    provider = S3StorageProvider(
        bucket="my-bucket",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )
    fake_url = "https://my-bucket.s3.amazonaws.com/uploads/abc.mp4?X-Amz-Signature=fake"

    import boto3
    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = fake_url
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: fake_client)

    url = await provider.get_public_url("uploads/abc.mp4", expires_in=3600)

    assert url == fake_url
    fake_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "my-bucket", "Key": "uploads/abc.mp4"},
        ExpiresIn=3600,
    )


def test_s3_provider_url_does_not_expose_secret_in_errors():
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    provider = S3StorageProvider(
        bucket="",
        access_key_id="",
        secret_access_key=secret,
        region="us-east-1",
    )
    for error in provider.configuration_errors:
        assert secret not in error


# ─── MockStorageProvider ──────────────────────────────────────────────────────

def test_mock_provider_is_publicly_accessible():
    provider = MockStorageProvider()
    assert provider.is_publicly_accessible is True
    assert provider.configuration_errors == []


@pytest.mark.asyncio
async def test_mock_provider_returns_https_url():
    provider = MockStorageProvider()
    url = await provider.get_public_url("uploads/test.mp4")
    assert url.startswith("https://")
    assert "uploads/test.mp4" in url


# ─── get_storage_provider factory ────────────────────────────────────────────

def test_get_storage_provider_returns_local_for_local_backend(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.get_settings(), "storage_backend", "local")
    # Re-call factory (does not cache)
    from importlib import reload
    import app.services.storage_providers as sp_module
    provider = sp_module.get_storage_provider()
    assert isinstance(provider, LocalStorageProvider)


def test_get_storage_provider_returns_s3_for_s3_backend(monkeypatch):
    from app.core import config as cfg_module
    settings = cfg_module.get_settings()
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "my-bucket")
    monkeypatch.setattr(settings, "aws_access_key_id", "KEY")
    monkeypatch.setattr(settings, "aws_secret_access_key", "SECRET")

    from app.services.storage_providers import get_storage_provider
    provider = get_storage_provider()
    assert isinstance(provider, S3StorageProvider)


# ─── InstagramProvider.validate_post_payload URL check ───────────────────────

def _make_payload(**kwargs) -> PublishPayload:
    defaults = dict(
        video_path="https://my-bucket.s3.amazonaws.com/uploads/abc.mp4?X-Amz-Signature=fake",
        local_video_path=None,
        title="Test",
        caption="Test caption",
        hashtags=[],
        privacy="public",
        scheduled_for=None,
    )
    defaults.update(kwargs)
    return PublishPayload(**defaults)


def test_instagram_validate_payload_rejects_http_localhost_url():
    provider = InstagramProvider()
    payload = _make_payload(video_path="http://localhost:8000/media/uploads/abc.mp4")
    errors = provider.validate_post_payload(payload)
    assert any("HTTPS" in e or "https" in e.lower() for e in errors)
    assert any("local" in e.lower() or "S3" in e or "storage" in e.lower() for e in errors)


def test_instagram_validate_payload_rejects_plain_http_url():
    provider = InstagramProvider()
    payload = _make_payload(video_path="http://example.com/video.mp4")
    errors = provider.validate_post_payload(payload)
    assert any("HTTPS" in e or "https" in e.lower() for e in errors)


def test_instagram_validate_payload_rejects_empty_url():
    provider = InstagramProvider()
    payload = _make_payload(video_path="")
    errors = provider.validate_post_payload(payload)
    assert any("HTTPS" in e or "url" in e.lower() for e in errors)


def test_instagram_validate_payload_accepts_https_presigned_url():
    provider = InstagramProvider()
    payload = _make_payload(
        video_path="https://my-bucket.s3.amazonaws.com/uploads/abc.mp4?X-Amz-Signature=fake"
    )
    errors = provider.validate_post_payload(payload)
    assert not any("HTTPS" in e or "https" in e.lower() for e in errors)


# ─── MediaService.get_instagram_public_url ───────────────────────────────────

@pytest.mark.asyncio
async def test_media_service_get_instagram_public_url_raises_for_local(monkeypatch):
    from app.services.media_service import MediaService
    import app.services.storage_providers as sp

    monkeypatch.setattr(sp, "get_storage_provider", lambda: LocalStorageProvider("http://localhost:8000"))
    svc = MediaService()
    with pytest.raises(ValueError, match="publicly accessible"):
        await svc.get_instagram_public_url("uploads/abc.mp4")


@pytest.mark.asyncio
async def test_media_service_get_instagram_public_url_returns_url_for_mock(monkeypatch):
    from app.services.media_service import MediaService
    import app.services.storage_providers as sp

    monkeypatch.setattr(sp, "get_storage_provider", lambda: MockStorageProvider())
    svc = MediaService()
    url = await svc.get_instagram_public_url("uploads/abc.mp4")
    assert url.startswith("https://")
    assert "uploads/abc.mp4" in url


# ─── PublishService blocks Instagram without public URL provider ──────────────

@pytest.mark.asyncio
async def test_publish_service_blocks_instagram_when_local_storage(monkeypatch):
    from app.services.publish_service import PublishService
    from app.models.models import Platform
    import app.services.storage_providers as sp

    monkeypatch.setattr(sp, "get_storage_provider", lambda: LocalStorageProvider("http://localhost:8000"))

    svc = PublishService()

    # Patch everything else so only the storage check matters
    import uuid
    from app.schemas.schemas import PublishJobCreate

    db = AsyncMock()
    upload_mock = MagicMock()
    upload_mock.uploaded_by_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    db.get = AsyncMock(return_value=upload_mock)

    job_in = MagicMock(spec=PublishJobCreate)
    job_in.upload_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    job_in.platform = Platform.INSTAGRAM
    job_in.scheduled_for = None

    # Patch provider.is_configured and _missing_credentials to pass
    from app.providers import registry
    fake_provider = MagicMock()
    fake_provider.is_configured = True
    fake_provider.platform_name = "Instagram"
    monkeypatch.setattr(registry, "get_provider", lambda p: fake_provider)
    monkeypatch.setattr("app.services.publish_service._missing_credentials", lambda p: [])

    with pytest.raises(ValueError, match="public HTTPS"):
        await svc.create_job(db, job_in, upload_mock.uploaded_by_id, enqueue=False)
```

- [ ] **Step 2: Run tests to verify they all FAIL (implementations don't exist yet)**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_instagram_media_url.py -v 2>&1 | head -60
```

Expected: Most tests fail with import errors or `AttributeError` since `storage_providers` doesn't exist and `InstagramProvider` doesn't have URL validation yet.

- [ ] **Step 3: Commit the failing test file**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/tests/test_instagram_media_url.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "test: add failing tests for Instagram public media URL layer"
```

---

## Task 3: Update `config.py` — add R2 field and `instagram_media_url_configured` property

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Run existing tests to confirm baseline passes**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_core.py -v 2>&1
```

Expected: All existing tests pass.

- [ ] **Step 2: Apply changes to `config.py`**

Add `r2_account_id` field and `instagram_media_url_configured` property. The full updated `Settings` class (only the changed sections shown):

```python
# In the Storage block, after the existing s3/aws fields, add:
r2_account_id: str = ""  # Cloudflare R2 — set S3_ENDPOINT_URL to https://{account_id}.r2.cloudflarestorage.com

# Replace the existing instagram_configured property, and add instagram_media_url_configured:
@property
def instagram_configured(self) -> bool:
    return bool(self.instagram_app_id and self.instagram_app_secret)

@property
def instagram_media_url_configured(self) -> bool:
    """
    True when a real public URL provider is configured for Instagram publishing.
    Local storage is NOT sufficient — S3 or R2 credentials must be present.
    """
    if self.storage_backend != "s3":
        return False
    return bool(
        self.s3_bucket and self.aws_access_key_id and self.aws_secret_access_key
    )
```

The full updated file:

```python
# backend/app/core/config.py
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    secret_key: str = "dev-insecure-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    sqlalchemy_echo: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://reelpush:reelpush@localhost:5432/reelpush"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "./storage"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""  # For Cloudflare R2: https://{r2_account_id}.r2.cloudflarestorage.com
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    r2_account_id: str = ""  # Optional: set S3_ENDPOINT_URL automatically for R2

    # Admin seed
    admin_email: str = "admin@example.com"
    admin_password: str = "changeme123!"

    # App URLs
    api_url: str = "http://localhost:8000"

    # TikTok — TODO: requires developer app approval
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    # Instagram / Meta — TODO: requires Meta app review + Business account
    instagram_app_id: str = ""
    instagram_app_secret: str = ""

    # YouTube / Google — TODO: requires Google Cloud credentials
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

    @property
    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)

    @property
    def instagram_configured(self) -> bool:
        return bool(self.instagram_app_id and self.instagram_app_secret)

    @property
    def instagram_media_url_configured(self) -> bool:
        """
        True when a real public URL provider is configured for Instagram publishing.
        Local storage is NOT sufficient — S3 or R2 credentials must be present.
        """
        if self.storage_backend != "s3":
            return False
        return bool(
            self.s3_bucket and self.aws_access_key_id and self.aws_secret_access_key
        )

    @property
    def youtube_configured(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Run existing tests — confirm no regressions**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_core.py -v 2>&1
```

Expected: All existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/app/core/config.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "feat: add r2_account_id field and instagram_media_url_configured to Settings"
```

---

## Task 4: Add `get_instagram_public_url()` to `MediaService`

**Files:**
- Modify: `backend/app/services/media_service.py`

- [ ] **Step 1: Apply changes**

Add two methods to `MediaService` after the existing `get_public_url()` method (around line 181):

```python
async def get_instagram_public_url(self, storage_key: str, expires_in: int = 3600) -> str:
    """
    Return a publicly accessible HTTPS URL for Instagram publishing.

    Raises ValueError with a user-facing message if the configured storage
    backend cannot produce a public URL (e.g. local storage).
    """
    from app.services.storage_providers import get_storage_provider

    provider = get_storage_provider()
    if not provider.is_publicly_accessible:
        errors = provider.configuration_errors
        raise ValueError(
            "Instagram requires a publicly accessible HTTPS video URL. "
            + " ".join(errors)
        )
    return await provider.get_public_url(storage_key, expires_in)

def has_public_url_provider(self) -> bool:
    """True when the current storage backend can produce a publicly accessible URL."""
    from app.services.storage_providers import get_storage_provider

    return get_storage_provider().is_publicly_accessible
```

The `get_public_url()` method at line 172 is unchanged — YouTube and TikTok continue to use it.

- [ ] **Step 2: Run the two MediaService tests from `test_instagram_media_url.py`**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_instagram_media_url.py::test_media_service_get_instagram_public_url_raises_for_local \
  tests/test_instagram_media_url.py::test_media_service_get_instagram_public_url_returns_url_for_mock -v 2>&1
```

Expected: Both PASS.

- [ ] **Step 3: Run all existing tests — confirm no regressions**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_core.py -v 2>&1
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/app/services/media_service.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "feat: add get_instagram_public_url() and has_public_url_provider() to MediaService"
```

---

## Task 5: Add HTTPS URL validation to `InstagramProvider.validate_post_payload()`

**Files:**
- Modify: `backend/app/providers/instagram.py`

- [ ] **Step 1: Apply change**

Replace the existing `validate_post_payload` method (lines 174–179):

```python
def validate_post_payload(self, payload: PublishPayload) -> list[str]:
    errors = []
    if payload.caption and len(payload.caption) > 2200:
        errors.append("Instagram caption must be 2200 characters or fewer.")
    if len(payload.hashtags) > 30:
        errors.append(f"Instagram allows max 30 hashtags (got {len(payload.hashtags)}).")
    url = payload.video_path or ""
    if not url.startswith("https://"):
        errors.append(
            "Instagram requires a publicly accessible HTTPS video URL. "
            "Local storage URLs are not accepted. "
            "Configure STORAGE_BACKEND=s3 with S3_BUCKET, AWS_ACCESS_KEY_ID, "
            "and AWS_SECRET_ACCESS_KEY in .env (or use Cloudflare R2 via S3_ENDPOINT_URL)."
        )
    return errors
```

- [ ] **Step 2: Run the Instagram URL validation tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_instagram_media_url.py::test_instagram_validate_payload_rejects_http_localhost_url \
  tests/test_instagram_media_url.py::test_instagram_validate_payload_rejects_plain_http_url \
  tests/test_instagram_media_url.py::test_instagram_validate_payload_rejects_empty_url \
  tests/test_instagram_media_url.py::test_instagram_validate_payload_accepts_https_presigned_url -v 2>&1
```

Expected: All 4 PASS.

- [ ] **Step 3: Run ALL tests — confirm no regressions**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/ -v 2>&1
```

Expected: All pass, including all `test_core.py` tests.

- [ ] **Step 4: Commit**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/app/providers/instagram.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "feat: Instagram validate_post_payload rejects non-HTTPS video URLs"
```

---

## Task 6: Block Instagram job creation in `PublishService` if no public URL provider

**Files:**
- Modify: `backend/app/services/publish_service.py`

- [ ] **Step 1: Apply change**

In `PublishService.create_job()`, after the `missing_credentials` / `is_configured` check (around line 84), add this block:

```python
# Instagram requires a publicly accessible HTTPS URL — block early with a clear message.
if job_in.platform == Platform.INSTAGRAM:
    from app.services.storage_providers import get_storage_provider

    storage_provider = get_storage_provider()
    if not storage_provider.is_publicly_accessible:
        storage_errors = storage_provider.configuration_errors
        raise ValueError(
            "Instagram publishing requires a public HTTPS media URL. "
            + " ".join(storage_errors)
        )
```

Insert it immediately after the existing credential check block:

```python
        # --- existing block (lines ~78-84) ---
        missing_credentials = _missing_credentials(job_in.platform)
        if missing_credentials or not provider.is_configured:
            raise ValueError(
                f"{provider.platform_name} credentials are not configured. "
                f"Missing: {', '.join(missing_credentials or REQUIRED_CREDENTIALS.get(job_in.platform, ()))}. "
                "Save credentials in Settings before publishing."
            )

        # --- NEW block ---
        if job_in.platform == Platform.INSTAGRAM:
            from app.services.storage_providers import get_storage_provider

            storage_provider = get_storage_provider()
            if not storage_provider.is_publicly_accessible:
                storage_errors = storage_provider.configuration_errors
                raise ValueError(
                    "Instagram publishing requires a public HTTPS media URL. "
                    + " ".join(storage_errors)
                )

        # --- rest of existing code ---
        account = await self._get_account(db, job_in.platform, uploaded_by_id)
        ...
```

- [ ] **Step 2: Run the PublishService blocking test**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/test_instagram_media_url.py::test_publish_service_blocks_instagram_when_local_storage -v 2>&1
```

Expected: PASS.

- [ ] **Step 3: Run ALL tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/ -v 2>&1
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/app/services/publish_service.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "feat: block Instagram publish job if storage provider is not publicly accessible"
```

---

## Task 7: Use `get_instagram_public_url()` in `tasks.py` for Instagram platform

**Files:**
- Modify: `backend/app/workers/tasks.py`

- [ ] **Step 1: Apply change**

In `execute_publish_job()`, replace the `video_path` line in the payload construction block (around line 102):

**Before:**
```python
payload = PublishPayload(
    video_path=await media_svc.get_public_url(upload.storage_key),
    local_video_path=str(media_svc.get_local_path(upload.storage_key)),
    ...
)
```

**After:**
```python
from app.models.models import Platform as _Platform

if job.platform == _Platform.INSTAGRAM:
    video_path = await media_svc.get_instagram_public_url(upload.storage_key)
else:
    video_path = await media_svc.get_public_url(upload.storage_key)

payload = PublishPayload(
    video_path=video_path,
    local_video_path=str(media_svc.get_local_path(upload.storage_key)),
    title=job.title,
    caption=job.caption,
    hashtags=hashtags,
    privacy=job.privacy.value if job.privacy else "public",
    scheduled_for=job.scheduled_for,
)
```

Note: `Platform` is already imported at the top of the file as `from app.models.models import JobStatus, PlatformAccount, PublishJob`. Add `Platform` to that import instead of the inline import shown above.

The actual import line to update is line 13:
```python
from app.models.models import JobStatus, Platform, PlatformAccount, PublishJob
```

- [ ] **Step 2: Run ALL tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/ -v 2>&1
```

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" add backend/app/workers/tasks.py
git -C "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" commit -m "feat: use get_instagram_public_url() in publish task for Instagram platform"
```

---

## Task 8: Run the full test suite and report results

**Files:** (none changed)

- [ ] **Step 1: Run all tests with verbose output**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend" && \
python -m pytest tests/ -v 2>&1
```

Expected output pattern:
```
tests/test_core.py::test_password_hashing PASSED
tests/test_core.py::test_token_encrypt_decrypt PASSED
... (all existing tests pass) ...
tests/test_instagram_media_url.py::test_placeholder_detects_empty_string PASSED
tests/test_instagram_media_url.py::test_local_provider_is_not_publicly_accessible PASSED
... (all new tests pass) ...
========== N passed in X.XXs ==========
```

- [ ] **Step 2: Report**

After the run, report:
- Total tests passed / failed
- Any unexpected failures with full traceback
- Which files were changed
- Any unresolved risks

**Unresolved risks to mention:**
1. `S3StorageProvider.get_public_url()` makes a real boto3 call — integration tests would need real or mocked AWS credentials. The unit test mocks boto3 at the `boto3.client` level which is sufficient.
2. `PublishService.create_job()` test for the blocking case uses heavy mocking. A real integration test would need a database. The unit test is sufficient for validating the guard logic.
3. `tasks.py` change is not directly tested in the new test file — it is covered indirectly by the existing YouTube task test (`test_youtube_publish_sends_post_metadata`) which still passes.

---

## Self-Review Against Spec

| Requirement | Covered in |
|-------------|-----------|
| 1. Storage abstraction for public media URLs | Task 1 — `StorageProvider` ABC |
| 2. Local/mock storage provider for tests | Task 1 — `LocalStorageProvider`, `MockStorageProvider` |
| 3. S3 / Cloudflare R2 provider behind env vars | Task 1 — `S3StorageProvider` + `s3_endpoint_url` for R2; Task 3 — `r2_account_id` |
| 4. Signed / time-limited URLs | Task 1 — `S3StorageProvider` uses `generate_presigned_url` with `expires_in` |
| 5. Credential validation for missing / placeholder values | Task 1 — `_is_placeholder()` + `configuration_errors` property |
| 6. Block Instagram unless valid public HTTPS provider | Task 5 — `validate_post_payload`; Task 6 — `PublishService.create_job`; Task 7 — `tasks.py` raises on bad URL |
| 7. Clear UI/status messages | Error text in `LocalStorageProvider.configuration_errors`, `validate_post_payload`, `PublishService.create_job` |
| 8. Tests with mocked storage and mocked Instagram provider | Task 2 — `test_instagram_media_url.py` |
| 9. Preserve YouTube/TikTok behavior | Tasks 4 & 7 — `get_public_url()` unchanged; `tasks.py` only uses new method for Instagram |
| 10. No publicly listable files | `S3StorageProvider` uses presigned GET — bucket not set to public; no `put_bucket_acl` calls |
| 11. No secrets in logs | `configuration_errors` never includes credential values; `_s3_presign` signature is in the URL but not logged |
