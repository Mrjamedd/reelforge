"""
Basic tests for core components.
Run with: pytest backend/tests/ -v
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from app.core.security import hash_password, verify_password, encrypt_token, decrypt_token, create_access_token, decode_access_token
from app.providers.tiktok import TikTokProvider
from app.providers.instagram import InstagramProvider
from app.providers.youtube import YouTubeProvider
from app.providers.base import PublishPayload
from app.providers.registry import get_provider, all_providers
from app.models.models import Platform
from app.schemas.schemas import WorkspacePublishRequest
from app.services.media_service import MediaService, MediaValidationError
from app.services.oauth_service import OAuthService


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
        local_video_path=None,
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


@pytest.mark.asyncio
async def test_youtube_publish_sends_post_metadata(monkeypatch, tmp_path):
    from app.models.models import PlatformAccount
    from app.providers import youtube

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-video")
    captured = {}

    class FakeResponse:
        def __init__(self, status_code=200, json_data=None, headers=None):
            self.status_code = status_code
            self._json_data = json_data or {}
            self.headers = headers or {}
            self.text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return self._json_data

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            captured["metadata"] = kwargs["json"]
            return FakeResponse(headers={"Location": "https://upload.youtube.test/session"})

        async def put(self, *args, **kwargs):
            return FakeResponse(status_code=201, json_data={"id": "yt123"})

    monkeypatch.setattr(youtube.httpx, "AsyncClient", FakeClient)
    account = PlatformAccount(platform=Platform.YOUTUBE, access_token_encrypted=encrypt_token("access-token"))
    payload = PublishPayload(
        video_path=str(video_path),
        local_video_path=str(video_path),
        title="Exact title",
        caption="Exact caption",
        hashtags=["one", "two"],
        privacy="unlisted",
        scheduled_for=None,
    )

    result = await YouTubeProvider().publish_now(account, "INLINE", payload)

    assert result.success
    assert captured["metadata"]["snippet"]["title"] == "Exact title"
    assert captured["metadata"]["snippet"]["description"] == "Exact caption\n\n#one #two"
    assert captured["metadata"]["snippet"]["tags"] == ["one", "two"]
    assert captured["metadata"]["status"]["privacyStatus"] == "unlisted"


@pytest.mark.asyncio
async def test_instagram_exchange_code_uses_linked_instagram_account(monkeypatch):
    from app.providers import instagram

    account_field_requests = []

    class FakeResponse:
        def __init__(self, json_data):
            self._json_data = json_data

        def raise_for_status(self):
            return None

        def json(self):
            return self._json_data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None):
            params = params or {}
            if url.endswith("/oauth/access_token") and params.get("grant_type") == "fb_exchange_token":
                return FakeResponse({"access_token": "long-token", "expires_in": 5184000})
            if url.endswith("/oauth/access_token"):
                return FakeResponse({"access_token": "short-token"})
            if url.endswith("/me"):
                return FakeResponse({"id": "fb-user", "name": "Facebook User"})
            if url.endswith("/me/accounts"):
                account_field_requests.append(params.get("fields"))
                return FakeResponse(
                    {
                        "data": [
                            {
                                "id": "page-1",
                                "name": "Page One",
                                "instagram_business_account": {
                                    "id": "ig-123",
                                    "username": "ig_user",
                                },
                            }
                        ]
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeClient)

    tokens = await InstagramProvider().exchange_code("code", "https://app.test/callback")

    assert tokens.platform_user_id == "ig-123"
    assert tokens.platform_username == "ig_user"
    assert tokens.extra_data["instagram_user_id"] == "ig-123"
    assert tokens.extra_data["facebook_page_id"] == "page-1"
    assert account_field_requests == ["id,name,instagram_business_account{id}"]


def test_instagram_caption_uses_title_when_caption_is_empty():
    payload = _make_payload(
        video_path="https://media.example.com/uploads/clip.mp4",
        title="Launch title",
        caption="",
        hashtags=["one", "two"],
    )

    assert InstagramProvider()._caption_for_payload(payload) == "Launch title #one #two"


@pytest.mark.asyncio
async def test_instagram_create_upload_sends_caption(monkeypatch):
    from app.models.models import PlatformAccount
    from app.providers import instagram

    captured = {}

    class FakeResponse:
        def __init__(self, json_data):
            self._json_data = json_data

        def raise_for_status(self):
            return None

        def json(self):
            return self._json_data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "page-1",
                            "instagram_business_account": {
                                "id": "ig-123",
                                "username": "ig_user",
                            },
                        }
                    ]
                }
            )

        async def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse({"id": "container-123"})

    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeClient)
    account = PlatformAccount(
        platform=Platform.INSTAGRAM,
        platform_user_id="ig-123",
        access_token_encrypted=encrypt_token("access-token"),
    )
    payload = _make_payload(
        video_path="https://media.example.com/uploads/clip.mp4",
        caption="Exact caption",
        hashtags=["one", "two"],
    )

    upload_id = await InstagramProvider().create_upload(account, payload.video_path, payload)

    assert upload_id == "container-123"
    assert captured["url"].endswith("/ig-123/media")
    assert captured["data"]["video_url"] == payload.video_path
    assert captured["data"]["caption"] == "Exact caption #one #two"


@pytest.mark.asyncio
async def test_instagram_create_upload_requires_linked_professional_account(monkeypatch):
    from app.models.models import PlatformAccount
    from app.providers import instagram

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None):
            return FakeResponse()

        async def post(self, url, data=None):
            raise AssertionError("Instagram media upload should not start without a linked account.")

    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeClient)
    account = PlatformAccount(
        platform=Platform.INSTAGRAM,
        platform_user_id="fb-user",
        access_token_encrypted=encrypt_token("access-token"),
    )
    payload = _make_payload(video_path="https://media.example.com/uploads/clip.mp4")

    with pytest.raises(ValueError, match="Professional account linked"):
        await InstagramProvider().create_upload(account, payload.video_path, payload)


@pytest.mark.asyncio
async def test_oauth_refresh_allows_instagram_without_refresh_token(monkeypatch):
    import uuid
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from app.models.models import PlatformAccount
    from app.providers.base import OAuthTokens
    from app.services.oauth_service import OAuthService

    class FakeProvider:
        async def refresh_auth(self, account):
            return OAuthTokens(
                access_token="new-access-token",
                refresh_token=None,
                expires_at=datetime.now(timezone.utc),
                scopes=account.scopes,
                platform_user_id=account.platform_user_id,
                platform_username=account.platform_username,
            )

    monkeypatch.setattr("app.services.oauth_service.get_provider", lambda platform: FakeProvider())

    account = PlatformAccount(
        platform=Platform.INSTAGRAM,
        owner_id=uuid.uuid4(),
        platform_user_id="ig-123",
        platform_username="ig_user",
        access_token_encrypted=encrypt_token("old-access-token"),
        refresh_token_encrypted=None,
        scopes="instagram_basic",
    )
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    refreshed = await OAuthService().refresh_account_token(db, account)

    assert refreshed is account
    assert decrypt_token(account.access_token_encrypted) == "new-access-token"


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


def test_only_youtube_supports_safe_post_delete_test():
    providers = all_providers()
    assert providers[Platform.YOUTUBE].supports_post_delete_test
    assert not providers[Platform.INSTAGRAM].supports_post_delete_test
    assert not providers[Platform.TIKTOK].supports_post_delete_test


def test_oauth_service_classifies_credential_failures():
    svc = OAuthService()
    assert svc.is_credential_failure("invalid_client: bad client_secret")
    assert svc.is_credential_failure("HTTP 401 invalid_token")
    assert not svc.is_credential_failure("Video file could not be loaded from disk.")


def test_oauth_redirect_uri_uses_configured_api_url(monkeypatch):
    from app.services import oauth_service

    monkeypatch.setattr(oauth_service.settings, "api_url", "http://localhost:8100")
    assert OAuthService().redirect_uri(Platform.YOUTUBE) == "http://localhost:8100/api/oauth/youtube/callback"


def test_workspace_publish_request_keeps_post_details():
    request = WorkspacePublishRequest(
        upload_id="19788eea-a040-4644-bed9-27eb60f87c18",
        selected_platforms=["youtube"],
        default_title="Launch title",
        default_caption="Launch caption",
        default_hashtags="#one, two",
        default_privacy="private",
    )

    assert request.default_title == "Launch title"
    assert request.default_caption == "Launch caption"
    assert request.default_hashtags == "one,two"
    assert request.default_privacy.value == "private"
    assert request.selected_platforms == [Platform.YOUTUBE]


def test_media_service_warns_before_short_form_normalization():
    svc = MediaService()
    warnings = svc.short_form_compatibility_warnings(
        {
            "duration_seconds": 1.0,
            "width": 1920,
            "height": 1080,
            "frame_rate": 30,
            "video_codec": "h264",
            "audio_codec": "aac",
        },
        file_size_bytes=1024,
    )

    assert any("under 3 seconds" in warning for warning in warnings)
    assert any("landscape" in warning for warning in warnings)


def test_media_service_reports_missing_libmagic(monkeypatch):
    from app.services import media_service

    monkeypatch.setattr(media_service, "magic", None)

    with pytest.raises(MediaValidationError, match="libmagic"):
        MediaService().validate_video(b"not-a-real-video", "clip.mp4")


def test_publish_credentials_accept_runtime_saved_credentials(monkeypatch):
    from app.core import config
    from app.services import publish_service

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(youtube_client_id="", youtube_client_secret=""),
    )
    monkeypatch.setattr(
        config,
        "_runtime_creds",
        {
            "youtube_client_id": "saved-youtube-client-id",
            "youtube_client_secret": "saved-youtube-client-secret",
        },
    )

    assert publish_service._missing_credentials(Platform.YOUTUBE) == []


@pytest.mark.asyncio
async def test_publish_staged_workspace_returns_user_error(monkeypatch):
    from app.api.routes import workspace

    class FailingWorkspaceService:
        async def publish_staged(self, _user_id):
            raise ValueError("No connected youtube account. Connect an account before publishing.")

    current_user = type("User", (), {"id": "user-id"})()
    monkeypatch.setattr(workspace, "workspace_svc", FailingWorkspaceService())

    with pytest.raises(HTTPException) as exc_info:
        await workspace.publish_staged_workspace(current_user=current_user)

    assert exc_info.value.status_code == 400
    assert "No connected youtube account" in exc_info.value.detail
