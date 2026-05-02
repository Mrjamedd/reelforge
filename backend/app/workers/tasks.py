"""
Celery Tasks
============
Background workers that execute publish jobs.

Key design decisions:
  - Tasks are idempotent: checking job state before acting prevents double-posts
  - Token refresh happens automatically before each publish attempt
  - Structured logging on every state transition
  - Exponential backoff on retries via Celery's built-in retry mechanism
"""

import asyncio
import uuid
from datetime import datetime, timezone

from celery import Task

from app.core.logging import get_logger
from app.models.models import JobStatus, Platform, PlatformAccount, PublishJob
from app.providers.base import PublishPayload
from app.workers.celery_app import celery_app

logger = get_logger("worker.tasks")


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.run_publish_job",
    max_retries=3,
    default_retry_delay=120,  # 2 minutes initial delay
    queue="publish",
    acks_late=True,
)
def run_publish_job(self: Task, job_id: str) -> dict:
    """
    Execute a single publish job.
    
    Flow:
      1. Load job from DB; bail if already processed (idempotency guard)
      2. Ensure token is fresh
      3. Validate payload
      4. Upload video to platform
      5. Publish (or schedule natively if supported)
      6. Record result and update audit log
    """
    return _run_async(execute_publish_job(job_id, task=self))


async def execute_publish_job(job_id: str, task: Task | None = None) -> dict:
    from app.db.session import AsyncSessionLocal
    from app.providers.registry import get_provider
    from app.services.media_service import MediaService
    from app.services.oauth_service import OAuthService
    from app.services.publish_service import PublishService

    async with AsyncSessionLocal() as db:
        job: PublishJob | None = await db.get(PublishJob, uuid.UUID(job_id))
        if not job:
            logger.error("publish_job_not_found", job_id=job_id)
            return {"error": "job_not_found"}

        # Idempotency guard: don't re-run completed or cancelled jobs
        if job.status in (JobStatus.POSTED, JobStatus.CANCELLED):
            logger.info(
                "publish_job_skipped_already_done",
                job_id=job_id,
                status=job.status,
            )
            return {"skipped": True, "status": job.status}

        publish_svc = PublishService()
        oauth_svc = OAuthService()
        media_svc = MediaService()

        # ── Mark as UPLOADING ──────────────────────────────────────────────
        await publish_svc.transition_status(
            db, job, JobStatus.UPLOADING, "Starting publish attempt."
        )
        job.attempt_count += 1
        await db.commit()

        try:
            # ── Refresh token ──────────────────────────────────────────────
            account: PlatformAccount = await db.get(PlatformAccount, job.platform_account_id)
            account = await oauth_svc.ensure_fresh_token(db, account)

            # ── Load upload ────────────────────────────────────────────────
            from app.models.models import Upload
            upload: Upload = await db.get(Upload, job.upload_id)

            provider = get_provider(job.platform)

            # Build normalized payload
            hashtags = [h.strip() for h in (job.hashtags or "").split(",") if h.strip()]
            if job.platform == Platform.INSTAGRAM:
                video_path = await media_svc.get_instagram_public_url(upload.storage_key)
            else:
                video_path = await media_svc.get_public_url(upload.storage_key)
            payload = PublishPayload(
                video_path=video_path,
                local_video_path=str(media_svc.get_local_path(upload.storage_key)),
                title=job.title,
                caption=job.caption,
                hashtags=hashtags,
                privacy=job.privacy.value if job.privacy else "public",
                scheduled_for=job.scheduled_for,
            )

            # ── Validate ───────────────────────────────────────────────────
            errors = provider.validate_post_payload(payload)
            if errors:
                msg = "Validation failed: " + "; ".join(errors)
                logger.warning("publish_job_validation_failed", job_id=job_id, errors=errors)
                await publish_svc.transition_status(db, job, JobStatus.FAILED, msg)
                await db.commit()
                return {"success": False, "validation_errors": errors}

            # ── Upload video ───────────────────────────────────────────────
            upload_id = await provider.create_upload(account, payload.video_path)

            # ── Publish or Schedule ────────────────────────────────────────
            await publish_svc.transition_status(
                db, job, JobStatus.PROCESSING, "Video uploaded; publishing now."
            )
            await db.commit()

            if job.scheduled_for and job.scheduled_for > datetime.now(timezone.utc):
                try:
                    result = await provider.schedule_publish(account, upload_id, payload)
                except NotImplementedError:
                    # Platform doesn't support native scheduling — already handled by Celery eta
                    result = await provider.publish_now(account, upload_id, payload)
            else:
                result = await provider.publish_now(account, upload_id, payload)

            # ── Record result ──────────────────────────────────────────────
            if result.success:
                await oauth_svc.mark_credential_health(
                    db,
                    account,
                    "verified",
                    f"{provider.platform_name} publish completed successfully.",
                    source="publish",
                )
                job.platform_post_id = result.platform_post_id
                job.platform_post_url = result.platform_post_url

                if result.requires_manual_completion:
                    final_status = JobStatus.REQUIRES_MANUAL
                    msg = (
                        f"{provider.platform_name} post is in your drafts inbox. "
                        "Please complete publishing manually in the platform app."
                    )
                else:
                    final_status = JobStatus.POSTED
                    msg = f"Successfully published. Post ID: {result.platform_post_id}"

                await publish_svc.transition_status(
                    db, job, final_status, msg,
                    api_response=_summarise(result.raw_response),
                )
                logger.info(
                    "publish_job_success",
                    job_id=job_id,
                    platform=job.platform,
                    post_id=result.platform_post_id,
                )
            else:
                if oauth_svc.is_credential_failure(result.error_message or "", result.raw_response):
                    await oauth_svc.mark_credential_health(
                        db,
                        account,
                        "invalid",
                        oauth_svc.describe_credential_failure(result.error_message or "Platform API error", result.raw_response),
                        source="publish",
                    )
                await publish_svc.transition_status(
                    db, job, JobStatus.FAILED,
                    f"Platform API error: {result.error_message}",
                    api_response=_summarise(result.raw_response),
                )
                logger.error(
                    "publish_job_failed",
                    job_id=job_id,
                    platform=job.platform,
                    error=result.error_message,
                )

            await db.commit()
            return {"success": result.success, "post_id": result.platform_post_id}

        except Exception as exc:
            # Unexpected error — mark failed and maybe retry
            logger.exception("publish_job_exception", job_id=job_id, error=str(exc))
            auth_problem = oauth_svc.is_credential_failure(exc)
            if auth_problem and "account" in locals() and account:
                await oauth_svc.mark_credential_health(
                    db,
                    account,
                    "invalid",
                    oauth_svc.describe_credential_failure(exc),
                    source="publish",
                )
            await publish_svc.transition_status(
                db, job, JobStatus.FAILED, f"Unexpected error: {str(exc)[:500]}"
            )
            await db.commit()

            # Retry with exponential backoff if we haven't hit max attempts
            if task is not None and job.attempt_count < job.max_attempts and not auth_problem:
                delay = 60 * (2 ** (job.attempt_count - 1))  # 60s, 120s, 240s
                raise task.retry(exc=exc, countdown=delay)

            return {"success": False, "error": str(exc)}


