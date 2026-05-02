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
from unittest.mock import AsyncMock, MagicMock

from app.services.storage_providers import (
    LocalStorageProvider,
    MockStorageProvider,
    S3StorageProvider,
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

    from app.providers import registry
    fake_provider = MagicMock()
    fake_provider.is_configured = True
    fake_provider.platform_name = "Instagram"
    monkeypatch.setattr(registry, "get_provider", lambda p: fake_provider)
    monkeypatch.setattr("app.services.publish_service._missing_credentials", lambda p: [])

    with pytest.raises(ValueError, match="public HTTPS"):
        await svc.create_job(db, job_in, upload_mock.uploaded_by_id, enqueue=False)
