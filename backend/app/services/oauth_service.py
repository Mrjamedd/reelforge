"""
OAuth Service
=============
Manages the OAuth flow for all platforms:
  - Generates authorization URLs
  - Exchanges codes for tokens
  - Stores / updates encrypted tokens in the database
  - Handles token refresh
"""

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import encrypt_token
from app.models.models import Platform, PlatformAccount
from app.providers.base import OAuthTokens
from app.providers.registry import get_provider

settings = get_settings()
logger = get_logger("service.oauth")

# In-memory state store for CSRF protection (use Redis in production for multi-instance)
_pending_states: dict[str, tuple[str, str]] = {}  # state -> (platform, admin_user_id)

AUTH_FAILURE_TERMS = (
    "invalid_client",
    "invalid_grant",
    "invalid_token",
    "expired_token",
    "unauthorized",
    "authorization",
    "oauth",
    "token",
    "refresh",
    "client_secret",
    "permission",
    "scope",
    "insufficient",
)


class OAuthService:

    def generate_auth_url(self, platform: Platform, admin_user_id: uuid.UUID) -> str:
        """Generate the OAuth authorization URL for the given platform."""
        provider = get_provider(platform)

        if not provider.is_configured:
            raise ValueError(
                f"{provider.platform_name} credentials are not configured. "
                f"Set the required environment variables."
            )

        state = secrets.token_urlsafe(32)
        redirect_uri = self.redirect_uri(platform)

        _pending_states[state] = (platform.value, str(admin_user_id))
        oauth_config = provider.get_auth_url(redirect_uri, state)
        logger.info("oauth_url_generated", platform=platform.value, state=state[:8])
        return oauth_config.authorization_url

    async def handle_callback(
        self,
        db: AsyncSession,
        platform: Platform,
        code: str,
        state: str,
    ) -> PlatformAccount:
        """
        Handle the OAuth callback: validate state, exchange code, store tokens.
        Returns the created/updated PlatformAccount.
        """
        # CSRF check
        expected = _pending_states.pop(state, None)
        if not expected or expected[0] != platform.value:
            raise ValueError("Invalid or expired OAuth state. Please try connecting again.")
        admin_user_id = uuid.UUID(expected[1])

        provider = get_provider(platform)
        redirect_uri = self.redirect_uri(platform)

        tokens: OAuthTokens = await provider.exchange_code(code, redirect_uri)
        logger.info(
            "oauth_tokens_received",
            platform=platform.value,
            user_id=tokens.platform_user_id,
        )

        account = await self._upsert_account(db, platform, tokens, admin_user_id=admin_user_id)
        return account

    async def refresh_account_token(
        self, db: AsyncSession, account: PlatformAccount
    ) -> PlatformAccount:
        """Refresh the access token for an account if it has expired or is close to expiry."""
        if account.refresh_token_encrypted is None and account.platform != Platform.INSTAGRAM:
            await self.mark_credential_health(
                db,
                account,
                "invalid",
                f"{account.platform.value} account has no refresh token stored. User must reconnect.",
                source="token_refresh",
            )
            raise ValueError(
                f"{account.platform.value} account has no refresh token stored. "
                "User must reconnect."
            )

        provider = get_provider(account.platform)
        try:
            tokens = await provider.refresh_auth(account)
        except Exception as exc:
            if self.is_credential_failure(exc):
                await self.mark_credential_health(
                    db,
                    account,
                    "invalid",
                    self.describe_credential_failure(exc),
                    source="token_refresh",
                )
            raise
        return await self._upsert_account(
            db,
            account.platform,
            tokens,
            admin_user_id=account.owner_id,
            existing=account,
        )

    async def disconnect_account(
        self, db: AsyncSession, platform: Platform, admin_user_id: uuid.UUID
    ) -> None:
        """Remove the stored platform account (revoke local tokens)."""
        result = await db.execute(
            select(PlatformAccount).where(
                PlatformAccount.platform == platform,
                PlatformAccount.owner_id == admin_user_id,
            )
        )
        account = result.scalar_one_or_none()
        if account:
            await db.delete(account)
            await db.commit()
            logger.info("platform_account_disconnected", platform=platform.value)

    async def ensure_fresh_token(
        self, db: AsyncSession, account: PlatformAccount
    ) -> PlatformAccount:
        """Refresh token if expired or within 5 minutes of expiry."""
        if account.token_expires_at is None:
            return account  # no expiry tracked

        now = datetime.now(timezone.utc)
        expires_at = account.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        remaining = (expires_at - now).total_seconds()
        if remaining < 300:  # refresh if < 5 minutes left
            logger.info(
                "token_refresh_triggered",
                platform=account.platform.value,
                seconds_remaining=int(remaining),
            )
            account = await self.refresh_account_token(db, account)

        return account

    def is_credential_failure(self, exc_or_message: object, raw_response: Any | None = None) -> bool:
        """Best-effort classifier for OAuth/client-secret/token/scope failures."""
        status_code: int | None = None
        parts: list[str] = []

        if isinstance(exc_or_message, httpx.HTTPStatusError):
            status_code = exc_or_message.response.status_code
            parts.append(exc_or_message.response.text)
        else:
            status_code = getattr(getattr(exc_or_message, "response", None), "status_code", None)
            response_text = getattr(getattr(exc_or_message, "response", None), "text", None)
            if response_text:
                parts.append(str(response_text))
            parts.append(str(exc_or_message))

        if raw_response:
            parts.append(str(raw_response))

        haystack = " ".join(parts).lower()
        if status_code in {401, 403}:
            return True
        if status_code == 400 and any(term in haystack for term in AUTH_FAILURE_TERMS):
            return True
        return any(term in haystack for term in AUTH_FAILURE_TERMS)

    def describe_credential_failure(self, exc_or_message: object, raw_response: Any | None = None) -> str:
        status_code = getattr(getattr(exc_or_message, "response", None), "status_code", None)
        response_text = getattr(getattr(exc_or_message, "response", None), "text", None)
        message = response_text or str(exc_or_message)
        if raw_response:
            message = f"{message} {raw_response}"
        prefix = f"Platform auth failed with HTTP {status_code}" if status_code else "Platform auth failed"
        return f"{prefix}: {message[:700]}"

    async def mark_credential_health(
        self,
        db: AsyncSession,
        account: PlatformAccount,
        status: str,
        detail: str,
        *,
        source: str,
    ) -> None:
        extra = dict(account.extra_data or {})
        extra["credential_health"] = {
            "status": status,
            "detail": detail[:700],
            "source": source,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        account.extra_data = extra
        await db.flush()

    # ─── Internal ─────────────────────────────────────────────────────────────

    def redirect_uri(self, platform: Platform) -> str:
        return f"{settings.api_url}/api/oauth/{platform.value}/callback"

    async def _upsert_account(
        self,
        db: AsyncSession,
        platform: Platform,
        tokens: OAuthTokens,
        admin_user_id: uuid.UUID,
        existing: PlatformAccount | None = None,
    ) -> PlatformAccount:
        if existing is None:
            result = await db.execute(
                select(PlatformAccount).where(
                    PlatformAccount.platform == platform,
                    PlatformAccount.owner_id == admin_user_id,
                )
            )
            existing = result.scalar_one_or_none()

        if existing:
            account = existing
        else:
            account = PlatformAccount(platform=platform, owner_id=admin_user_id)
            db.add(account)

        account.platform_user_id = tokens.platform_user_id
        account.platform_username = tokens.platform_username
        account.access_token_encrypted = encrypt_token(tokens.access_token)
        account.refresh_token_encrypted = (
            encrypt_token(tokens.refresh_token) if tokens.refresh_token else None
        )
        account.token_expires_at = tokens.expires_at
        account.scopes = tokens.scopes
        extra_data = dict(tokens.extra_data or {})
        extra_data["credential_health"] = {
            "status": "verified",
            "detail": "OAuth token exchange or refresh completed successfully.",
            "source": "oauth",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        account.extra_data = extra_data

        await db.commit()
        await db.refresh(account)
        logger.info("platform_account_saved", platform=platform.value, account_id=str(account.id))
        return account
