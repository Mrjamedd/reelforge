from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import AdminUser, PublishJob, PublishProfile, StagedPublish, Upload
from app.schemas.schemas import (
    PublishProfileOut,
    PublishProfileUpdate,
    PublishJobOut,
    StagedPublishOut,
    StagedPublishUpdate,
    UploadOut,
    WorkspacePublishRequest,
)
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspace", tags=["workspace"])
workspace_svc = WorkspaceService()


def _serialize_staged(staged: StagedPublish, upload: Upload | None) -> StagedPublishOut:
    return StagedPublishOut(
        upload=UploadOut.model_validate(upload) if upload else None,
        selected_platforms=staged.selected_platforms or [],
        updated_at=staged.updated_at,
    )


@router.get("/profile", response_model=PublishProfileOut)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    return await workspace_svc.get_or_create_profile(db, current_user)


@router.put("/profile", response_model=PublishProfileOut)
async def update_profile(
    body: PublishProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    profile = await workspace_svc.get_or_create_profile(db, current_user)
    profile.default_title = body.default_title
    profile.default_caption = body.default_caption
    profile.default_hashtags = body.default_hashtags
    profile.default_privacy = body.default_privacy
    await db.commit()
    await db.refresh(profile)
    return profile


@router.get("/staged", response_model=StagedPublishOut)
async def get_staged_publish(
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    staged = await workspace_svc.get_or_create_staged(db, current_user)
    upload = None
    if staged.upload_id:
        upload = await db.get(Upload, staged.upload_id)
        if upload and upload.uploaded_by_id != current_user.id:
            upload = None
    return _serialize_staged(staged, upload)


@router.put("/staged", response_model=StagedPublishOut)
async def update_staged_publish(
    body: StagedPublishUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    staged = await workspace_svc.get_or_create_staged(db, current_user)
    upload = None

    if body.upload_id:
        upload = await db.get(Upload, body.upload_id)
        if not upload or upload.uploaded_by_id != current_user.id:
            raise HTTPException(status_code=404, detail="Upload not found.")

    staged.upload_id = body.upload_id
    staged.selected_platforms = [platform.value for platform in body.selected_platforms]
    await db.commit()
    await db.refresh(staged)
    return _serialize_staged(staged, upload)


@router.delete("/staged", response_model=StagedPublishOut)
async def clear_staged_publish(
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    staged = await workspace_svc.get_or_create_staged(db, current_user)
    staged.upload_id = None
    staged.selected_platforms = []
    await db.commit()
    await db.refresh(staged)
    return _serialize_staged(staged, None)


@router.post("/publish", response_model=list[PublishJobOut])
async def publish_staged_workspace(
    current_user: AdminUser = Depends(get_current_user),
):
    try:
        jobs = await workspace_svc.publish_staged(current_user.id)
        return [PublishJobOut.model_validate(job) for job in jobs]
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/publish-now", response_model=list[PublishJobOut])
async def publish_now_workspace(
    body: WorkspacePublishRequest,
    current_user: AdminUser = Depends(get_current_user),
):
    try:
        jobs = await workspace_svc.publish_now(current_user.id, body)
        return [PublishJobOut.model_validate(job) for job in jobs]
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
