"""
Publish the current staged upload for an admin user.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.models import AdminUser
from app.services.workspace_service import WorkspaceService

settings = get_settings()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish the staged ReelPush upload for the selected admin user."
    )
    parser.add_argument(
        "--admin-email",
        help="Admin email whose staged workspace should be published. Defaults to ADMIN_EMAIL.",
    )
    parser.add_argument(
        "--keep-staged",
        action="store_true",
        help="Leave the staged upload in place after a fully successful publish.",
    )
    return parser


async def get_admin_user(email: str) -> AdminUser:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AdminUser).where(AdminUser.email == email))
        user = result.scalar_one_or_none()
        if user:
            return user
    raise RuntimeError(f"No admin user found for {email}.")


async def run() -> int:
    args = build_parser().parse_args()
    admin_email = args.admin_email or settings.admin_email
    admin = await get_admin_user(admin_email)
    jobs = await WorkspaceService().publish_staged(admin.id, clear_stage=not args.keep_staged)

    print("")
    print(f"Admin: {admin_email}")
    for job in jobs:
        print("")
        print(f"Platform: {job.platform.value}")
        print(f"Status:   {job.status.value}")
        if job.platform_post_url:
            print(f"Post URL: {job.platform_post_url}")
        if job.platform_post_id:
            print(f"Post ID:  {job.platform_post_id}")

    return 0 if all(job.platform_post_id or job.status.value == "requires_manual" for job in jobs) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
