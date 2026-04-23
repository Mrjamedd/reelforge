from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.models import AdminUser, Platform
from app.providers.registry import all_providers
from app.schemas.schemas import PlatformAccountOut, PlatformStatusOut
from app.services.oauth_service import OAuthService

router = APIRouter(prefix="/oauth", tags=["oauth"])
settings = get_settings()
oauth_svc = OAuthService()


@router.get("/status", response_model=list[PlatformStatusOut])
async def platform_statuses(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_user),
):
    """Return connection status for all platforms."""
    from sqlalchemy import select
    from app.models.models import PlatformAccount

    result = await db.execute(select(PlatformAccount))
    accounts = {a.platform: a for a in result.scalars().all()}
    providers = all_providers()

    statuses = []
    for platform, provider in providers.items():
        account = accounts.get(platform)
        statuses.append(
            PlatformStatusOut(
                platform=platform,
                connected=account is not None,
                account=PlatformAccountOut.model_validate(account) if account else None,
                configured=provider.is_configured,
                pending_approval=provider.requires_app_review,
            )
        )
    return statuses


@router.get("/{platform}/connect")
async def connect_platform(
    platform: Platform,
    _: AdminUser = Depends(get_current_user),
):
    """Redirect admin to the platform's OAuth authorization page."""
    try:
        auth_url = oauth_svc.generate_auth_url(platform)
        return RedirectResponse(url=auth_url)
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
    Exchanges the code for tokens, stores them, redirects back to the dashboard.
    """
    try:
        account = await oauth_svc.handle_callback(db, platform, code, state)
        return RedirectResponse(
            url=f"{settings.frontend_url}/dashboard?connected={platform.value}"
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"{settings.frontend_url}/dashboard?oauth_error={str(e)}"
        )
    except Exception as e:
        return RedirectResponse(
            url=f"{settings.frontend_url}/dashboard?oauth_error=unexpected_error"
        )


@router.delete("/{platform}/disconnect")
async def disconnect_platform(
    platform: Platform,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_user),
):
    """Remove a connected platform account."""
    await oauth_svc.disconnect_account(db, platform)
    return {"detail": f"{platform.value} account disconnected."}
