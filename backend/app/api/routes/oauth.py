import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import ffmpeg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_effective_cred, get_settings
from app.core.logging import get_logger
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.models import AdminUser, Platform, PlatformAccount
from app.providers.base import PublishPayload
from app.providers.registry import all_providers
from app.schemas.schemas import PlatformAccountOut, PlatformStatusOut
from app.services.oauth_service import OAuthService

router = APIRouter(prefix="/oauth", tags=["oauth"])
settings = get_settings()
logger = get_logger("api.oauth")
oauth_svc = OAuthService()

REQUIRED_CREDENTIALS: dict[Platform, tuple[str, ...]] = {
    Platform.YOUTUBE: ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
    Platform.INSTAGRAM: ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
    Platform.TIKTOK: ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
}

SETTINGS_CREDENTIAL_ATTRS: dict[str, str] = {
    "YOUTUBE_CLIENT_ID": "youtube_client_id",
    "YOUTUBE_CLIENT_SECRET": "youtube_client_secret",
    "INSTAGRAM_APP_ID": "instagram_app_id",
    "INSTAGRAM_APP_SECRET": "instagram_app_secret",
    "TIKTOK_CLIENT_KEY": "tiktok_client_key",
    "TIKTOK_CLIENT_SECRET": "tiktok_client_secret",
}

YOUTUBE_DELETE_SCOPES = {
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtubepartner",
}


def _missing_credentials(platform: Platform) -> list[str]:
    return [
        name
        for name in REQUIRED_CREDENTIALS.get(platform, ())
        if not get_effective_cred(SETTINGS_CREDENTIAL_ATTRS[name])
    ]


def _token_needs_refresh(account) -> bool:
    expires_at = account.token_expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    return remaining < 300


def _stored_credential_health(account) -> dict:
    if not account or not isinstance(account.extra_data, dict):
        return {}
    health = account.extra_data.get("credential_health")
    return health if isinstance(health, dict) else {}


async def _instagram_link_diagnostics(db: AsyncSession, account: PlatformAccount) -> dict | None:
    access_token = decrypt_token(account.access_token_encrypted)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://graph.facebook.com/v19.0/me/accounts",
                params={
                    "fields": "id,name,instagram_business_account{id,username}",
                    "access_token": access_token,
                },
            )
            resp.raise_for_status()
            pages = resp.json().get("data", [])
    except Exception as exc:  # noqa: BLE001
        credential_problem = oauth_svc.is_credential_failure(exc)
        return {
            "credential_status": "invalid" if credential_problem else "warning",
            "credential_detail": (
                oauth_svc.describe_credential_failure(exc)
                if credential_problem
                else f"Could not verify the linked Instagram Professional account right now: {exc}"
            ),
            "next_action": (
                "Reconnect Instagram and approve Page/Instagram permissions."
                if credential_problem
                else "Try again when Meta's API is reachable."
            ),
            "missing_credentials": [],
            "can_publish": False,
        }

    page_data = None
    instagram_account = None
    for page in pages:
        candidate = page.get("instagram_business_account")
        if isinstance(candidate, dict) and candidate.get("id"):
            page_data = page
            instagram_account = candidate
            break

    if not instagram_account:
        return {
            "credential_status": "invalid",
            "credential_detail": (
                "No Instagram Professional account linked to a Facebook Page was returned. "
                "Instagram publishing requires a Business or Creator account linked to a Facebook Page."
            ),
            "next_action": "Link the Instagram account to a Facebook Page, then reconnect Instagram.",
            "missing_credentials": [],
            "can_publish": False,
        }

    if account.platform_user_id != instagram_account["id"]:
        account.platform_user_id = instagram_account["id"]
        account.platform_username = instagram_account.get("username") or account.platform_username
        extra = dict(account.extra_data or {})
        extra.update(
            {
                "instagram_user_id": instagram_account["id"],
                "instagram_username": instagram_account.get("username"),
                "facebook_page_id": page_data.get("id") if page_data else None,
                "facebook_page_name": page_data.get("name") if page_data else None,
            }
        )
        account.extra_data = extra
        await db.commit()
        await db.refresh(account)

    return None


