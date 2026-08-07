"""
Instagram Platform Provider
============================
Uses the official Meta Instagram Content Publishing API (Meta Graph API;
version configurable via the META_GRAPH_API_VERSION env var).
Docs: https://developers.facebook.com/docs/instagram-api/guides/content-publishing

STATUS: Scaffolded with the correct official API structure.
TODO: Requires Meta app review before executing real API calls.
      Apply at https://developers.facebook.com
      Required permissions:
        - instagram_basic
        - instagram_content_publish
        - pages_read_engagement
        - pages_show_list

REQUIREMENTS:
  - Instagram account must be a Professional account (Business or Creator)
  - Must be linked to a Facebook Page
  - App must be Live (not Development) for external users

REELS PUBLISHING FLOW:
  1. Upload video to Instagram as a container (returns container_id)
  2. Poll container status until FINISHED
  3. Publish container to create the Reel
"""

import asyncio
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
logger = get_logger("provider.instagram")

GRAPH_API_VERSION = settings.meta_graph_api_version
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
META_AUTH_BASE = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"
META_TOKEN_URL = f"{GRAPH_API_BASE}/oauth/access_token"
INSTAGRAM_PAGE_LOOKUP_FIELDS = "id,name,instagram_business_account{id}"
INSTAGRAM_ACCOUNT_VERIFY_FIELDS = "id"

REQUIRED_SCOPES = (
    "instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list"
)


