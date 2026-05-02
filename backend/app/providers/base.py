"""
Platform Provider Interface
===========================
Every supported platform must implement this abstract base class.
This enforces a consistent contract across TikTok, Instagram, YouTube, and
any future platforms, making the system extensible without touching core logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.models import PlatformAccount


@dataclass
class OAuthConfig:
    """OAuth authorization URL + state for CSRF protection."""
    authorization_url: str
    state: str


@dataclass
class OAuthTokens:
    """Normalized token set returned after OAuth code exchange."""
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: str | None
    platform_user_id: str
    platform_username: str | None
    extra_data: dict[str, Any] | None = None


@dataclass
class PublishPayload:
    """Normalized publish request passed to each provider."""
    video_path: str          # public URL or provider-specific source reference
    title: str | None
    caption: str | None
    hashtags: list[str]
    privacy: str             # normalized: "public" | "private" | "unlisted" | "friends"
    scheduled_for: datetime | None = None
    local_video_path: str | None = None


@dataclass
class PublishResult:
    """Result returned by a provider after a publish attempt."""
    success: bool
    platform_post_id: str | None = None
    platform_post_url: str | None = None
    requires_manual_completion: bool = False  # e.g. TikTok creator mode
    error_message: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass
class PostStatus:
    """Current status of a post as reported by the platform."""
    platform_post_id: str
    is_live: bool
    view_count: int | None = None
    like_count: int | None = None
    raw_response: dict[str, Any] | None = None


class PlatformProvider(ABC):
    """
    Abstract base class that all platform adapters must implement.

    Subclasses should be stateless — all account-specific state is
    passed in via the `account` parameter where needed.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform name."""
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True if the required env credentials (client ID/secret) are set."""
        ...

    @property
    @abstractmethod
    def requires_app_review(self) -> bool:
        """True if this provider needs platform app review before use."""
        ...

    # ─── OAuth ────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_auth_url(self, redirect_uri: str, state: str) -> OAuthConfig:
        """
        Build the OAuth authorization URL to redirect the user to.
        Must include required scopes for video publishing.
        """
        ...

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        """Exchange an authorization code for access + refresh tokens."""
        ...

    @abstractmethod
    async def refresh_auth(self, account: PlatformAccount) -> OAuthTokens:
        """
        Refresh the access token using the stored refresh token.
        Raises an exception if the refresh fails (user must reconnect).
        """
        ...

    # ─── Pre-publish validation ───────────────────────────────────────────────

    @abstractmethod
    def validate_post_payload(self, payload: PublishPayload) -> list[str]:
        """
        Validate the payload against platform-specific rules.
        Returns a list of validation error strings (empty = valid).
        """
        ...

    # ─── Publishing ───────────────────────────────────────────────────────────

    @abstractmethod
    async def create_upload(self, account: PlatformAccount, video_path: str) -> str:
        """
        Initiate a video upload to the platform.
        Returns an upload session ID / resource URL used by publish_now().
        Some platforms combine upload + publish in one step — return a sentinel
        string like "INLINE" and handle everything in publish_now().
        """
        ...

    @abstractmethod
    async def publish_now(
        self,
        account: PlatformAccount,
        upload_id: str,
        payload: PublishPayload,
    ) -> PublishResult:
        """
        Publish the video immediately.
        `upload_id` is the value returned by create_upload().
        """
        ...

    async def schedule_publish(
        self,
        account: PlatformAccount,
        upload_id: str,
        payload: PublishPayload,
    ) -> PublishResult:
        """
        Schedule the video for a future publish time.
        Default implementation raises NotImplementedError — platforms that don't
        support native scheduling will have this emulated by our Celery scheduler.
        """
        raise NotImplementedError(
            f"{self.platform_name} does not support native scheduling. "
            "Use the ReelPush scheduler instead."
        )

    @abstractmethod
    async def get_post_status(
        self,
        account: PlatformAccount,
        platform_post_id: str,
    ) -> PostStatus:
        """Poll the platform for the current status of a published post."""
        ...

    @property
    def supports_post_delete_test(self) -> bool:
        """True when a provider can safely delete a test post through its API."""
        return False

    async def delete_post(
        self,
        account: PlatformAccount,
        platform_post_id: str,
    ) -> None:
        """Delete a platform post. Providers opt in when the official API supports it."""
        raise NotImplementedError(
            f"{self.platform_name} does not support API post deletion."
        )
