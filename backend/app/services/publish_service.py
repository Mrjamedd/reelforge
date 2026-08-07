"""
Publish Service
===============
Orchestrates the creation and dispatch of publish jobs.
Handles idempotency, scheduling, and state transitions.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.config import get_effective_cred
from app.models.models import (
    AuditLog,
    JobStatus,
    Platform,
    PlatformAccount,
    PublishJob,
    Upload,
)
from app.schemas.schemas import PublishJobCreate

logger = get_logger("service.publish")

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


def _missing_credentials(platform: Platform) -> list[str]:
    return [
        name
        for name in REQUIRED_CREDENTIALS.get(platform, ())
        if not get_effective_cred(SETTINGS_CREDENTIAL_ATTRS[name])
    ]


class PublishService:

    async def create_job(
        self,
        db: AsyncSession,
        job_in: PublishJobCreate,
        uploaded_by_id: uuid.UUID,
        *,
        enqueue: bool = True,
    ) -> PublishJob:
        """
        Create a PublishJob record and enqueue it for background processing.
        Prevents duplicate jobs via idempotency_key.
        """
        # Verify upload exists
        upload = await db.get(Upload, job_in.upload_id)
        if not upload:
            raise ValueError(f"Upload {job_in.upload_id} not found.")
        if upload.uploaded_by_id != uploaded_by_id:
            raise ValueError("Upload does not belong to the current user.")

        from app.providers.registry import get_provider
        from app.services.oauth_service import OAuthService

        provider = get_provider(job_in.platform)
        missing_credentials = _missing_credentials(job_in.platform)
        if missing_credentials or not provider.is_configured:
            raise ValueError(
                f"{provider.platform_name} credentials are not configured. "
                f"Missing: {', '.join(missing_credentials or REQUIRED_CREDENTIALS.get(job_in.platform, ()))}. "
                "Save credentials in Settings before publishing."
            )

        # Instagram requires a publicly accessible HTTPS URL - block early with a clear message.
        if job_in.platform == Platform.INSTAGRAM:
            from app.services.storage_providers import get_storage_provider

            storage_provider = get_storage_provider()
            if not storage_provider.is_publicly_accessible:
                storage_errors = storage_provider.configuration_errors
                raise ValueError(
                    "Instagram publishing requires a public HTTPS media URL. "
                    + " ".join(storage_errors)
                )

        # Verify a connected account exists for this platform
        account = await self._get_account(db, job_in.platform, uploaded_by_id)
        if not account:
            raise ValueError(
                f"No connected {provider.platform_name} account. "
                "Connect an account before publishing."
            )
        health = (account.extra_data or {}).get("credential_health") if isinstance(account.extra_data, dict) else None
        if isinstance(health, dict) and health.get("status") == "invalid":
            raise ValueError(
                f"{provider.platform_name} credentials are marked invalid. "
                "Reconnect the account or update the saved credentials before publishing."
            )
        oauth_svc = OAuthService()
        try:
            account = await oauth_svc.ensure_fresh_token(db, account)
        except Exception as exc:
            if oauth_svc.is_credential_failure(exc):
                await db.commit()
            else:
                await db.rollback()
            raise ValueError(
                f"{provider.platform_name} credentials could not be verified. "
                "Reconnect the account or update the saved credentials before publishing."
            ) from exc

        # Idempotency: prevent double-creating jobs for the same upload+platform
        # within the same scheduling window
        idempotency_key = self._make_idempotency_key(
            job_in.upload_id, job_in.platform, job_in.scheduled_for
        )

        existing = await db.execute(
            select(PublishJob).where(
                PublishJob.idempotency_key == idempotency_key,
                PublishJob.status.notin_([JobStatus.FAILED, JobStatus.CANCELLED]),
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(
                f"A publish job for {job_in.platform.value} with this upload already exists."
            )

        job = PublishJob(
            upload_id=job_in.upload_id,
            platform_account_id=account.id,
            platform=job_in.platform,
            status=JobStatus.SCHEDULED if job_in.scheduled_for else JobStatus.QUEUED,
            title=job_in.title,
            caption=job_in.caption,
            hashtags=job_in.hashtags,
            privacy=job_in.privacy,
            scheduled_for=job_in.scheduled_for,
            idempotency_key=idempotency_key,
        )
        db.add(job)
        await db.flush()  # get the job.id before committing

        await self._append_audit(
            db, job, None, job.status, "Job created."
        )
        await db.commit()
        await db.refresh(job)

        # Dispatch to Celery unless the caller plans to execute inline.
        if enqueue:
            await self._enqueue(job)
        logger.info("publish_job_created", job_id=str(job.id), platform=job.platform)
        return job

    async def retry_job(self, db: AsyncSession, job_id: uuid.UUID) -> PublishJob:
        """Reset a failed job back to QUEUED and re-enqueue it."""
        job = await db.get(PublishJob, job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found.")
        if job.status not in (JobStatus.FAILED, JobStatus.REQUIRES_MANUAL):
            raise ValueError(f"Job {job_id} is not in a retryable state (status={job.status}).")
        if job.attempt_count >= job.max_attempts:
            raise ValueError(
                f"Job {job_id} has exceeded max attempts ({job.max_attempts})."
            )

        old_status = job.status
        job.status = JobStatus.QUEUED
        await self._append_audit(db, job, old_status, JobStatus.QUEUED, "Manual retry requested.")
        await db.commit()
        await db.refresh(job)
        await self._enqueue(job)
        return job

    async def cancel_job(self, db: AsyncSession, job_id: uuid.UUID) -> PublishJob:
        """Cancel a queued or scheduled job."""
        job = await db.get(PublishJob, job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found.")
        if job.status not in (JobStatus.QUEUED, JobStatus.SCHEDULED):
            raise ValueError(f"Cannot cancel job in status: {job.status}")

        old_status = job.status
        job.status = JobStatus.CANCELLED
        if job.celery_task_id:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=True)

        await self._append_audit(db, job, old_status, JobStatus.CANCELLED, "Cancelled by admin.")
        await db.commit()
        await db.refresh(job)
        return job

    async def transition_status(
        self,
        db: AsyncSession,
        job: PublishJob,
        new_status: JobStatus,
        message: str | None = None,
        api_response: dict | None = None,
    ) -> None:
        """Update job status and append an audit log entry."""
        old_status = job.status
        job.status = new_status
        await self._append_audit(db, job, old_status, new_status, message, api_response)
        await db.flush()

    # ─── Internal Helpers ─────────────────────────────────────────────────────

    async def _get_account(
        self, db: AsyncSession, platform: Platform, owner_id: uuid.UUID
    ) -> PlatformAccount | None:
        result = await db.execute(
            select(PlatformAccount).where(
                PlatformAccount.platform == platform,
                PlatformAccount.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def _append_audit(
        self,
        db: AsyncSession,
        job: PublishJob,
        from_status: JobStatus | None,
        to_status: JobStatus,
        message: str | None = None,
        api_response: dict | None = None,
    ) -> None:
        log = AuditLog(
            publish_job_id=job.id,
            from_status=from_status,
            to_status=to_status,
            message=message,
            api_response_summary=api_response,
        )
        db.add(log)

    def _make_idempotency_key(
        self,
        upload_id: uuid.UUID,
        platform: Platform,
        scheduled_for: datetime | None,
    ) -> str:
        scheduled_str = scheduled_for.isoformat() if scheduled_for else "immediate"
        return f"{upload_id}::{platform.value}::{scheduled_str}"

    async def _enqueue(self, job: PublishJob) -> None:
        """Dispatch the job to the appropriate Celery task."""
        from app.workers import tasks

        if job.scheduled_for and job.scheduled_for > datetime.now(timezone.utc):
            eta = job.scheduled_for
            result = tasks.run_publish_job.apply_async(
                args=[str(job.id)],
                eta=eta,
                task_id=str(job.id),
            )
        else:
            result = tasks.run_publish_job.apply_async(
                args=[str(job.id)],
                task_id=str(job.id),
            )

        # Store the Celery task ID for potential revocation
        # We use a separate DB session here to avoid transaction conflicts
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            j = await session.get(PublishJob, job.id)
            if j:
                j.celery_task_id = result.id
                await session.commit()
