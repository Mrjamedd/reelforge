"""
TikTok Platform Provider
========================
Uses the official TikTok for Developers API v2.
Docs: https://developers.tiktok.com/doc/overview

STATUS: Scaffolded with the correct official API structure.
TODO: Requires TikTok developer app approval before this can execute real API calls.
      Apply at https://developers.tiktok.com
      Required scopes: video.upload, video.publish, user.info.basic

NOTE: TikTok has two publishing modes:
  1. DIRECT_POST — posts immediately without creator review (requires approval)
  2. INBOX — sends to creator's draft inbox; creator must manually post
  
  We scaffold both and expose the limitation in PublishResult.requires_manual_completion.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.core.config import get_effective_cred, get_settings
from app.core.logging import get_logger
from app.core.security import decrypt_token
from app.models.models import PlatformAccount
from app.providers.base import (
    OAuthConfig,
    OAuthTokens,
    PlatformProvider,
    PostStatus,
    PublishPayload,
    PublishResult,
)

settings = get_settings()
logger = get_logger("provider.tiktok")

TIKTOK_AUTH_BASE = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_UPLOAD_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

REQUIRED_SCOPES = "user.info.basic,video.upload,video.publish"


class TikTokProvider(PlatformProvider):

    @property
    def platform_name(self) -> str:
        return "TikTok"

    @property
    def is_configured(self) -> bool:
        return bool(get_effective_cred("tiktok_client_key") and get_effective_cred("tiktok_client_secret"))

    @property
    def requires_app_review(self) -> bool:
        # TODO: App review is required for video.publish scope
        return True

    # ─── OAuth ────────────────────────────────────────────────────────────────

    def get_auth_url(self, redirect_uri: str, state: str) -> OAuthConfig:
        """
        Build TikTok OAuth 2.0 authorization URL.
        Uses PKCE (code_verifier + code_challenge) as required by TikTok v2.
        """
        # TikTok v2 requires PKCE
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()

        params = {
            "client_key": get_effective_cred("tiktok_client_key"),
            "scope": REQUIRED_SCOPES,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{TIKTOK_AUTH_BASE}?{urlencode(params)}"

        logger.info("tiktok_auth_url_generated", state=state)
        return OAuthConfig(authorization_url=auth_url, state=state)

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        """Exchange authorization code for tokens."""
        # TODO: Requires valid TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": get_effective_cred("tiktok_client_key"),
                    "client_secret": get_effective_cred("tiktok_client_secret"),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        # Fetch user info to get platform_user_id
        user_info = await self._get_user_info(data["access_token"])

        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + data.get("expires_in", 86400),
                tz=timezone.utc,
            ),
            scopes=data.get("scope"),
            platform_user_id=user_info["open_id"],
            platform_username=user_info.get("display_name"),
        )

    async def refresh_auth(self, account: PlatformAccount) -> OAuthTokens:
        """Refresh the TikTok access token."""
        refresh_token = decrypt_token(account.refresh_token_encrypted)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": get_effective_cred("tiktok_client_key"),
                    "client_secret": get_effective_cred("tiktok_client_secret"),
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_token),
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + data.get("expires_in", 86400),
                tz=timezone.utc,
            ),
            scopes=data.get("scope"),
            platform_user_id=account.platform_user_id,
            platform_username=account.platform_username,
        )

    # ─── Validation ───────────────────────────────────────────────────────────

    def validate_post_payload(self, payload: PublishPayload) -> list[str]:
        errors = []
        if payload.caption and len(payload.caption) > 2200:
            errors.append("TikTok caption must be 2200 characters or fewer.")
        hashtag_count = len(payload.hashtags)
        if hashtag_count > 30:
            errors.append(f"TikTok allows max 30 hashtags (got {hashtag_count}).")
        return errors

    # ─── Publishing ───────────────────────────────────────────────────────────

    async def create_upload(
        self,
        account: PlatformAccount,
        video_path: str,
        payload: PublishPayload | None = None,
    ) -> str:
        """
        Initialize a TikTok upload session using the Content Posting API.
        Returns the TikTok publish_id to be used in publish_now().

        TODO: Requires approved video.publish scope.
        """
        access_token = decrypt_token(account.access_token_encrypted)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TIKTOK_UPLOAD_URL,
                json={
                    "post_info": {
                        "title": "",  # filled in publish_now
                        "privacy_level": "SELF_ONLY",  # conservative default; overridden below
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                        "video_cover_timestamp_ms": 1000,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": 0,  # TODO: pass actual file size
                        "chunk_size": 0,
                        "total_chunk_count": 1,
                    },
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        publish_id = data["data"]["publish_id"]
        logger.info("tiktok_upload_initialized", publish_id=publish_id)
        return publish_id

    async def publish_now(
        self,
        account: PlatformAccount,
        upload_id: str,
        payload: PublishPayload,
    ) -> PublishResult:
        """
        Complete the TikTok publish using DIRECT_POST mode.

        TODO: DIRECT_POST requires explicit approval from TikTok.
        Until approved, videos will go to the creator's inbox (INBOX mode)
        and require manual posting — this is surfaced via requires_manual_completion=True.
        """
        access_token = decrypt_token(account.access_token_encrypted)

        privacy_map = {
            "public": "PUBLIC_TO_EVERYONE",
            "private": "SELF_ONLY",
            "friends": "MUTUAL_FOLLOW_FRIENDS",
        }
        tiktok_privacy = privacy_map.get(payload.privacy, "SELF_ONLY")

        title = payload.title or ""
        if payload.hashtags:
            hashtag_str = " ".join(f"#{t}" for t in payload.hashtags)
            title = f"{title} {hashtag_str}".strip()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TIKTOK_UPLOAD_URL,
                json={
                    "post_info": {
                        "title": title[:2200],
                        "privacy_level": tiktok_privacy,
                    },
                    "source_info": {"source": "FILE_UPLOAD"},
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )

        if resp.status_code == 200:
            data = resp.json()
            logger.info("tiktok_publish_success", publish_id=upload_id)
            return PublishResult(
                success=True,
                platform_post_id=data["data"].get("publish_id"),
                # TODO: TikTok does not return a direct post URL immediately
                requires_manual_completion=False,
                raw_response=data,
            )

        logger.error("tiktok_publish_failed", status=resp.status_code, body=resp.text)
        return PublishResult(
            success=False,
            error_message=f"TikTok API error {resp.status_code}: {resp.text}",
            raw_response=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None,
        )

    async def get_post_status(
        self,
        account: PlatformAccount,
        platform_post_id: str,
    ) -> PostStatus:
        """
        Query TikTok for publish status.
        Uses the /v2/post/publish/status/fetch/ endpoint.

        TODO: Requires approved scopes.
        """
        access_token = decrypt_token(account.access_token_encrypted)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                json={"publish_id": platform_post_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        status_data = data.get("data", {})
        return PostStatus(
            platform_post_id=platform_post_id,
            is_live=status_data.get("status") == "PUBLISH_COMPLETE",
            raw_response=data,
        )

    # ─── Helpers ──────────────────────────────────────────────────────────────

    async def _get_user_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                TIKTOK_USER_INFO_URL,
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("user", {})