async def _credential_diagnostics(
    db: AsyncSession,
    platform: Platform,
    provider,
    account,
) -> dict:
    missing = _missing_credentials(platform)
    if missing or not provider.is_configured:
        return {
            "credential_status": "missing",
            "credential_detail": f"Missing credentials: {', '.join(missing or REQUIRED_CREDENTIALS.get(platform, ()))}.",
            "next_action": "Add the platform credentials in Settings, then save and refresh.",
            "missing_credentials": missing or list(REQUIRED_CREDENTIALS.get(platform, ())),
            "can_publish": False,
        }

    if account is None:
        return {
            "credential_status": "configured",
            "credential_detail": "App credentials are present. Connect an account to verify posting access.",
            "next_action": "Click Connect and approve ReelPush in the platform browser flow.",
            "missing_credentials": [],
            "can_publish": False,
        }

    needs_refresh = _token_needs_refresh(account)
    stored_health = _stored_credential_health(account)
    if stored_health.get("status") == "invalid" and not needs_refresh:
        return {
            "credential_status": "invalid",
            "credential_detail": stored_health.get("detail") or "Stored credential check failed.",
            "next_action": "Reconnect the platform account or correct the saved credentials.",
            "missing_credentials": [],
            "can_publish": False,
        }

    try:
        account = await oauth_svc.ensure_fresh_token(db, account)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        credential_problem = oauth_svc.is_credential_failure(exc)
        if credential_problem:
            try:
                await oauth_svc.mark_credential_health(
                    db,
                    account,
                    "invalid",
                    oauth_svc.describe_credential_failure(exc),
                    source="status_check",
                )
                await db.commit()
            except Exception:
                await db.rollback()
        return {
            "credential_status": "invalid" if credential_problem else "warning",
            "credential_detail": (
                f"Connected account could not be verified: {exc}"
                if credential_problem
                else f"Credential verification could not reach the platform right now: {exc}"
            ),
            "next_action": (
                "Reconnect the platform account or correct the saved credentials."
                if credential_problem
                else "Try again when the platform API is reachable."
            ),
            "missing_credentials": [],
            "can_publish": False,
        }

    if platform == Platform.INSTAGRAM and account is not None:
        instagram_issue = await _instagram_link_diagnostics(db, account)
        if instagram_issue:
            return instagram_issue

    stored_health = _stored_credential_health(account)
    if provider.requires_app_review:
        return {
            "credential_status": "warning",
            "credential_detail": (
                "Credentials and account token are valid, but this platform may still require app review "
                "or publishing approval."
            ),
            "next_action": "Attempt publishing only after the platform app is approved for posting.",
            "missing_credentials": [],
            "can_publish": True,
        }

    return {
        "credential_status": "verified",
        "credential_detail": stored_health.get("detail") or (
            "Credentials and connected account verified."
            if needs_refresh
            else "Credentials are present and the connected account token is current."
        ),
        "next_action": None,
        "missing_credentials": [],
        "can_publish": True,
    }


async def _run_platform_credential_test(
    platform: Platform,
    account: PlatformAccount,
) -> tuple[str, dict]:
    access_token = decrypt_token(account.access_token_encrypted)

    async with httpx.AsyncClient(timeout=30) as client:
        if platform == Platform.YOUTUBE:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if not items:
                raise ValueError("No YouTube channel was returned for the connected account.")
            channel = items[0]
            name = channel.get("snippet", {}).get("title") or account.platform_username or "channel"
            return (f"YouTube credentials verified for {name}.", data)

        if platform == Platform.INSTAGRAM:
            resp = await client.get(
                f"https://graph.facebook.com/v19.0/{account.platform_user_id}",
                params={"fields": "id,username", "access_token": access_token},
            )
            resp.raise_for_status()
            data = resp.json()
            name = data.get("username") or account.platform_username or "account"
            return (f"Instagram credentials verified for {name}.", data)

        if platform == Platform.TIKTOK:
            resp = await client.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            user = data.get("data", {}).get("user", {})
            name = user.get("display_name") or account.platform_username or "account"
            return (
                (
                    f"TikTok credentials verified for {name}. "
                    "Direct posting may still require TikTok publishing approval."
                ),
                data,
            )

    raise ValueError(f"Credential testing is not implemented for {platform.value}.")


def _generate_post_delete_test_video() -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
        output_path = handle.name

    try:
        (
            ffmpeg
            .input("color=c=black:s=1080x1920:r=30:d=1", f="lavfi")
            .output(
                output_path,
                format="mp4",
                vcodec="libx264",
                pix_fmt="yuv420p",
                r=30,
                movflags="+faststart",
                preset="ultrafast",
            )
            .overwrite_output()
            .run(quiet=True)
        )
    except ffmpeg.Error as exc:
        Path(output_path).unlink(missing_ok=True)
        raise RuntimeError("Could not generate the temporary credential test video.") from exc

    return output_path


def _has_youtube_delete_scope(account: PlatformAccount) -> bool:
    scopes = set((account.scopes or "").split())
    return bool(scopes & YOUTUBE_DELETE_SCOPES)


