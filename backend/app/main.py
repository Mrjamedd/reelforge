"""
ReelPush — FastAPI Application Entry Point
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api.routes import auth, jobs, oauth, uploads, workspace
from app.core.config import get_settings, set_runtime_creds
from app.core.logging import get_logger, setup_logging
from app.core.security import decrypt_token
from app.db.session import AsyncSessionLocal
from app.models.models import UserAppSettings

settings = get_settings()
setup_logging()
logger = get_logger("main")


async def _load_user_app_settings() -> None:
    """Load stored platform credentials from DB so they take effect without a server restart."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(UserAppSettings))
            for row in result.scalars():
                if row.credentials_enc:
                    try:
                        data = json.loads(decrypt_token(row.credentials_enc))
                        set_runtime_creds(data)
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("failed_loading_user_app_settings", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("reelpush_starting", environment=settings.environment)
    if settings.storage_backend == "local":
        for sub in ("uploads", "thumbnails"):
            Path(settings.local_storage_path, sub).mkdir(parents=True, exist_ok=True)
    await _load_user_app_settings()
    yield
    logger.info("reelpush_shutdown")


app = FastAPI(
    title="ReelPush API",
    description="Multi-platform short-form video publishing backend.",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ─── Routes ───────────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api")
app.include_router(oauth.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")

# ─── Media serving (local dev only) ───────────────────────────────────────────
if settings.storage_backend == "local":
    storage_path = Path(settings.local_storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(storage_path)), name="media")


@app.get("/api/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