@celery_app.task(
    name="app.workers.tasks.poll_scheduled_jobs",
    queue="scheduler",
)
def poll_scheduled_jobs() -> dict:
    """
    Beat task: find SCHEDULED jobs whose scheduled_for time has passed
    and re-enqueue them.
    This handles the case where our Celery eta wasn't set or got lost.
    """
    return _run_async(_do_poll_scheduled_jobs())


async def _do_poll_scheduled_jobs() -> dict:
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.models import PublishJob

    now = datetime.now(timezone.utc)
    dispatched = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PublishJob).where(
                PublishJob.status == JobStatus.SCHEDULED,
                PublishJob.scheduled_for <= now,
            )
        )
        due_jobs = result.scalars().all()

        for job in due_jobs:
            run_publish_job.apply_async(
                args=[str(job.id)],
                task_id=str(job.id) + "-retry",
            )
            dispatched += 1
            logger.info("scheduled_job_dispatched", job_id=str(job.id))

    return {"dispatched": dispatched}


def _summarise(raw: dict | None) -> dict | None:
    """Truncate raw API response to avoid storing enormous JSONB blobs."""
    if not raw:
        return None
    import json
    serialized = json.dumps(raw)
    if len(serialized) > 4000:
        return {"truncated": True, "preview": serialized[:4000]}
    return raw
