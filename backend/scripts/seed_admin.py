"""
Seed the initial admin user.
Run once after `alembic upgrade head`.

Usage:
    cd backend
    python scripts/seed_admin.py
"""

import asyncio
import sys
from pathlib import Path

# Allow running from the backend/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.models import AdminUser

settings = get_settings()


async def seed():
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(AdminUser).where(AdminUser.email == settings.admin_email)
        )
        if existing.scalar_one_or_none():
            print(f"Admin user {settings.admin_email!r} already exists. Skipping.")
            return

        if not settings.admin_password:
            print("ADMIN_PASSWORD env var not set — skipping seed.")
            return

        user = AdminUser(
            email=settings.admin_email,
            hashed_password=hash_password(settings.admin_password),
            is_active=True,
            is_email_verified=True,  # Pre-verified seed account
        )
        db.add(user)
        await db.commit()
        print(f"✓ Admin user created: {settings.admin_email}")


if __name__ == "__main__":
    asyncio.run(seed())
