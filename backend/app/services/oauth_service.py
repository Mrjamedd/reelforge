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
from datetime import datetime, timezone

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
_pending_states: dict[str, str] = {}  # state -> platform


class OAuthService:

    def generate_auth_url(self, platform: Platform) -> str:
        """Generate the OAuth authorization URL for the given platform."""
        provider = get_provider(platform)

        if not provider.is_configured:
            raise ValueError(
                f"{provider.platform_name} credentials are not configured. "
                f"Set the required environment variables."
            )

        state = secrets.token_urlsafe(32)
        redirect_uri = self._redirect_uri(platform)

        _pending_states[state] = platform.value
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
        expected_platform = _pending_states.pop(state, None)
        if not expected_platform or expected_platform != platform.value:
            raise ValueError("Invalid or expired OAuth state. Please try connecting again.")

        provider = get_provider(platform)
        redirect_uri = self._redirect_uri(platform)

        tokens: OAuthTokens = await provider.exchange_code(code, redirect_uri)
        logger.info(
            "oauth_tokens_received",
            platform=platform.value,
            user_id=tokens.platform_user_id,
        )

        account = await self._upsert_account(db, platform, tokens)
        return account

    async def refresh_account_token(
        self, db: AsyncSession, account: PlatformAccount
    ) -> PlatformAccount:
        """Refresh the access token for an account if it has expired or is close to expiry."""
        if account.refresh_token_encrypted is None:
            raise ValueError(
                f"{account.platform.value} account has no refresh token stored. "
                "User must reconnect."
            )

        provider = get_provider(account.platform)
        tokens = await provider.refresh_auth(account)
        return await self._upsert_account(db, account.platform, tokens, existing=account)

    async def disconnect_account(self, db: AsyncSession, platform: Platform) -> None:
        """Remove the stored platform account (revoke local tokens)."""
        result = await db.execute(
            select(PlatformAccount).where(PlatformAccount.platform == platform)
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

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _redirect_uri(self, platform: Platform) -> str:
        return f"{settings.api_url}/api/oauth/{platform.value}/callback"

    async def _upsert_account(
        self,
        db: AsyncSession,
        platform: Platform,
        tokens: OAuthTokens,
        existing: PlatformAccount | None = None,
    ) -> PlatformAccount:
        if existing is None:
            result = await db.execute(
                select(PlatformAccount).where(PlatformAccount.platform == platform)
            )
            existing = result.scalar_one_or_none()

        if existing:
            account = existing
        else:
            account = PlatformAccount(platform=platform)
            db.add(account)

        account.platform_user_id = tokens.platform_user_id
        account.platform_username = tokens.platform_username
        account.access_token_encrypted = encrypt_token(tokens.access_token)
        account.refresh_token_encrypted = (
            encrypt_token(tokens.refresh_token) if tokens.refresh_token else None
        )
        account.token_expires_at = tokens.expires_at
        account.scopes = tokens.scopes
        account.extra_data = tokens.extra_data

        await db.commit()
        await db.refresh(account)
        logger.info("platform_account_saved", platform=platform.value, account_id=str(account.id))
        return account
