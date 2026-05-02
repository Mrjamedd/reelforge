"""
Workspace service for persistent publish defaults, staged uploads, and direct execution.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    AdminUser,
    JobStatus,
    Platform,
    PrivacyLevel,
    PublishJob,
    PublishProfile,
    StagedPublish,
    Upload,
)
from app.schemas.schemas import PublishJobCreate
from app.schemas.schemas import WorkspacePublishRequest
from app.services.publish_service import PublishService
from app.workers.tasks import execute_publish_job


class WorkspaceService:
    async def get_or_create_profile(
        self, db: AsyncSession, current_user: AdminUser
    ) -> PublishProfile:
        result = await db.execute(
            select(PublishProfile).where(PublishProfile.admin_user_id == current_user.id)
        )
        profile = result.scalar_one_or_none()
        if profile:
            return profile

        profile = PublishProfile(
            admin_user_id=current_user.id,
            default_privacy=PrivacyLevel.PUBLIC,
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        return profile

    async def get_or_create_staged(
        self, db: AsyncSession, current_user: AdminUser
    ) -> StagedPublish:
        result = await db.execute(
            select(StagedPublish).where(StagedPublish.admin_user_id == current_user.id)
        )
        staged = result.scalar_one_or_none()
        if staged:
            return staged

        staged = StagedPublish(admin_user_id=current_user.id, selected_platforms=[])
        db.add(staged)
        await db.commit()
        await db.refresh(staged)
        return staged

    async def publish_staged(self, admin_user_id: uuid.UUID, clear_stage: bool = True) -> list[PublishJob]:
        from app.db.session import AsyncSessionLocal

        publish_svc = PublishService()

        async with AsyncSessionLocal() as db:
            admin = await db.get(AdminUser, admin_user_id)
            if not admin or not admin.is_active:
                raise RuntimeError("Admin user not found or inactive.")

            profile = await self.get_or_create_profile(db, admin)
            staged = await self.get_or_create_staged(db, admin)
            if not staged.upload_id:
                raise RuntimeError("No staged upload found for this user.")

            upload = await db.get(Upload, staged.upload_id)
            if not upload or upload.uploaded_by_id != admin.id:
                raise RuntimeError("The staged upload is missing or does not belong to this user.")

            selected_platforms = [Platform(value) for value in (staged.selected_platforms or [])]
            if not selected_platforms:
                raise RuntimeError("No staged platforms selected for this user.")

            jobs: list[PublishJob] = []
            for platform in selected_platforms:
                job = await publish_svc.create_job(
                    db,
                    PublishJobCreate(
                        upload_id=upload.id,
                        platform=platform,
                        title=profile.default_title,
                        caption=profile.default_caption,
                        hashtags=profile.default_hashtags,
                        privacy=profile.default_privacy or PrivacyLevel.PUBLIC,
                    ),
                    admin.id,
                    enqueue=False,
                )
                jobs.append(job)

        for job in jobs:
            await execute_publish_job(str(job.id))

        async with AsyncSessionLocal() as db:
            refreshed_jobs: list[PublishJob] = []
            for job in jobs:
                refreshed = await db.get(PublishJob, job.id)
                if refreshed:
                    refreshed_jobs.append(refreshed)

            if clear_stage and refreshed_jobs and all(
                job.status in (JobStatus.POSTED, JobStatus.REQUIRES_MANUAL) for job in refreshed_jobs
            ):
                admin = await db.get(AdminUser, admin_user_id)
                if admin:
                    staged = await self.get_or_create_staged(db, admin)
                    staged.upload_id = None
                    staged.selected_platforms = []
                    await db.commit()

            return refreshed_jobs

    async def publish_now(
        self,
        admin_user_id: uuid.UUID,
        request: WorkspacePublishRequest,
        clear_stage: bool = True,
    ) -> list[PublishJob]:
        from app.db.session import AsyncSessionLocal

        publish_svc = PublishService()

        async with AsyncSessionLocal() as db:
            admin = await db.get(AdminUser, admin_user_id)
            if not admin or not admin.is_active:
                raise RuntimeError("Admin user not found or inactive.")

            upload = await db.get(Upload, request.upload_id)
            if not upload or upload.uploaded_by_id != admin.id:
                raise RuntimeError("The selected upload is missing or does not belong to this user.")

            selected_platforms = list(request.selected_platforms or [])
            if not selected_platforms:
                raise RuntimeError("No platforms selected for this publish.")

            profile = await self.get_or_create_profile(db, admin)
            profile.default_title = request.default_title
            profile.default_caption = request.default_caption
            profile.default_hashtags = request.default_hashtags
            profile.default_privacy = request.default_privacy

            staged = await self.get_or_create_staged(db, admin)
            staged.upload_id = upload.id
            staged.selected_platforms = [platform.value for platform in selected_platforms]

            jobs: list[PublishJob] = []
            for platform in selected_platforms:
                job = await publish_svc.create_job(
                    db,
                    PublishJobCreate(
                        upload_id=upload.id,
                        platform=platform,
                        title=request.default_title,
                        caption=request.default_caption,
                        hashtags=request.default_hashtags,
                        privacy=request.default_privacy or PrivacyLevel.PUBLIC,
                    ),
                    admin.id,
                    enqueue=False,
                )
                jobs.append(job)

        for job in jobs:
            await execute_publish_job(str(job.id))

        async with AsyncSessionLocal() as db:
            refreshed_jobs: list[PublishJob] = []
            for job in jobs:
                refreshed = await db.get(PublishJob, job.id)
                if refreshed:
                    refreshed_jobs.append(refreshed)

            if clear_stage and refreshed_jobs and all(
                job.status in (JobStatus.POSTED, JobStatus.REQUIRES_MANUAL) for job in refreshed_jobs
            ):
                admin = await db.get(AdminUser, admin_user_id)
                if admin:
                    staged = await self.get_or_create_staged(db, admin)
                    staged.upload_id = None
                    staged.selected_platforms = []
                    await db.commit()

            return refreshed_jobs
