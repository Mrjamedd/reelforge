"""
Interactive local publish flow for ReelPush.

Run inside the backend container:
    python scripts/local_publish.py --video /storage/inbox/my-video.mp4
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from the backend/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.core.config import get_settings
from app.models.models import AdminUser, Platform, PlatformAccount, PrivacyLevel, PublishJob, Upload
from app.providers.registry import get_provider
from app.schemas.schemas import PublishJobCreate
from app.services.media_service import MediaService, MediaValidationError
from app.services.publish_service import PublishService
from app.workers.tasks import execute_publish_job
from app.db.session import AsyncSessionLocal

settings = get_settings()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a local video and publish it using a connected ReelPush account."
    )
    parser.add_argument("--video", help="Path visible inside the backend container.")
    parser.add_argument(
        "--platform",
        choices=[platform.value for platform in Platform],
        help="Platform to publish to.",
    )
    parser.add_argument("--title", help="Post title.")
    parser.add_argument("--caption", help="Post caption or description.")
    parser.add_argument(
        "--hashtags",
        help="Comma-separated hashtags without leading #.",
    )
    parser.add_argument(
        "--privacy",
        choices=[level.value for level in PrivacyLevel],
        help="Privacy level for the publish.",
    )
    parser.add_argument(
        "--scheduled-for",
        help="Optional UTC ISO-8601 datetime, e.g. 2026-04-23T14:00:00Z.",
    )
    parser.add_argument(
        "--cleanup-source",
        action="store_true",
        help="Delete the temporary source file after it has been imported into storage.",
    )
    return parser


def prompt(label: str, default: str | None = None, *, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("A value is required.")


def parse_scheduled_for(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def get_admin_user(db) -> AdminUser:
    preferred = await db.execute(
        select(AdminUser).where(AdminUser.email == settings.admin_email)
    )
    user = preferred.scalar_one_or_none()
    if user:
        return user

    fallback = await db.execute(
        select(AdminUser).where(AdminUser.is_active.is_(True)).order_by(AdminUser.created_at.asc())
    )
    user = fallback.scalars().first()
    if user:
        return user

    raise RuntimeError(
        "No admin user exists yet. Run `python scripts/seed_admin.py` first."
    )


async def get_connected_accounts(db) -> list[PlatformAccount]:
    result = await db.execute(
        select(PlatformAccount).order_by(PlatformAccount.connected_at.asc())
    )
    return list(result.scalars().all())


def choose_platform(args_platform: str | None, connected_accounts: list[PlatformAccount]) -> Platform:
    connected = {account.platform.value: account for account in connected_accounts}
    if not connected:
        raise RuntimeError(
            "No connected platform account found. Connect a platform once through OAuth first."
        )

    if args_platform:
        platform = Platform(args_platform)
    elif "youtube" in connected:
        platform = Platform.YOUTUBE
    elif len(connected) == 1:
        platform = next(iter(connected.values())).platform
    else:
        print("Connected platforms:")
        for account in connected_accounts:
            username = account.platform_username or account.platform_user_id
            print(f"  - {account.platform.value} ({username})")
        platform = Platform(prompt("Platform", required=True).lower())

    if platform.value not in connected:
        raise RuntimeError(
            f"No connected {platform.value} account found. Connect it once before using local publish."
        )

    return platform


def ensure_local_mode_supported(platform: Platform, media_svc: MediaService) -> None:
    if platform == Platform.TIKTOK:
        raise RuntimeError(
            "TikTok local auto-post is not ready in this codebase yet. "
            "The provider still needs a real upload implementation."
        )
    if platform == Platform.INSTAGRAM and media_svc.backend == "local":
        raise RuntimeError(
            "Instagram local mode needs a publicly reachable video URL. "
            "Use S3/public storage or a tunnel before trying local auto-post."
        )


async def import_upload(db, source_path: Path, uploaded_by_id, media_svc: MediaService) -> Upload:
    validation = media_svc.validate_video_file(str(source_path))
    storage_key = await media_svc.store_upload_from_path(str(source_path), source_path.name)
    stored_path = media_svc.get_local_path(storage_key)
    validation.update(media_svc.validate_video_file(str(stored_path)))

    metadata = media_svc.extract_metadata(str(stored_path))
    thumbnail_key = None
    thumb_tmp = stored_path.parent / f"{stored_path.stem}_thumb.jpg"
    if media_svc.generate_thumbnail(str(stored_path), str(thumb_tmp)):
        thumbnail_key = await media_svc.store_thumbnail(thumb_tmp.read_bytes(), storage_key)
        thumb_tmp.unlink(missing_ok=True)

    upload = Upload(
        original_filename=source_path.name,
        storage_key=storage_key,
        thumbnail_key=thumbnail_key,
        mime_type=validation["mime_type"],
        file_size_bytes=validation["file_size_bytes"],
        duration_seconds=metadata.get("duration_seconds"),
        width=metadata.get("width"),
        height=metadata.get("height"),
        uploaded_by_id=uploaded_by_id,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)
    return upload


async def run() -> int:
    args = build_parser().parse_args()
    media_svc = MediaService()

    async with AsyncSessionLocal() as db:
        connected_accounts = await get_connected_accounts(db)
        platform = choose_platform(args.platform, connected_accounts)
        ensure_local_mode_supported(platform, media_svc)
        provider = get_provider(platform)
        if not provider.is_configured:
            raise RuntimeError(
                f"{provider.platform_name} credentials are not configured in .env yet."
            )
        admin = await get_admin_user(db)

    raw_video = args.video or prompt("Video path inside container", required=True)
    source_path = Path(raw_video).expanduser()
    if not source_path.is_file():
        raise RuntimeError(f"Video file not found: {source_path}")

    default_title = source_path.stem.replace("_", " ").replace("-", " ").strip()
    title = args.title if args.title is not None else prompt("Title", default=default_title)
    caption = args.caption if args.caption is not None else prompt("Caption", default="")
    hashtags = args.hashtags if args.hashtags is not None else prompt(
        "Hashtags (comma-separated, no #)", default=""
    )
    privacy = args.privacy if args.privacy is not None else prompt("Privacy", default="public")
    scheduled_for = parse_scheduled_for(args.scheduled_for)

    publish_svc = PublishService()

    try:
        async with AsyncSessionLocal() as db:
            upload = await import_upload(db, source_path, admin.id, media_svc)
            job = await publish_svc.create_job(
                db,
                PublishJobCreate(
                    upload_id=upload.id,
                    platform=platform,
                    title=title or None,
                    caption=caption or None,
                    hashtags=hashtags or None,
                    privacy=PrivacyLevel(privacy),
                    scheduled_for=scheduled_for,
                ),
                admin.id,
                enqueue=False,
            )
    except MediaValidationError as exc:
        raise RuntimeError(str(exc)) from exc

    if args.cleanup_source:
        source_path.unlink(missing_ok=True)

    result = await execute_publish_job(str(job.id))

    async with AsyncSessionLocal() as db:
        refreshed_job = await db.get(PublishJob, job.id)

    print("")
    print(f"Platform: {platform.value}")
    print(f"Job ID:   {job.id}")
    print(f"Status:   {refreshed_job.status.value}")
    if refreshed_job.platform_post_url:
        print(f"Post URL: {refreshed_job.platform_post_url}")
    if refreshed_job.platform_post_id:
        print(f"Post ID:  {refreshed_job.platform_post_id}")
    if not result.get("success"):
        error = result.get("error") or "Publish failed. Check the audit log in the database."
        print(f"Error:    {error}")
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
