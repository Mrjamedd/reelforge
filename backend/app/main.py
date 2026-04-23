"""
ReelForge — FastAPI Application Entry Point
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, jobs, oauth, uploads
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

settings = get_settings()
setup_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("reelforge_starting", environment=settings.environment)
    # Ensure local storage directories exist
    if settings.storage_backend == "local":
        for sub in ("uploads", "thumbnails"):
            Path(settings.local_storage_path, sub).mkdir(parents=True, exist_ok=True)
    yield
    logger.info("reelforge_shutdown")


app = FastAPI(
    title="ReelForge API",
    description="Multi-platform short-form video publishing backend.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routes ───────────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api")
app.include_router(oauth.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")

# ─── Media serving (local dev only) ───────────────────────────────────────────
if settings.storage_backend == "local":
    storage_path = Path(settings.local_storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(storage_path)), name="media")


@app.get("/api/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
