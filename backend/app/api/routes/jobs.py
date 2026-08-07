import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import AdminUser, AuditLog, PublishJob, Upload
from app.schemas.schemas import (
    AuditLogOut,
    BulkPublishRequest,
    DashboardStats,
    PublishJobCreate,
    PublishJobOut,
)
from app.services.publish_service import PublishService

router = APIRouter(prefix="/jobs", tags=["jobs"])
publish_svc = PublishService()


async def _get_user_job(
    db: AsyncSession, job_id: uuid.UUID, current_user: AdminUser
) -> PublishJob | None:
    result = await db.execute(
        select(PublishJob)
        .join(PublishJob.upload)
        .where(
            PublishJob.id == job_id,
            Upload.uploaded_by_id == current_user.id,
        )
    )
    return result.scalar_one_or_none()


@router.post("/", response_model=PublishJobOut, status_code=201)
async def create_job(
    body: PublishJobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    """Create and enqueue a single publish job."""
    try:
        job = await publish_svc.create_job(db, body, current_user.id)
        return job
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bulk", response_model=list[PublishJobOut], status_code=201)
async def create_bulk_jobs(
    body: BulkPublishRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    """Create multiple publish jobs (one per platform) for a single upload."""
    created = []
    errors = []
    for job_in in body.jobs:
        job_in.upload_id = body.upload_id
        try:
            job = await publish_svc.create_job(db, job_in, current_user.id)
            created.append(job)
        except ValueError as e:
            errors.append({"platform": job_in.platform, "error": str(e)})

    if errors and not created:
        raise HTTPException(status_code=400, detail=errors)

    return created


@router.get("/", response_model=list[PublishJobOut])
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    result = await db.execute(
        select(PublishJob)
        .join(PublishJob.upload)
        .where(Upload.uploaded_by_id == current_user.id)
        .order_by(PublishJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    upload_count = (
        await db.execute(
            select(func.count()).select_from(Upload).where(Upload.uploaded_by_id == current_user.id)
        )
    ).scalar()
    job_count = (
        await db.execute(
            select(func.count())
            .select_from(PublishJob)
            .join(PublishJob.upload)
            .where(Upload.uploaded_by_id == current_user.id)
        )
    ).scalar()

    # Jobs grouped by status
    status_rows = (
        await db.execute(
            select(PublishJob.status, func.count().label("cnt"))
            .join(PublishJob.upload)
            .where(Upload.uploaded_by_id == current_user.id)
            .group_by(PublishJob.status)
        )
    ).all()
    jobs_by_status = {row.status.value: row.cnt for row in status_rows}

    # Recent 10 jobs
    recent = (
        await db.execute(
            select(PublishJob)
            .join(PublishJob.upload)
            .where(Upload.uploaded_by_id == current_user.id)
            .order_by(PublishJob.updated_at.desc())
            .limit(10)
        )
    ).scalars().all()

    return DashboardStats(
        total_uploads=upload_count or 0,
        total_jobs=job_count or 0,
        jobs_by_status=jobs_by_status,
        recent_jobs=[PublishJobOut.model_validate(j) for j in recent],
    )


@router.get("/{job_id}", response_model=PublishJobOut)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    job = await _get_user_job(db, uuid.UUID(job_id), current_user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/{job_id}/retry", response_model=PublishJobOut)
async def retry_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    existing = await _get_user_job(db, uuid.UUID(job_id), current_user)
    if not existing:
        raise HTTPException(status_code=404, detail="Job not found.")
    try:
        job = await publish_svc.retry_job(db, uuid.UUID(job_id))
        return job
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{job_id}/cancel", response_model=PublishJobOut)
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    existing = await _get_user_job(db, uuid.UUID(job_id), current_user)
    if not existing:
        raise HTTPException(status_code=404, detail="Job not found.")
    try:
        job = await publish_svc.cancel_job(db, uuid.UUID(job_id))
        return job
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{job_id}/audit", response_model=list[AuditLogOut])
async def get_job_audit_log(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    existing = await _get_user_job(db, uuid.UUID(job_id), current_user)
    if not existing:
        raise HTTPException(status_code=404, detail="Job not found.")
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.publish_job_id == uuid.UUID(job_id))
        .order_by(AuditLog.created_at.asc())
    )
    return result.scalars().all()