async def _run_platform_post_delete_test(
    platform: Platform,
    provider,
    account: PlatformAccount,
) -> tuple[str, str, dict]:
    if not provider.supports_post_delete_test:
        return (
            "unverified",
            (
                f"{provider.platform_name} cannot be post/delete tested safely because "
                "its official API does not expose deletion for completed posts."
            ),
            {},
        )

    if platform == Platform.YOUTUBE and not _has_youtube_delete_scope(account):
        raise ValueError(
            "Reconnect YouTube before running the post/delete test. The connected OAuth token "
            "does not include YouTube delete permission."
        )

    video_path = _generate_post_delete_test_video()
    post_id: str | None = None
    try:
        payload = PublishPayload(
            video_path=video_path,
            local_video_path=video_path,
            title="ReelPush Credential Test",
            caption=(
                "Temporary ReelPush credential verification video. "
                "This should be deleted automatically."
            ),
            hashtags=[],
            privacy="private",
            scheduled_for=None,
        )
        errors = provider.validate_post_payload(payload)
        if errors:
            raise ValueError("Validation failed: " + "; ".join(errors))

        upload_id = await provider.create_upload(account, payload.video_path, payload)
        result = await provider.publish_now(account, upload_id, payload)
        if not result.success or not result.platform_post_id:
            raise ValueError(result.error_message or "The platform did not return a test post ID.")

        post_id = result.platform_post_id
        await provider.delete_post(account, post_id)
        return (
            "verified",
            f"{provider.platform_name} verified: test video posted privately and deleted.",
            result.raw_response or {},
        )
    except Exception as exc:
        if post_id:
            raise RuntimeError(
                f"Test post {post_id} was uploaded but could not be deleted automatically. "
                f"Delete it manually before retrying. {exc}"
            ) from exc
        raise
    finally:
        Path(video_path).unlink(missing_ok=True)