class InstagramProvider(PlatformProvider):

    @property
    def platform_name(self) -> str:
        return "Instagram"

    @property
    def is_configured(self) -> bool:
        return bool(get_effective_cred("instagram_app_id") and get_effective_cred("instagram_app_secret"))

    @property
    def requires_app_review(self) -> bool:
        # TODO: Meta app review is required for instagram_content_publish
        return True

    # ─── OAuth ────────────────────────────────────────────────────────────────

    def get_auth_url(self, redirect_uri: str, state: str) -> OAuthConfig:
        params = {
            "client_id": get_effective_cred("instagram_app_id"),
            "redirect_uri": redirect_uri,
            "scope": REQUIRED_SCOPES,
            "response_type": "code",
            "state": state,
        }
        auth_url = f"{META_AUTH_BASE}?{urlencode(params)}"
        logger.info("instagram_auth_url_generated", state=state)
        return OAuthConfig(authorization_url=auth_url, state=state)

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        """
        Exchange short-lived code for long-lived token.
        Meta short-lived tokens expire in 1 hour; we exchange for a 60-day token.
        """
        async with httpx.AsyncClient() as client:
            # Step 1: short-lived token
            resp = await client.get(
                META_TOKEN_URL,
                params={
                    "client_id": get_effective_cred("instagram_app_id"),
                    "client_secret": get_effective_cred("instagram_app_secret"),
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
            resp.raise_for_status()
            short_lived = resp.json()
            short_token = short_lived["access_token"]

            # Step 2: long-lived token (60 days)
            ll_resp = await client.get(
                f"{GRAPH_API_BASE}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": get_effective_cred("instagram_app_id"),
                    "client_secret": get_effective_cred("instagram_app_secret"),
                    "fb_exchange_token": short_token,
                },
            )
            ll_resp.raise_for_status()
            long_lived = ll_resp.json()
            access_token = long_lived["access_token"]

            user_resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={"fields": "id,name", "access_token": access_token},
            )
            user_resp.raise_for_status()
            facebook_user = user_resp.json()

            # Fetch the Instagram Professional account linked to one of the user's Pages.
            user_resp = await client.get(
                f"{GRAPH_API_BASE}/me/accounts",
                params={
                    "fields": INSTAGRAM_PAGE_LOOKUP_FIELDS,
                    "access_token": access_token,
                },
            )
            user_resp.raise_for_status()
            pages = user_resp.json().get("data", [])

        page_data = None
        instagram_account = None
        for page in pages:
            candidate = page.get("instagram_business_account")
            if isinstance(candidate, dict) and candidate.get("id"):
                page_data = page
                instagram_account = candidate
                break

        if not instagram_account:
            raise ValueError(
                "No Instagram Professional account linked to a Facebook Page was returned. "
                "Connect a Business or Creator Instagram account to a Facebook Page, then reconnect."
            )

        return OAuthTokens(
            access_token=access_token,
            refresh_token=None,  # Meta uses token refresh differently
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + long_lived.get("expires_in", 5184000),
                tz=timezone.utc,
            ),
            scopes=REQUIRED_SCOPES,
            platform_user_id=instagram_account["id"],
            platform_username=instagram_account.get("username") or page_data.get("name"),
            extra_data={
                "instagram_user_id": instagram_account["id"],
                "instagram_username": instagram_account.get("username"),
                "facebook_user_id": facebook_user.get("id"),
                "facebook_user_name": facebook_user.get("name"),
                "facebook_page_id": page_data.get("id"),
                "facebook_page_name": page_data.get("name"),
            },
        )

    async def refresh_auth(self, account: PlatformAccount) -> OAuthTokens:
        """
        Meta long-lived tokens can be refreshed before expiry.
        Re-exchange the existing long-lived token for a new one.
        """
        access_token = decrypt_token(account.access_token_encrypted)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": get_effective_cred("instagram_app_id"),
                    "client_secret": get_effective_cred("instagram_app_secret"),
                    "fb_exchange_token": access_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=None,
            expires_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + data.get("expires_in", 5184000),
                tz=timezone.utc,
            ),
            scopes=account.scopes,
            platform_user_id=account.platform_user_id,
            platform_username=account.platform_username,
        )

    # ─── Validation ───────────────────────────────────────────────────────────

    def validate_post_payload(self, payload: PublishPayload) -> list[str]:
        errors = []
        caption = self._caption_for_payload(payload, truncate=False)
        if caption and len(caption) > 2200:
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

    # ─── Publishing ───────────────────────────────────────────────────────────

    async def create_upload(
        self,
        account: PlatformAccount,
        video_path: str,
        payload: PublishPayload | None = None,
    ) -> str:
        """
        Step 1: Create an Instagram media container for a Reel.
        Returns the container_id.

        The video must be accessible via a public URL; we pass a presigned S3 URL
        or a temporarily served local URL.

        TODO: Requires instagram_content_publish permission (approved app).
        """
        access_token = decrypt_token(account.access_token_encrypted)
        caption = self._caption_for_payload(payload) if payload else ""

        async with httpx.AsyncClient() as client:
            ig_user_id = await self._resolve_instagram_user_id(client, account, access_token)
            data = {
                "media_type": "REELS",
                "video_url": video_path,
                "access_token": access_token,
            }
            if caption:
                data["caption"] = caption
            resp = await client.post(
                f"{GRAPH_API_BASE}/{ig_user_id}/media",
                data=data,
            )
            resp.raise_for_status()
            data = resp.json()

        container_id = data["id"]
        logger.info("instagram_container_created", container_id=container_id)
        return container_id

    async def publish_now(
        self,
        account: PlatformAccount,
        upload_id: str,
        payload: PublishPayload,
    ) -> PublishResult:
        """
        Step 2: Poll container status, then publish.
        `upload_id` here is the container_id from create_upload().
        """
        access_token = decrypt_token(account.access_token_encrypted)

        async with httpx.AsyncClient() as client:
            ig_user_id = await self._resolve_instagram_user_id(client, account, access_token)
            # Poll until the container is ready (status = FINISHED)
            for attempt in range(15):
                status_resp = await client.get(
                    f"{GRAPH_API_BASE}/{upload_id}",
                    params={"fields": "status_code", "access_token": access_token},
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()
                if status_data.get("status_code") == "FINISHED":
                    break
                if status_data.get("status_code") == "ERROR":
                    return PublishResult(
                        success=False,
                        error_message="Instagram media container processing failed.",
                        raw_response=status_data,
                    )
                await asyncio.sleep(5)
            else:
                return PublishResult(
                    success=False,
                    error_message="Instagram media container timed out waiting for FINISHED status.",
                )

            # Publish the container
            pub_resp = await client.post(
                f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
                data={
                    "creation_id": upload_id,
                    "access_token": access_token,
                },
            )
            pub_resp.raise_for_status()
            pub_data = pub_resp.json()

        post_id = pub_data["id"]
        logger.info("instagram_published", post_id=post_id)
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_post_url=f"https://www.instagram.com/p/{post_id}/",
            raw_response=pub_data,
        )

    @staticmethod
    def _caption_for_payload(payload: PublishPayload, *, truncate: bool = True) -> str:
        # Instagram Reels do not expose a separate title field in the publishing API.
        # Use the ReelPush title as a caption fallback so title-only posts still carry text.
        caption = (payload.caption or payload.title or "").strip()
        if payload.hashtags:
            caption = f"{caption} {' '.join(f'#{t}' for t in payload.hashtags)}".strip()
        return caption[:2200] if truncate else caption

    async def _resolve_instagram_user_id(
        self,
        client: httpx.AsyncClient,
        account: PlatformAccount,
        access_token: str,
    ) -> str:
        extra = account.extra_data if isinstance(account.extra_data, dict) else {}
        stored_instagram_id = extra.get("instagram_user_id") or account.platform_user_id
        if stored_instagram_id:
            return stored_instagram_id

        resp = await client.get(
            f"{GRAPH_API_BASE}/me/accounts",
            params={
                "fields": INSTAGRAM_PAGE_LOOKUP_FIELDS,
                "access_token": access_token,
            },
        )
        resp.raise_for_status()
        for page in resp.json().get("data", []):
            candidate = page.get("instagram_business_account")
            if isinstance(candidate, dict) and candidate.get("id"):
                return candidate["id"]
        raise ValueError(
            "No Instagram Professional account linked to a Facebook Page was returned. "
            "Connect a Business or Creator Instagram account to a Facebook Page, then reconnect Instagram."
        )

    async def get_post_status(
        self,
        account: PlatformAccount,
        platform_post_id: str,
    ) -> PostStatus:
        access_token = decrypt_token(account.access_token_encrypted)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/{platform_post_id}",
                params={
                    "fields": "id,media_type,permalink,like_count,comments_count",
                    "access_token": access_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return PostStatus(
            platform_post_id=platform_post_id,
            is_live=True,  # If we got it, it's live
            like_count=data.get("like_count"),
            raw_response=data,
        )
