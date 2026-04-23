"""
Basic tests for core components.
Run with: pytest backend/tests/ -v
"""

import pytest
from app.core.security import hash_password, verify_password, encrypt_token, decrypt_token, create_access_token, decode_access_token
from app.providers.tiktok import TikTokProvider
from app.providers.instagram import InstagramProvider
from app.providers.youtube import YouTubeProvider
from app.providers.base import PublishPayload
from app.providers.registry import get_provider, all_providers
from app.models.models import Platform


# ─── Security ─────────────────────────────────────────────────────────────────

def test_password_hashing():
    hashed = hash_password("supersecret123")
    assert hashed != "supersecret123"
    assert verify_password("supersecret123", hashed)
    assert not verify_password("wrongpassword", hashed)


def test_token_encrypt_decrypt():
    original = "my_oauth_access_token_abc123"
    encrypted = encrypt_token(original)
    assert encrypted != original
    assert decrypt_token(encrypted) == original


def test_jwt_roundtrip():
    token = create_access_token("user-id-123")
    assert token
    subject = decode_access_token(token)
    assert subject == "user-id-123"


def test_jwt_invalid():
    assert decode_access_token("not.a.valid.token") is None


# ─── Provider Registry ────────────────────────────────────────────────────────

def test_provider_registry_all_platforms():
    providers = all_providers()
    assert Platform.TIKTOK in providers
    assert Platform.INSTAGRAM in providers
    assert Platform.YOUTUBE in providers


def test_get_provider():
    p = get_provider(Platform.TIKTOK)
    assert p.platform_name == "TikTok"


def test_get_provider_invalid():
    with pytest.raises(ValueError):
        get_provider("nonexistent")  # type: ignore


# ─── Provider Validation ──────────────────────────────────────────────────────

def _make_payload(**kwargs) -> PublishPayload:
    defaults = dict(
        video_path="/tmp/test.mp4",
        title="Test",
        caption="Test caption",
        hashtags=[],
        privacy="public",
        scheduled_for=None,
    )
    defaults.update(kwargs)
    return PublishPayload(**defaults)


def test_tiktok_caption_too_long():
    p = TikTokProvider()
    payload = _make_payload(caption="x" * 2201)
    errors = p.validate_post_payload(payload)
    assert any("2200" in e for e in errors)


def test_tiktok_too_many_hashtags():
    p = TikTokProvider()
    payload = _make_payload(hashtags=[f"tag{i}" for i in range(31)])
    errors = p.validate_post_payload(payload)
    assert any("30" in e for e in errors)


def test_tiktok_valid_payload():
    p = TikTokProvider()
    payload = _make_payload(caption="Hello world", hashtags=["fyp", "trending"])
    errors = p.validate_post_payload(payload)
    assert errors == []


def test_instagram_caption_too_long():
    p = InstagramProvider()
    payload = _make_payload(caption="x" * 2201)
    errors = p.validate_post_payload(payload)
    assert len(errors) > 0


def test_youtube_title_too_long():
    p = YouTubeProvider()
    payload = _make_payload(title="x" * 101)
    errors = p.validate_post_payload(payload)
    assert any("100" in e for e in errors)


def test_youtube_description_too_long():
    p = YouTubeProvider()
    payload = _make_payload(caption="x" * 5001)
    errors = p.validate_post_payload(payload)
    assert len(errors) > 0


# ─── Provider interface completeness ─────────────────────────────────────────

def test_all_providers_have_required_methods():
    required = [
        "platform_name", "is_configured", "requires_app_review",
        "get_auth_url", "exchange_code", "refresh_auth",
        "validate_post_payload", "create_upload", "publish_now", "get_post_status",
    ]
    for platform, provider in all_providers().items():
        for method in required:
            assert hasattr(provider, method), (
                f"{platform.value} provider is missing required attribute: {method}"
            )