@router.get("/status", response_model=list[PlatformStatusOut])
async def platform_statuses(
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    """Return connection status for all platforms."""
    from sqlalchemy import select
    from app.models.models import PlatformAccount

    result = await db.execute(
        select(PlatformAccount).where(PlatformAccount.owner_id == current_user.id)
    )
    accounts = {a.platform: a for a in result.scalars().all()}
    providers = all_providers()

    statuses = []
    for platform, provider in providers.items():
        account = accounts.get(platform)
        diagnostics = await _credential_diagnostics(db, platform, provider, account)
        if account is not None:
            await db.refresh(account)
        statuses.append(
            PlatformStatusOut(
                platform=platform,
                connected=account is not None,
                account=PlatformAccountOut.model_validate(account) if account else None,
                configured=provider.is_configured,
                pending_approval=provider.requires_app_review,
                **diagnostics,
            )
        )
    return statuses


@router.get("/{platform}/connect")
async def connect_platform(
    platform: Platform,
    current_user: AdminUser = Depends(get_current_user),
):
    """Redirect admin to the platform's OAuth authorization page."""
    try:
        auth_url = oauth_svc.generate_auth_url(platform, current_user.id)
        return RedirectResponse(url=auth_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{platform}/connect-url")
async def connect_platform_url(
    platform: Platform,
    current_user: AdminUser = Depends(get_current_user),
):
    """Return the provider authorization URL for desktop clients."""
    try:
        auth_url = oauth_svc.generate_auth_url(platform, current_user.id)
        return {
            "authorization_url": auth_url,
            "redirect_uri": oauth_svc.redirect_uri(platform),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{platform}/callback")
async def oauth_callback(
    platform: Platform,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    OAuth callback endpoint. The platform redirects here after the user authorizes.
    Exchanges the code for tokens, stores them, then shows a local completion page.
    """
    try:
        account = await oauth_svc.handle_callback(db, platform, code, state)
        return RedirectResponse(
            url=f"{settings.api_url}/api/oauth/complete?connected={platform.value}"
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"{settings.api_url}/api/oauth/complete?oauth_error={quote(str(e))}"
        )
    except Exception as e:
        logger.exception(
            "oauth_callback_unexpected_error",
            platform=platform.value,
            error=str(e),
        )
        detail = (
            oauth_svc.describe_credential_failure(e)
            if oauth_svc.is_credential_failure(e)
            else "unexpected_error"
        )
        return RedirectResponse(
            url=f"{settings.api_url}/api/oauth/complete?oauth_error={quote(detail)}"
        )


@router.delete("/{platform}/disconnect")
async def disconnect_platform(
    platform: Platform,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    """Remove a connected platform account."""
    await oauth_svc.disconnect_account(db, platform, current_user.id)
    return {"detail": f"{platform.value} account disconnected."}


@router.post("/{platform}/test")
async def test_platform_credentials(
    platform: Platform,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    from sqlalchemy import select

    from app.providers.registry import get_provider

    provider = get_provider(platform)
    missing = _missing_credentials(platform)
    if missing or not provider.is_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                "Platform credentials are not configured. Missing: "
                + ", ".join(missing or REQUIRED_CREDENTIALS.get(platform, ()))
            ),
        )

    result = await db.execute(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.owner_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=400, detail="Connect the platform account before testing.")

    try:
        account = await oauth_svc.ensure_fresh_token(db, account)
        message, raw = await _run_platform_credential_test(platform, account)
        await oauth_svc.mark_credential_health(
            db,
            account,
            "verified" if not provider.requires_app_review else "warning",
            message,
            source="manual_test",
        )
        await db.commit()
        return {
            "success": True,
            "platform": platform.value,
            "message": message,
            "requires_app_review": provider.requires_app_review,
            "raw_response": raw,
        }
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        detail = (
            oauth_svc.describe_credential_failure(exc)
            if oauth_svc.is_credential_failure(exc)
            else str(exc)
        )
        try:
            await oauth_svc.mark_credential_health(
                db,
                account,
                "invalid" if oauth_svc.is_credential_failure(exc) else "warning",
                detail,
                source="manual_test",
            )
            await db.commit()
        except Exception:
            await db.rollback()
        raise HTTPException(status_code=400, detail=detail) from exc


@router.post("/{platform}/post-delete-test")
async def post_delete_test_platform_credentials(
    platform: Platform,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    from sqlalchemy import select

    from app.providers.registry import get_provider

    provider = get_provider(platform)
    missing = _missing_credentials(platform)
    if missing or not provider.is_configured:
        return {
            "success": False,
            "status": "unverified",
            "platform": platform.value,
            "message": "Unverified: missing credentials: "
            + ", ".join(missing or REQUIRED_CREDENTIALS.get(platform, ())),
            "skipped": True,
        }

    if not provider.supports_post_delete_test:
        return {
            "success": False,
            "status": "unverified",
            "platform": platform.value,
            "message": (
                f"Unverified: {provider.platform_name} cannot be post/delete tested safely "
                "because its official API does not expose deletion for completed posts."
            ),
            "skipped": True,
        }

    result = await db.execute(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.owner_id == current_user.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=400,
            detail="Connect the platform account before running the post/delete test.",
        )

    try:
        account = await oauth_svc.ensure_fresh_token(db, account)
        status, message, raw = await _run_platform_post_delete_test(platform, provider, account)
        if status == "verified":
            await oauth_svc.mark_credential_health(
                db,
                account,
                "verified",
                message,
                source="post_delete_test",
            )
            await db.commit()
        return {
            "success": status == "verified",
            "status": status,
            "platform": platform.value,
            "message": message,
            "requires_app_review": provider.requires_app_review,
            "raw_response": raw,
        }
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        failure = exc.__cause__ or exc
        is_auth_failure = oauth_svc.is_credential_failure(failure)
        detail = (
            oauth_svc.describe_credential_failure(failure)
            if is_auth_failure
            else str(exc)
        )
        try:
            await oauth_svc.mark_credential_health(
                db,
                account,
                "invalid" if is_auth_failure else "warning",
                detail,
                source="post_delete_test",
            )
            await db.commit()
        except Exception:
            await db.rollback()
        raise HTTPException(status_code=400, detail=detail) from exc


@router.get("/complete", response_class=HTMLResponse)
async def oauth_complete(
    connected: str | None = None,
    oauth_error: str | None = None,
):
    if oauth_error:
        return HTMLResponse(
            f"""
            <html>
              <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#f3f4f6;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
                <div style="max-width:520px;padding:32px;border:1px solid #2a2f3a;border-radius:18px;background:#161a22;">
                  <h1 style="margin:0 0 12px;font-size:28px;">Connection failed</h1>
                  <p style="margin:0;color:#c3cad5;line-height:1.6;">{oauth_error}</p>
                  <p style="margin:18px 0 0;color:#9aa3b2;">You can close this window and return to ReelPush.</p>
                </div>
              </body>
            </html>
            """,
            status_code=400,
        )

    platform = connected or "account"
    return HTMLResponse(
        f"""
        <html>
          <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#f3f4f6;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
            <div style="max-width:520px;padding:32px;border:1px solid #2a2f3a;border-radius:18px;background:#161a22;">
              <h1 style="margin:0 0 12px;font-size:28px;">{platform.title()} connected</h1>
              <p style="margin:0;color:#c3cad5;line-height:1.6;">Your account is now linked to ReelPush Desktop.</p>
              <p style="margin:18px 0 0;color:#9aa3b2;">You can close this window and return to the app.</p>
            </div>
          </body>
        </html>
        """
    )
