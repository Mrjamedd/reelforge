"""
YouTube Platform Provider
==========================
Uses the official YouTube Data API v3 with OAuth 2.0.
Docs: https://developers.google.com/youtube/v3/guides/uploading_a_video

STATUS: Scaffolded with correct official API structure.
TODO: Requires Google Cloud credentials.
      1. Create project at https://console.cloud.google.com
      2. Enable YouTube Data API v3
      3. Create OAuth 2.0 credentials (Web Application)
      4. Add redirect URI: {API_URL}/api/oauth/youtube/callback
      5. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env

YOUTUBE SHORTS:
  Videos are automatically classified as Shorts when:
    - Vertical aspect ratio (9:16)
    - 60 seconds or under
  No special API flag needed — the platform detects it automatically.

SCHEDULING:
  YouTube Data API v3 supports native scheduling via publishAt parameter.
  Video must be set to "private" and scheduledStartTime to schedule publishing.
"""

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
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
logger = get_logger("provider.youtube")

GOOGLE_AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


class YouTubeProvider(PlatformProvider):

    @property
    def platform_name(self) -> str:
        return "YouTube"

    @property
    def is_configured(self) -> bool:
        return bool(get_effective_cred("youtube_client_id") and get_effective_cred("youtube_client_secret"))

    @property
    def requires_app_review(self) -> bool:
        # YouTube requires OAuth app verification for production use
        # Test accounts work without verification
        return False  # No special review beyond Google OAuth app verification

    @property
    def supports_post_delete_test(self) -> bool:
        return True

    # ─── OAuth ────────────────────────────────────────────────────────────────

    def get_auth_url(self, redirect_uri: str, state: str) -> OAuthConfig:
        params = {
            "client_id": get_effective_cred("youtube_client_id"),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_SCOPES),
            "access_type": "offline",   # needed to get refresh_token
            "prompt": "consent",        # force consent to always get refresh_token
            "state": state,
        }
        auth_url = f"{GOOGLE_AUTH_BASE}?{urlencode(params)}"
        logger.info("youtube_auth_url_generated", state=state)
        return OAuthConfig(authorization_url=auth_url, state=state)

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": get_effective_cred("youtube_client_id"),
                    "client_secret": get_effective_cred("youtube_client_secret"),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            access_token = data["access_token"]

            # Fetch channel info
            channel_resp = await client.get(
                YOUTUBE_CHANNELS_URL,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            channel_resp.raise_for_status()
            channels = channel_resp.json().get("items", [])
            channel = channels[0] if channels else {}

        return OAuthTokens(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + data.get("expires_in", 3600),
                tz=timezone.utc,
            ),
            scopes=" ".join(REQUIRED_SCOPES),
            platform_user_id=channel.get("id", ""),
            platform_username=channel.get("snippet", {}).get("title"),
        )

    async def refresh_auth(self, account: PlatformAccount) -> OAuthTokens:
        refresh_token = decrypt_token(account.refresh_token_encrypted)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": get_effective_cred("youtube_client_id"),
                    "client_secret": get_effective_cred("youtube_client_secret"),
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=refresh_token,  # Google refresh tokens don't rotate
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + data.get("expires_in", 3600),
                tz=timezone.utc,
            ),
            scopes=account.scopes,
            platform_user_id=account.platform_user_id,
            platform_username=account.platform_username,
        )

    # ─── Validation ───────────────────────────────────────────────────────────

    def validate_post_payload(self, payload: PublishPayload) -> list[str]:
        errors = []
        if payload.title and len(payload.title) > 100:
            errors.append("YouTube title must be 100 characters or fewer.")
        if payload.caption and len(payload.caption) > 5000:
            errors.append("YouTube description must be 5000 characters or fewer.")
        return errors

    # ─── Publishing ───────────────────────────────────────────────────────────

    async def create_upload(self, account: PlatformAccount, video_path: str) -> str:
        """
        YouTube upload is handled inline in publish_now() because it needs the
        final metadata payload at session creation time.
        """
        return "INLINE"

    async def publish_now(
        self,
        account: PlatformAccount,
        upload_id: str,
        payload: PublishPayload,
    ) -> PublishResult:
        """
        Upload the video via YouTube's resumable upload flow and return the new video ID.
        """
        access_token = decrypt_token(account.access_token_encrypted)
        source = await self._load_video_source(payload)
        if not source:
            return PublishResult(
                success=False,
                error_message="Could not load the video file for YouTube upload.",
            )

        privacy_map = {
            "public": "public",
            "private": "private",
            "unlisted": "unlisted",
        }
        yt_privacy = privacy_map.get(payload.privacy, "private")

        description = payload.caption or ""
        if payload.hashtags:
            description += "\n\n" + " ".join(f"#{t}" for t in payload.hashtags)

        async with httpx.AsyncClient(timeout=300) as client:
            metadata = {
                "snippet": {
                    "title": (payload.title or source["filename"])[:100],
                    "description": description[:5000],
                    "categoryId": "22",
                },
                "status": {
                    "privacyStatus": yt_privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }
            if payload.hashtags:
                metadata["snippet"]["tags"] = payload.hashtags[:500]
            if payload.scheduled_for:
                metadata["status"]["privacyStatus"] = "private"
                metadata["status"]["publishAt"] = payload.scheduled_for.astimezone(
                    timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")

            init_resp = await client.post(
                YOUTUBE_UPLOAD_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": source["content_type"],
                    "X-Upload-Content-Length": str(source["content_length"]),
                },
                json=metadata,
            )
            init_resp.raise_for_status()

            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                return PublishResult(
                    success=False,
                    error_message="YouTube upload session did not return a resumable location.",
                )

            upload_resp = await client.put(
                upload_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": source["content_type"],
                    "Content-Length": str(source["content_length"]),
                },
                content=source["content"],
            )

        if upload_resp.status_code in (200, 201):
            data = upload_resp.json()
            video_id = data.get("id")
            if not video_id:
                return PublishResult(
                    success=False,
                    error_message="YouTube upload completed without returning a video ID.",
                    raw_response=data,
                )
            logger.info("youtube_published", video_id=video_id)
            return PublishResult(
                success=True,
                platform_post_id=video_id,
                platform_post_url=f"https://www.youtube.com/watch?v={video_id}",
                raw_response=data,
            )

        logger.error("youtube_publish_failed", status=upload_resp.status_code)
        return PublishResult(
            success=False,
            error_message=f"YouTube API error {upload_resp.status_code}: {upload_resp.text}",
        )

    async def schedule_publish(
        self,
        account: PlatformAccount,
        upload_id: str,
        payload: PublishPayload,
    ) -> PublishResult:
        return await self.publish_now(account, upload_id, payload)

    async def get_post_status(
        self,
        account: PlatformAccount,
        platform_post_id: str,
    ) -> PostStatus:
        access_token = decrypt_token(account.access_token_encrypted)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                YOUTUBE_VIDEOS_URL,
                params={
                    "part": "statistics,status",
                    "id": platform_post_id,
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        if not items:
            return PostStatus(platform_post_id=platform_post_id, is_live=False)

        item = items[0]
        stats = item.get("statistics", {})
        status = item.get("status", {})

        return PostStatus(
            platform_post_id=platform_post_id,
            is_live=status.get("privacyStatus") == "public",
            view_count=int(stats["viewCount"]) if "viewCount" in stats else None,
            like_count=int(stats["likeCount"]) if "likeCount" in stats else None,
            raw_response=data,
        )

    async def delete_post(
        self,
        account: PlatformAccount,
        platform_post_id: str,
    ) -> None:
        access_token = decrypt_token(account.access_token_encrypted)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                YOUTUBE_VIDEOS_URL,
                params={"id": platform_post_id},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 204:
                logger.info("youtube_deleted", video_id=platform_post_id)
                return
            resp.raise_for_status()

    async def _load_video_source(self, payload: PublishPayload) -> dict | None:
        local_path = Path(payload.local_video_path) if payload.local_video_path else None
        if local_path and local_path.is_file():
            content = local_path.read_bytes()
            return {
                "content": content,
                "content_length": len(content),
                "content_type": mimetypes.guess_type(local_path.name)[0] or "video/mp4",
                "filename": local_path.stem,
            }

        source_ref = payload.video_path.strip()
        if source_ref.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.get(source_ref)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "video/mp4").split(";", 1)[0]
                filename = Path(source_ref.split("?", 1)[0]).stem or "upload"
                return {
                    "content": resp.content,
                    "content_length": len(resp.content),
                    "content_type": content_type,
                    "filename": filename,
                }

        file_path = Path(source_ref)
        if file_path.is_file():
            content = file_path.read_bytes()
            return {
                "content": content,
                "content_length": len(content),
                "content_type": mimetypes.guess_type(file_path.name)[0] or "video/mp4",
                "filename": file_path.stem,
            }

        return None
