"""
Celery Application
==================
Background job processor for video publishing.
Uses Redis as both broker and result backend.
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "reelforge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task behavior
    task_acks_late=True,             # acknowledge only after task completes
    task_reject_on_worker_lost=True, # re-queue if worker crashes mid-task
    worker_prefetch_multiplier=1,    # one task per worker at a time (fair distribution)

    # Retry defaults
    task_default_retry_delay=60,     # 60s between retries
    task_max_retries=3,

    # Result expiry
    result_expires=86400,  # 24 hours

    # Routing — publish jobs get their own queue for isolation
    task_routes={
        "app.workers.tasks.run_publish_job": {"queue": "publish"},
        "app.workers.tasks.refresh_scheduled_jobs": {"queue": "scheduler"},
    },

    # Beat schedule — checks for jobs that are due every minute
    beat_schedule={
        "poll-scheduled-jobs": {
            "task": "app.workers.tasks.poll_scheduled_jobs",
            "schedule": crontab(minute="*"),  # every minute
        },
    },
)
