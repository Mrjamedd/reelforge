# ReelPush Server-Backend Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move ReelPush's secure backend, OAuth, token storage, and publishing logic from a Docker-Compose-on-Mac stack to a single uvicorn process on Oracle Cloud Ubuntu (`ubuntu@129.213.126.251`), HTTPS-fronted by Caddy on a DuckDNS placeholder hostname, while leaving the existing `discordbot.service` at `/home/ubuntu/discord-bot` completely untouched. The local Mac Tk app becomes a thin client that authenticates via JWT and stores its token in macOS Keychain.

**Architecture:** Drop Postgres, Redis, Celery worker, and Celery beat — replace with SQLite (WAL mode, aiosqlite driver) plus an in-process `AsyncIOScheduler` from APScheduler for periodic jobs and FastAPI `BackgroundTasks` for sub-second post-response work. A new `/media/{token}` route serves uploaded videos as Instagram-Container-API-compatible public HTTPS URLs. The Mac app's data and OAuth code is replaced by a `ServerAPI` httpx wrapper; provider client secrets and access tokens never touch the Mac again.

**Tech Stack:** Python 3.11+ (server, via venv), Python 3.14 (Mac, system framework install), FastAPI 0.111, SQLAlchemy 2.0 async + aiosqlite, APScheduler 3.10+, httpx 0.27, keyring 24+, Caddy 2.x, systemd, DuckDNS.

**Reference spec:** [`docs/superpowers/specs/2026-05-01-reelpush-server-backend-design.md`](../specs/2026-05-01-reelpush-server-backend-design.md)

**Operational guardrails (read once, apply throughout):**
- The Discord bot at `/home/ubuntu/discord-bot` and `discordbot.service` are off-limits. Never edit, restart, stop, or change permissions on anything inside that path or the unit. Verify it's `active (running)` before and after every server-side task.
- Never edit `/home/ubuntu/.env` (Discord bot env). All ReelPush env lives at `/home/ubuntu/reelpush-backend/.env`.
- Server `.env` is created and edited *only* on the server via interactive SSH (`nano`/`vi`). Never `scp` or `rsync` an env file from local to server — that creates a shell-history breadcrumb of secrets.
- Per CLAUDE.md: Claude reviews each diff before commit. No `git push` until end-of-phase review.
- Per CLAUDE.md Codex Token Policy: Codex tokens are free; Claude context is precious. For substantial code rewrites, dispatch Codex with the prompt embedded in the step. For 1–10 line edits, write the diff inline.

---

## File Map

### Backend (server-side code — local repo first, deployed in Phase 3)

| Action | Path | Responsibility |
|---|---|---|
| Modify | `backend/requirements.txt` | Drop celery/redis/asyncpg/psycopg2/kombu; add aiosqlite, apscheduler, itsdangerous |
| Modify | `backend/app/core/config.py` | Drop redis/celery settings; add `public_base_url`, `media_url_ttl_seconds`; flip default `database_url` to SQLite |
| Modify | `backend/app/db/session.py` | Replace asyncpg engine with aiosqlite engine; remove pool_size/max_overflow (irrelevant for SQLite); enable WAL on first connect |
| Create | `backend/app/workers/scheduler.py` | `AsyncIOScheduler` instance + `start_scheduler()` / `stop_scheduler()` for FastAPI lifespan |
| Modify | `backend/app/workers/tasks.py` | Strip Celery decorators; expose `run_publish_job(job_id)`, `poll_scheduled_jobs()`, `refresh_scheduled_jobs(job_id, when)` as plain async functions |
| Delete | `backend/app/workers/celery_app.py` | Replaced by scheduler.py |
| Modify | `backend/app/services/publish_service.py` | Replace `tasks.run_publish_job.apply_async(args=[job_id], countdown=...)` calls (lines ~267 and ~273) with `scheduler.add_job(run_publish_job, id=f"publish:{job_id}", args=[job_id], trigger=DateTrigger(run_date=...))` |
| Modify | `backend/app/services/oauth_service.py` | If a periodic refresh exists, register it via APScheduler interval trigger in scheduler.py instead of celery beat |
| Modify | `backend/app/services/media_service.py` | Add `make_signed_url(storage_key) -> str` returning `f"{settings.public_base_url}/media/{token}"` where token is an itsdangerous-signed payload |
| Create | `backend/app/api/routes/media.py` | `GET /media/{token}` — verify itsdangerous signature, decode storage_key, stream file from `local_storage_path` |
| Modify | `backend/app/api/routes/oauth.py` | Replace any hardcoded `localhost:8100`/`api_url`-as-localhost references with `settings.public_base_url` for redirect URI construction |
| Modify | `backend/app/api/routes/uploads.py` | After saving file, return `media_service.make_signed_url(storage_key)` as the upload's `url` field |
| Modify | `backend/app/main.py` | Wire `start_scheduler()` / `stop_scheduler()` into lifespan; replace `app.mount("/media", StaticFiles(...))` with `app.include_router(media.router)` so token-validated route handles serving |
| Create | `backend/app/db/migrations/` (alembic) | Run `alembic init` here; configure `env.py` to read from `app.core.config.get_settings().database_url` and `target_metadata = Base.metadata`; one initial migration |
| Modify | `backend/tests/conftest.py` | Switch test DB to in-memory or tmp-file SQLite |
| Modify | `backend/tests/test_core.py` | Drop any Celery-coupled tests; keep DB/auth tests against SQLite |
| Create | `backend/tests/test_signed_media_url.py` | TDD for `make_signed_url` + `/media/{token}` route |
| Create | `backend/tests/test_scheduler.py` | TDD for scheduler start/stop and job registration |

### Mac client (local Tk app)

| Action | Path | Responsibility |
|---|---|---|
| Create | `reelpush_client/__init__.py` | Empty package marker |
| Create | `reelpush_client/config.py` | Loads `~/Library/Application Support/ReelPush/config.json`; `REELPUSH_API_URL` env override |
| Create | `reelpush_client/api.py` | `ServerAPI` class wrapping httpx with JWT-from-keychain bearer header; methods: `login`, `logout`, `me`, `list_accounts`, `start_oauth`, `upload_media`, `create_publish_job`, `get_job`, `list_jobs` |
| Create | `reelpush_client/auth.py` | `LoginWindow` Tk modal (email + password) + `prompt_login_if_needed(api)` helper |
| Create | `reelpush_client/oauth_browser.py` | `connect_provider(api, provider)` — opens system browser, polls accounts, returns when account appears |
| Modify | `reelpush_desktop.py` | Replace direct provider/DB calls with `ServerAPI`; remove all `dotenv` and provider-secret reads |
| Delete | `reelpush_desktop.py.bak` | Stale backup, no longer relevant |
| Create | `reelpush_client_requirements.txt` | Mac client deps: `httpx`, `keyring`, `tenacity`, `platformdirs` |

### Server-side artifacts (created in Phase 3+)

| Action | Path (on server) | Responsibility |
|---|---|---|
| Create | `/home/ubuntu/reelpush-backend/` | Project root, owned `ubuntu:ubuntu`, mode 0750 |
| Create | `/home/ubuntu/reelpush-backend/venv/` | Python 3.11 venv, isolated from Discord bot's venv |
| Create | `/home/ubuntu/reelpush-backend/data/` | SQLite DB lives here; mode 0700 |
| Create | `/home/ubuntu/reelpush-backend/storage/` | Uploaded media; mode 0750 |
| Create | `/home/ubuntu/reelpush-backend/.env` | Server-only secrets, mode 0600 |
| Create | `/etc/systemd/system/reelpush-backend.service` | systemd unit, no relationship to discordbot.service |
| Modify | `/etc/caddy/Caddyfile` | Additive vhost block only; preserves any existing config |

---

## Phase 0: Local working state hygiene

### Task 1: Sort the existing uncommitted backend changes

**Context:** `git status` on the local repo currently shows ~25 modified files in `backend/` plus deleted `frontend/Dockerfile`, `frontend/index.html`, plus modified `.env.example` and `README.md`. Some of this may be from the prior `instagram-public-media-url` plan; some may be unrelated WIP. We need a clean baseline before starting the migration branch so the diff stays scoped.

**Files:**
- Inspect: full repo working tree

- [ ] **Step 1: Capture current state**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
git status > /tmp/reelpush-pre-migration-status.txt
git diff > /tmp/reelpush-pre-migration-diff.patch
git diff --stat
```

- [ ] **Step 2: Decide on each modified group**

Options for the executor:
- (a) **Commit current WIP to a `pre-migration-wip` branch** if it's coherent feature work the user wants to keep — `git checkout -b pre-migration-wip && git add -A && git commit -m "wip: snapshot before server-backend migration"` then `git checkout main`
- (b) **Stash with intent to restore** if it's exploratory and the user might revisit — `git stash push -u -m "pre-migration WIP"`
- (c) **Discard** only with explicit user approval

The executor MUST ask the user which option before proceeding. Default recommendation: option (a) — keeping a labeled snapshot branch costs nothing and protects work-in-progress.

- [ ] **Step 3: Verify clean working tree**

```bash
git status
```

Expected: `nothing to commit, working tree clean` (or only ignored files)

- [ ] **Step 4: Pull latest main**

```bash
git checkout main
git pull --ff-only
```

If the pull fails because there are no upstream changes, that's fine — proceed.

- [ ] **Step 5: No commit for this task** — task 1 is housekeeping only.

---

### Task 2: Create migration branch

**Files:**
- Create branch: `server-backend-migration`

- [ ] **Step 1: Branch from clean main**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
git checkout -b server-backend-migration
```

- [ ] **Step 2: Confirm branch**

```bash
git rev-parse --abbrev-ref HEAD
```

Expected: `server-backend-migration`

- [ ] **Step 3: No commit** — branch creation only.

---

## Phase 1: Read-only preflight on Oracle VM

### Task 3: SSH preflight + baseline capture

**Goal:** Confirm Discord bot health, identify which (if any) reverse proxy already runs, capture baseline state for post-deploy diff. Read-only — zero changes to the server.

**Files:**
- Capture to: `docs/superpowers/runs/2026-05-01-preflight.txt` (local)

- [ ] **Step 1: Confirm SSH key works**

```bash
ssh -o ConnectTimeout=10 ubuntu@129.213.126.251 'echo OK; uname -a'
```

Expected: `OK` followed by Linux kernel info. If this fails, stop and resolve SSH access before continuing.

- [ ] **Step 2: Discord bot health baseline**

```bash
ssh ubuntu@129.213.126.251 '
echo "=== discordbot service ==="
systemctl status discordbot --no-pager
echo
echo "=== discord-bot git status ==="
git -C /home/ubuntu/discord-bot status
echo
echo "=== discord-bot last commit ==="
git -C /home/ubuntu/discord-bot log -1 --oneline
'
```

Capture output to `docs/superpowers/runs/2026-05-01-preflight.txt`. The git output here is the **baseline** that Phase 5 verifies against — it must be byte-identical at the end.

- [ ] **Step 3: Server resource and port survey**

```bash
ssh ubuntu@129.213.126.251 '
echo "=== disk ==="
df -h
echo
echo "=== memory ==="
free -h
echo
echo "=== listening ports ==="
sudo ss -tulpn
echo
echo "=== cgroup memory of discordbot ==="
systemctl show discordbot -p MemoryCurrent --value
'
```

Append to the same preflight file. **Specifically check whether port 8001 is free** (it should not appear in `ss` output). If 8001 is taken, Phase 3 picks the next free high port (8002, 8003) — update the systemd unit and Caddyfile accordingly.

- [ ] **Step 4: Reverse-proxy survey**

```bash
ssh ubuntu@129.213.126.251 '
for unit in nginx caddy apache2 traefik; do
  echo "=== $unit ==="
  systemctl status $unit --no-pager 2>&1 | head -5
done
echo
echo "=== /etc/caddy ==="
ls -la /etc/caddy 2>/dev/null
echo
echo "=== /etc/nginx ==="
ls -la /etc/nginx 2>/dev/null
'
```

Decision point captured in the preflight file: **which proxy will Phase 4 use?**
- If Caddy is already installed → reuse it (just add a vhost in Phase 4)
- If only Nginx is installed and serving production traffic → Phase 4 falls back to Nginx + Certbot
- If nothing is listening on 80/443 → Phase 4 installs Caddy fresh

- [ ] **Step 5: Filesystem layout sanity**

```bash
ssh ubuntu@129.213.126.251 '
ls -la /home/ubuntu
echo
echo "=== existing reelpush dirs (should be empty) ==="
ls -la /home/ubuntu/reelpush* 2>&1 || echo "(none — good)"
'
```

If `/home/ubuntu/reelpush-backend` already exists from a prior attempt, **stop and ask the user**: do we resume or wipe and restart?

- [ ] **Step 6: Save preflight file**

```bash
mkdir -p "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/docs/superpowers/runs"
# save the captured outputs from steps 2-5 here
```

Filename convention: `docs/superpowers/runs/2026-05-01-preflight.txt`. This file is the source of truth Phase 5 verifies against.

- [ ] **Step 7: No commit yet** — preflight artifacts can be committed at end of Phase 1 if user wants them in the repo, but they are not strictly required.

---

## Phase 2: Backend code refactor (local repo, no server contact)

### Task 4: Update `requirements.txt`

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Replace dependency list**

Open `backend/requirements.txt` and change to:

```
# Web framework
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-multipart==0.0.9

# Database (SQLite, async)
sqlalchemy[asyncio]==2.0.30
aiosqlite==0.20.0
alembic==1.13.1

# Auth & Security
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
bcrypt==4.0.1
cryptography==42.0.7
itsdangerous==2.2.0

# Background scheduling (in-process)
apscheduler==3.10.4

# HTTP client (for platform API calls)
httpx==0.27.0

# Validation
pydantic==2.7.1
pydantic-settings==2.2.1
email-validator==2.1.1

# Media processing
pillow==10.3.0
ffmpeg-python==0.2.0
python-magic==0.4.27

# Object storage (kept — supports future S3/R2 swap, see config.storage_backend)
boto3==1.34.99

# Utilities
python-dotenv==1.0.1
structlog==24.1.0
tenacity==8.3.0

# Dev/test
pytest==8.2.0
pytest-asyncio==0.23.6
```

Removed compared to old: `asyncpg`, `psycopg2-binary`, `celery`, `redis`, `kombu`. Added: `aiosqlite`, `apscheduler`, `itsdangerous`.

- [ ] **Step 2: Reinstall locally**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install -r requirements.txt
```

Expected: clean install. If anything errors on Mac (e.g., `python-magic` needing `libmagic`), confirm `tests/conftest.py` already stubs `magic` per CLAUDE.md.

- [ ] **Step 3: Quick import smoke test**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "import fastapi, sqlalchemy, aiosqlite, apscheduler, itsdangerous; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps: drop celery/redis/postgres, add aiosqlite/apscheduler/itsdangerous

Migration from Docker-Compose 5-service stack to single-process FastAPI
with SQLite + APScheduler. itsdangerous signs media URL tokens.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Switch DB driver to SQLite (aiosqlite + WAL)

**Files:**
- Modify: `backend/app/db/session.py`

- [ ] **Step 1: Write the failing test first**

Create `backend/tests/test_db_session.py`:

```python
"""Verify DB session uses aiosqlite and enables WAL."""
import pytest
from sqlalchemy import text
from app.db.session import engine, get_db


@pytest.mark.asyncio
async def test_engine_dialect_is_sqlite():
    assert engine.dialect.name == "sqlite"


@pytest.mark.asyncio
async def test_wal_mode_enabled():
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        assert mode.lower() == "wal", f"expected WAL, got {mode}"


@pytest.mark.asyncio
async def test_get_db_yields_session():
    async for session in get_db():
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
        break
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_db_session.py -v
```

Expected: dialect test fails (postgres), WAL test fails (postgres doesn't have it).

- [ ] **Step 3: Rewrite `backend/app/db/session.py`**

Replace the file with:

```python
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# SQLite-only engine. WAL mode is set via a connect-time hook below.
# Pool sizing args from the previous Postgres setup are intentionally absent —
# SQLite uses SingletonThreadPool / NullPool semantics under aiosqlite, and
# pool_size/max_overflow are silently ignored.
engine = create_async_engine(
    settings.database_url,
    echo=settings.sqlalchemy_echo,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_wal(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

- [ ] **Step 4: Update `database_url` default in config**

Open `backend/app/core/config.py` and change line 19:

```python
    # OLD
    database_url: str = "postgresql+asyncpg://reelpush:reelpush@localhost:5432/reelpush"
    # NEW
    database_url: str = "sqlite+aiosqlite:///./reelpush.db"
```

(Production server `.env` will override with the absolute path `/home/ubuntu/reelpush-backend/data/reelpush.db`.)

- [ ] **Step 5: Re-run the test**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_db_session.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/session.py backend/app/core/config.py backend/tests/test_db_session.py
git commit -m "db: switch to aiosqlite with WAL/foreign_keys/synchronous=NORMAL

WAL mode enables single-process concurrent readers + one writer, which is
exactly the APScheduler + request-handler shape this app uses.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Drop Celery/Redis settings, add `public_base_url` and `media_url_ttl_seconds`

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Apply edits**

In `backend/app/core/config.py`:

Delete lines 21–24 (the Redis/Celery block):

```python
    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
```

Add these fields just below the existing `api_url` block (around line 41):

```python
    # Public-facing URL the Mac client and OAuth providers reach.
    # Production: https://<reelpush-host>.duckdns.org
    # Dev:        http://localhost:8001
    public_base_url: str = "http://localhost:8001"

    # How long /media/<token> URLs stay valid. Instagram needs the URL live
    # only briefly while their fetcher pulls it; an hour is generous.
    media_url_ttl_seconds: int = 3600

    # Signing secret for /media/<token>. Falls back to secret_key but
    # can be rotated independently if needed.
    media_signing_key: str = ""
```

Add helper property at the bottom of the class (after `youtube_configured`):

```python
    @property
    def effective_media_signing_key(self) -> str:
        return self.media_signing_key or self.secret_key
```

- [ ] **Step 2: Update `.env.example`**

Open `.env.example` (repo root) and:
- Delete the `Redis / Celery` block (lines 9-12 in the current file)
- Add under "Local API URL":

```bash
# Public base URL for OAuth callbacks, /media/<token>, Mac client API_URL.
# Server: https://<reelpush-host>.duckdns.org
# Dev:    http://localhost:8001
PUBLIC_BASE_URL=http://localhost:8001

# Token lifetime for signed media URLs (seconds).
MEDIA_URL_TTL_SECONDS=3600

# Optional separate signing key for /media/<token>; falls back to SECRET_KEY.
MEDIA_SIGNING_KEY=
```

- [ ] **Step 3: Verify config loads**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "
from app.core.config import get_settings
s = get_settings()
assert s.public_base_url == 'http://localhost:8001', s.public_base_url
assert s.media_url_ttl_seconds == 3600
assert s.effective_media_signing_key == s.secret_key
assert not hasattr(s, 'redis_url'), 'redis_url should be gone'
print('config OK')
"
```

Expected: `config OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/config.py .env.example
git commit -m "config: drop redis/celery settings; add public_base_url + signed media URL config

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Create `app/workers/scheduler.py` (APScheduler in-process)

**Files:**
- Create: `backend/app/workers/scheduler.py`
- Create: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_scheduler.py`:

```python
"""Verify APScheduler module: start, stop, register interval job."""
import asyncio
import pytest

from app.workers.scheduler import (
    get_scheduler,
    start_scheduler,
    stop_scheduler,
    schedule_publish_job,
    register_periodic_jobs,
)


@pytest.mark.asyncio
async def test_start_stop_idempotent():
    await start_scheduler()
    sched = get_scheduler()
    assert sched.running
    await start_scheduler()  # second call must not raise
    await stop_scheduler()
    assert not sched.running
    await stop_scheduler()  # second stop must not raise


@pytest.mark.asyncio
async def test_schedule_publish_job_adds_one_off_job():
    await start_scheduler()
    try:
        from datetime import datetime, timedelta, timezone
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        job = schedule_publish_job(job_id="test-job-1", when=when)
        assert job.id == "publish:test-job-1"
        # idempotent: scheduling same job_id replaces the previous one
        job2 = schedule_publish_job(job_id="test-job-1", when=when)
        assert job2.id == "publish:test-job-1"
    finally:
        await stop_scheduler()


@pytest.mark.asyncio
async def test_register_periodic_jobs_registers_poller():
    await start_scheduler()
    try:
        register_periodic_jobs()
        sched = get_scheduler()
        assert sched.get_job("periodic:poll_scheduled_jobs") is not None
    finally:
        await stop_scheduler()
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_scheduler.py -v
```

Expected: `ImportError: cannot import name 'get_scheduler' from 'app.workers.scheduler'`

- [ ] **Step 3: Implement `scheduler.py`**

Create `backend/app/workers/scheduler.py`:

```python
"""
APScheduler — in-process job runner.

Replaces the previous Celery worker + beat. Runs inside the FastAPI
process; lifespan hooks in app.main start/stop it. Single source of truth
for both one-off (publish) and periodic (poll) job scheduling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.logging import get_logger

_logger = get_logger("scheduler")
_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        _logger.info("scheduler_started")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _logger.info("scheduler_stopped")


def schedule_publish_job(job_id: str, when: datetime):
    """
    Schedule one publish run at `when`. Replaces any existing schedule
    for the same job_id (idempotent). Job ID format: "publish:<job_id>".
    """
    from app.workers.tasks import run_publish_job  # late import avoids cycle

    sched = get_scheduler()
    return sched.add_job(
        run_publish_job,
        trigger=DateTrigger(run_date=when),
        args=[job_id],
        id=f"publish:{job_id}",
        replace_existing=True,
        misfire_grace_time=300,
    )


def register_periodic_jobs() -> None:
    """
    Register all periodic jobs that were previously celery-beat-driven.
    Called once during app startup (after start_scheduler).
    """
    from app.workers.tasks import poll_scheduled_jobs  # late import

    sched = get_scheduler()
    sched.add_job(
        poll_scheduled_jobs,
        trigger=IntervalTrigger(minutes=1),
        id="periodic:poll_scheduled_jobs",
        replace_existing=True,
        misfire_grace_time=60,
    )
    _logger.info("periodic_jobs_registered")
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_scheduler.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/scheduler.py backend/tests/test_scheduler.py
git commit -m "workers: add APScheduler module replacing celery worker+beat

Single AsyncIOScheduler instance, lifespan-managed, supports one-off
publish jobs and the every-minute poller.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Strip Celery decorators from `app/workers/tasks.py`

**Files:**
- Modify: `backend/app/workers/tasks.py`
- Delete: `backend/app/workers/celery_app.py`

**Approach:** This is a substantive rewrite (273 lines, 3 task entry points). Use Codex per CLAUDE.md. Claude reviews the diff before commit.

- [ ] **Step 1: Read current file end-to-end**

```bash
cat "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/workers/tasks.py"
```

Note the three Celery entry points: `run_publish_job(self, job_id)` at line 40 (decorated `@celery_app.task(bind=True, ...)`), `poll_scheduled_jobs()` at line 227 (decorated `@celery_app.task(...)`), and `_run_async(coro)` helper at line 27 that bridges sync Celery to async code. After this task, only async functions remain — the sync bridges are deleted.

- [ ] **Step 2: Dispatch Codex with this prompt**

```bash
codex exec -m gpt-5.5 -c model_reasoning_effort="high" \
  --cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" <<'PROMPT'
Project: ReelPush
Goal: Strip Celery from backend/app/workers/tasks.py while preserving all business logic.
Folder: /Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac

Inspect first: backend/app/workers/tasks.py, backend/app/workers/celery_app.py, backend/app/workers/scheduler.py
Edit only: backend/app/workers/tasks.py
Do not touch: backend/app/services/*, backend/app/api/*, anything outside backend/app/workers/

Constraints:
- Remove the @celery_app.task decorators on run_publish_job and poll_scheduled_jobs.
- Remove `from celery import ...`, the `_run_async` helper, and any Task/self argument.
- run_publish_job becomes `async def run_publish_job(job_id: str) -> dict:` — the body of execute_publish_job moves into it (or run_publish_job calls execute_publish_job directly).
- poll_scheduled_jobs becomes `async def poll_scheduled_jobs() -> dict:` — direct async, no _run_async wrapper.
- Inside _do_poll_scheduled_jobs (line 236), replace the call `run_publish_job.apply_async(args=[job_id], countdown=...)` with `from app.workers.scheduler import schedule_publish_job; schedule_publish_job(job_id=job_id, when=<utc datetime when run>)`.
- Inside any retry logic that used Celery's `self.retry(...)`, replace with `from app.workers.scheduler import schedule_publish_job; schedule_publish_job(job_id=job_id, when=datetime.now(timezone.utc) + timedelta(seconds=delay))` and `return` from the function.
- Preserve every other line of business logic exactly: the publishing flow, error handling, logging, status updates.
- Keep `_summarise(raw)` helper unchanged.

Rules:
1. Inspect before editing.
2. Match existing code style (structlog logging, async DB access via AsyncSessionLocal).
3. Smallest safe change beyond the Celery removal.
4. Run: cd backend && /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/ -v
5. Do not commit.
6. Do not hide failures.

Report: files changed, full diff of tasks.py, test output, anything that surprised you.
PROMPT
```

- [ ] **Step 3: Claude reviews the diff**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
git diff backend/app/workers/tasks.py | head -200
```

Review checklist:
- [ ] No remaining `import celery` / `from celery` lines
- [ ] No `@celery_app.task` decorators
- [ ] `run_publish_job` is `async def`, takes only `job_id: str`
- [ ] `poll_scheduled_jobs` is `async def`, no params
- [ ] `apply_async` calls all gone
- [ ] `_run_async` helper gone (or marked unused for follow-up cleanup)
- [ ] No reference to `Task`, `self.retry`, `bind=True`

If any item fails, re-run Codex with a tightened prompt rather than hand-patching (per CLAUDE.md token policy).

- [ ] **Step 4: Delete celery_app.py**

```bash
git rm "backend/app/workers/celery_app.py"
```

- [ ] **Step 5: Run tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/ -v 2>&1 | tail -40
```

Expected: tests previously passing still pass; some Celery-bound tests in `tests/test_core.py` may now fail — handle in Task 12.

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/
git commit -m "workers: remove Celery; tasks.py is plain async functions

run_publish_job and poll_scheduled_jobs are now async-native and dispatched
via AsyncIOScheduler (see scheduler.py).
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Update `publish_service.py` to use scheduler instead of Celery

**Files:**
- Modify: `backend/app/services/publish_service.py`

- [ ] **Step 1: Find the call sites**

```bash
grep -n "tasks.run_publish_job\|apply_async\|task.delay" "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/services/publish_service.py"
```

Expected output: lines ~267 and ~273 (both `tasks.run_publish_job.apply_async(...)`).

- [ ] **Step 2: Read the surrounding context**

```bash
sed -n '255,285p' "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/services/publish_service.py"
```

Note the `countdown` / `eta` semantics being passed to `apply_async`.

- [ ] **Step 3: Apply the edit**

In `backend/app/services/publish_service.py`:

Replace the import block at the top — find the current `from app.workers import tasks` (or equivalent) and change to:

```python
from datetime import datetime, timedelta, timezone
from app.workers.scheduler import schedule_publish_job
```

Replace each `apply_async` call. The shape changes from (Celery):

```python
result = tasks.run_publish_job.apply_async(
    args=[job_id],
    countdown=delay_seconds,
)
```

To (APScheduler):

```python
when = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
schedule_publish_job(job_id=job_id, when=when)
```

If a call uses `eta=some_datetime` instead of `countdown`, just pass `when=some_datetime` (ensure it's tz-aware UTC).

If the result of `apply_async` was being read (e.g., `.id`), replace with `f"publish:{job_id}"` since `schedule_publish_job` returns the APScheduler `Job` whose `.id` matches that pattern.

- [ ] **Step 4: Run tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/ -v 2>&1 | tail -40
```

If `tests/test_core.py` references `apply_async` or `celery_app`, those tests fail here. They get fixed in Task 12.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/publish_service.py
git commit -m "publish_service: dispatch via APScheduler instead of Celery

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 10: Move OAuth periodic refresh (if any) to APScheduler

**Files:**
- Modify: `backend/app/services/oauth_service.py` (only if a refresh-task hook exists)
- Modify: `backend/app/workers/scheduler.py` (add registration)

- [ ] **Step 1: Check whether oauth_service has periodic refresh**

```bash
grep -n "periodic\|crontab\|interval\|refresh" "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/services/oauth_service.py" | head -10
grep -rn "celery_app.task\|celery.schedules\|crontab" "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/" | head -10
```

- [ ] **Step 2: If a periodic refresh exists**

Add to `backend/app/workers/scheduler.py` `register_periodic_jobs()` body, before the closing log line:

```python
    from app.workers.tasks import refresh_provider_tokens  # if such function exists
    sched.add_job(
        refresh_provider_tokens,
        trigger=IntervalTrigger(minutes=15),
        id="periodic:refresh_provider_tokens",
        replace_existing=True,
        misfire_grace_time=300,
    )
```

If no such function exists in `tasks.py` after Task 8, **skip this task entirely** — there's nothing to migrate. Token refresh likely happens on-demand inside `oauth_service` already.

- [ ] **Step 3: Tests**

If you added a registration, extend `tests/test_scheduler.py::test_register_periodic_jobs_registers_poller` to also assert `sched.get_job("periodic:refresh_provider_tokens") is not None`.

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_scheduler.py -v
```

- [ ] **Step 4: Commit (only if changes made)**

```bash
git add backend/app/workers/scheduler.py backend/tests/test_scheduler.py
git commit -m "scheduler: register periodic provider-token refresh

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 11: Wire scheduler into FastAPI lifespan

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Apply edits to `main.py`**

Replace the `lifespan` function and import block. Open `backend/app/main.py` and change:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, jobs, oauth, uploads, workspace
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

settings = get_settings()
setup_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("reelpush_starting", environment=settings.environment)
    # Ensure local storage directories exist
    if settings.storage_backend == "local":
        for sub in ("uploads", "thumbnails"):
            Path(settings.local_storage_path, sub).mkdir(parents=True, exist_ok=True)
    yield
    logger.info("reelpush_shutdown")
```

To:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.routes import auth, jobs, media, oauth, uploads, workspace
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.workers.scheduler import register_periodic_jobs, start_scheduler, stop_scheduler

settings = get_settings()
setup_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("reelpush_starting", environment=settings.environment)
    if settings.storage_backend == "local":
        for sub in ("uploads", "thumbnails"):
            Path(settings.local_storage_path, sub).mkdir(parents=True, exist_ok=True)
    await start_scheduler()
    register_periodic_jobs()
    try:
        yield
    finally:
        await stop_scheduler()
        logger.info("reelpush_shutdown")
```

Then **delete** the `from fastapi.staticfiles import StaticFiles` import and the entire local-media static-mount block (currently lines 48–52):

```python
# ─── Media serving (local dev only) ───────────────────────────────────────────
if settings.storage_backend == "local":
    storage_path = Path(settings.local_storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(storage_path)), name="media")
```

…and add this single line in the routes block (alongside the other `app.include_router` calls):

```python
app.include_router(media.router, prefix="")  # /media/{token}, no /api prefix
```

(The existing `app.include_router(...)` calls use `prefix="/api"` so the media route stays at the root path which IG/providers fetch.)

- [ ] **Step 2: Verify import (will fail until Task 13 creates media route)**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "from app import main"
```

Expected: `ImportError: cannot import name 'media' from 'app.api.routes'` — that's intentional; Task 13 fixes it.

- [ ] **Step 3: No commit yet** — wait until Task 13 lands so the tree imports cleanly.

---

### Task 12: Add signed-URL helper to `media_service.py`

**Files:**
- Modify: `backend/app/services/media_service.py`
- Create: `backend/tests/test_signed_media_url.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_signed_media_url.py`:

```python
"""TDD for signed /media/<token> URL helper."""
import pytest
from itsdangerous import BadSignature

from app.core.config import get_settings
from app.services.media_service import MediaService, sign_media_token, verify_media_token


def test_sign_round_trip():
    storage_key = "uploads/abc123.mp4"
    token = sign_media_token(storage_key)
    decoded = verify_media_token(token)
    assert decoded == storage_key


def test_sign_rejects_tampered_token():
    storage_key = "uploads/abc123.mp4"
    token = sign_media_token(storage_key)
    tampered = token[:-2] + ("xx" if not token.endswith("xx") else "yy")
    with pytest.raises(BadSignature):
        verify_media_token(tampered)


def test_sign_expires():
    import time
    storage_key = "uploads/abc123.mp4"
    # Use a tiny TTL to test expiry
    token = sign_media_token(storage_key, ttl_seconds=1)
    time.sleep(2)
    with pytest.raises(BadSignature):
        verify_media_token(token)


def test_make_signed_url_returns_full_url():
    settings = get_settings()
    svc = MediaService()
    url = svc.make_signed_url("uploads/abc123.mp4")
    assert url.startswith(settings.public_base_url + "/media/")
    token = url.rsplit("/", 1)[-1]
    assert verify_media_token(token) == "uploads/abc123.mp4"
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_signed_media_url.py -v
```

Expected: ImportError — `sign_media_token` doesn't exist.

- [ ] **Step 3: Add the helpers to `media_service.py`**

At the top of `backend/app/services/media_service.py`, add imports:

```python
from itsdangerous import URLSafeTimedSerializer, BadSignature

from app.core.config import get_settings
```

(if `get_settings` import already exists, leave it alone)

Add module-level functions before the `MediaService` class definition (near the top, after the existing `MediaValidationError`):

```python
_SERIALIZER_SALT = "reelpush.media.url.v1"


def _get_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(
        secret_key=settings.effective_media_signing_key,
        salt=_SERIALIZER_SALT,
    )


def sign_media_token(storage_key: str, ttl_seconds: int | None = None) -> str:
    """Return an itsdangerous-signed token encoding the storage_key.

    The token is the entire opaque path segment in /media/<token>.
    """
    return _get_serializer().dumps({"k": storage_key, "ttl": ttl_seconds})


def verify_media_token(token: str) -> str:
    """Verify token; return the encoded storage_key.

    Raises itsdangerous.BadSignature on tampering or expiry.
    """
    settings = get_settings()
    payload = _get_serializer().loads(
        token,
        max_age=settings.media_url_ttl_seconds,
    )
    # If a per-token override TTL was set at sign-time, honor the smaller window.
    if isinstance(payload, dict):
        if payload.get("ttl") is not None:
            # Re-verify with the smaller max_age
            _get_serializer().loads(token, max_age=int(payload["ttl"]))
        return str(payload["k"])
    return str(payload)
```

Add to the `MediaService` class:

```python
    def make_signed_url(self, storage_key: str) -> str:
        """Public HTTPS URL for /media/<token>.
        Used for Instagram Container API and Mac client display."""
        settings = get_settings()
        token = sign_media_token(storage_key)
        return f"{settings.public_base_url}/media/{token}"
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_signed_media_url.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media_service.py backend/tests/test_signed_media_url.py
git commit -m "media: add itsdangerous-signed /media/<token> URL helpers

sign_media_token + verify_media_token + MediaService.make_signed_url.
Used by uploads route and Instagram Container API publishing path.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 13: Add `/media/{token}` route

**Files:**
- Create: `backend/app/api/routes/media.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_signed_media_url.py`:

```python
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_media_route_serves_file_for_valid_token(monkeypatch, tmp_path):
    # Set up a fake storage dir with a known file.
    fake_root = tmp_path / "storage"
    (fake_root / "uploads").mkdir(parents=True)
    file_path = fake_root / "uploads" / "video.mp4"
    file_path.write_bytes(b"FAKE-VIDEO-BYTES")

    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(fake_root))
    # Force re-read of settings
    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.main import app
    from app.services.media_service import sign_media_token

    token = sign_media_token("uploads/video.mp4")
    with TestClient(app) as client:
        r = client.get(f"/media/{token}")
        assert r.status_code == 200
        assert r.content == b"FAKE-VIDEO-BYTES"


def test_media_route_404_for_bad_token(tmp_path):
    from app.main import app
    with TestClient(app) as client:
        r = client.get("/media/this-is-not-a-valid-token")
        assert r.status_code == 404


def test_media_route_404_for_traversal_attempt():
    """Token decodes to a path with .. — must be rejected."""
    from app.services.media_service import sign_media_token
    from app.main import app

    token = sign_media_token("uploads/../../../etc/passwd")
    with TestClient(app) as client:
        r = client.get(f"/media/{token}")
        assert r.status_code == 404
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_signed_media_url.py::test_media_route_serves_file_for_valid_token -v
```

Expected: ImportError or 404 on every call.

- [ ] **Step 3: Implement `app/api/routes/media.py`**

```python
"""
Public /media/<token> route.

Serves files from local_storage_path, gated by an itsdangerous-signed token.
Tokens encode the storage_key (relative path inside storage). Used by:
- Instagram Container API (must fetch over public HTTPS)
- Mac client preview/playback
- YouTube/TikTok upload paths if they fetch URLs (they typically don't)
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from itsdangerous import BadSignature

from app.core.config import get_settings
from app.services.media_service import verify_media_token

router = APIRouter(tags=["media"])


@router.get("/media/{token}")
async def serve_media(token: str):
    settings = get_settings()
    try:
        storage_key = verify_media_token(token)
    except BadSignature:
        raise HTTPException(status_code=404, detail="not found")

    # Defense in depth: reject any storage_key that escapes the storage root.
    storage_root = Path(settings.local_storage_path).resolve()
    candidate = (storage_root / storage_key).resolve()
    try:
        candidate.relative_to(storage_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")

    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(str(candidate))
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_signed_media_url.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/media.py backend/tests/test_signed_media_url.py
git commit -m "routes: add /media/{token} with itsdangerous signature + path-traversal guard

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 14: Update OAuth routes to use `public_base_url`

**Files:**
- Modify: `backend/app/api/routes/oauth.py`

- [ ] **Step 1: Find hardcoded URLs**

```bash
grep -n "localhost:8100\|localhost:8000\|api_url\|API_URL" "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/api/routes/oauth.py"
```

- [ ] **Step 2: Read and patch redirect-URI construction**

For every line that constructs an OAuth redirect URI for *the provider's* purposes (the URL the provider will hit), replace `settings.api_url` (or any hardcoded value) with `settings.public_base_url`.

This affects roughly the YouTube, Instagram, and TikTok start/callback handlers. Search for patterns like:

```python
redirect_uri = f"{settings.api_url}/api/oauth/youtube/callback"
```

Replace with:

```python
redirect_uri = f"{settings.public_base_url}/api/oauth/youtube/callback"
```

Do **not** replace `api_url` references that drive *Mac-client-facing* URLs in response bodies — only the redirect URIs used for OAuth provider callbacks.

If a single `api_url` reference is genuinely both, leave it as `api_url` (since `api_url` defaults to localhost too) and document the dev/prod swap as an env var override.

- [ ] **Step 3: Tests**

If any OAuth test asserts the redirect URI shape, update assertions:

```bash
grep -rn "redirect_uri\|api_url" "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/tests/" | head -20
```

Run the full suite:

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/ -v 2>&1 | tail -30
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes/oauth.py backend/tests
git commit -m "oauth: use public_base_url for provider-facing redirect URIs

Decouples OAuth callback hostname from local dev API_URL. Production
will set PUBLIC_BASE_URL=https://<reelpush-host>.duckdns.org.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 15: Update uploads route to return signed URL

**Files:**
- Modify: `backend/app/api/routes/uploads.py`
- Modify: `backend/app/schemas/schemas.py` (if `UploadOut.url` field needs updating)

- [ ] **Step 1: Read current upload response**

```bash
sed -n '15,55p' "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/api/routes/uploads.py"
```

- [ ] **Step 2: Patch the response builder**

Inside the `upload_video` handler (line 19 area), after the file is saved, replace the line that constructs the URL (likely `f"{settings.api_url}/media/{key}"` or a local path) with:

```python
public_url = media_service.make_signed_url(storage_key)
```

…and ensure the `UploadOut` response includes `url=public_url`.

If `UploadOut` schema in `schemas.py` doesn't already have a `url: str` field, add it.

- [ ] **Step 3: Test**

Either add a test or extend an existing upload test:

```python
def test_upload_returns_signed_url(authenticated_client, tmp_path):
    # …existing upload setup…
    r = authenticated_client.post("/api/uploads/", files={"file": ("test.mp4", b"x", "video/mp4")})
    assert r.status_code == 201
    body = r.json()
    assert body["url"].startswith("http")
    assert "/media/" in body["url"]
```

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/ -v -k upload
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes/uploads.py backend/app/schemas/schemas.py backend/tests
git commit -m "uploads: return signed /media/<token> URL on upload

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 16: Update tests/conftest.py for SQLite + scrub Celery references

**Files:**
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_core.py` (drop Celery-only tests)

- [ ] **Step 1: Read current conftest**

```bash
cat "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/tests/conftest.py"
```

- [ ] **Step 2: Ensure conftest sets SQLite test URL**

Open `backend/tests/conftest.py` and ensure it sets `DATABASE_URL` to a tmp SQLite before any app import. Keep the existing `magic` stub. Example structure:

```python
import os
import sys
import tempfile
from unittest.mock import MagicMock

# Stub libmagic on hosts where it's not installed.
if "magic" not in sys.modules:
    sys.modules["magic"] = MagicMock()

# Force SQLite for tests, isolated per session.
_test_db_dir = tempfile.mkdtemp(prefix="reelpush-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_test_db_dir}/test.db")
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:8001")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-prod")
os.environ.setdefault("LOCAL_STORAGE_PATH", _test_db_dir)
```

- [ ] **Step 3: Drop Celery-coupled assertions in test_core.py**

```bash
grep -n "celery\|apply_async\|task.delay\|celery_app" "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/tests/test_core.py"
```

For each match, either:
- Delete the test if it was purely about Celery wiring, OR
- Rewrite it to assert APScheduler equivalents (e.g., assert `scheduler.get_job("publish:<id>") is not None`)

- [ ] **Step 4: Run full test suite**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/ -v
```

Expected: all tests PASS or skipped (no failures, no errors). If failures remain, fix in this task before committing.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_core.py
git commit -m "tests: configure SQLite test DB + remove Celery-coupled assertions

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 17: Initialize Alembic for SQLite

**Files:**
- Create: `backend/app/db/migrations/` (via `alembic init`)
- Modify: `backend/app/db/migrations/env.py`
- Create: `backend/app/db/migrations/versions/<hash>_initial.py`

- [ ] **Step 1: Initialize alembic**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m alembic init app/db/migrations
```

This populates `app/db/migrations/` with `env.py`, `script.py.mako`, `versions/`.

- [ ] **Step 2: Patch env.py to use Settings + async**

Open `backend/app/db/migrations/env.py` and replace its body with:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.session import Base
from app.models import models  # noqa: F401  ensure models are imported

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the URL from settings (env-driven).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER support
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 3: Generate baseline migration**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m alembic revision --autogenerate -m "initial sqlite baseline"
```

This creates `app/db/migrations/versions/<hash>_initial_sqlite_baseline.py`. Inspect the generated file:

```bash
ls "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/db/migrations/versions/"
cat "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend/app/db/migrations/versions/"*_initial_sqlite_baseline.py
```

Verify the migration creates all expected tables (users, accounts, jobs, uploads, etc., per `backend/app/models/models.py`). If anything's missing, the `from app.models import models` import in `env.py` may have failed — fix imports and regenerate.

- [ ] **Step 4: Apply migration to test DB and verify**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
DATABASE_URL=sqlite+aiosqlite:///./test_migrate.db /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m alembic upgrade head
ls -la test_migrate.db
sqlite3 test_migrate.db ".tables"
rm test_migrate.db
```

Expected: tables match `models.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/migrations backend/alembic.ini
git commit -m "db: initialize alembic for SQLite with async + render_as_batch

render_as_batch=True is required for SQLite ALTER TABLE support.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 18: Local backend smoke test

**Files:**
- (Verification only; no edits)

- [ ] **Step 1: Set local env**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
cat > .env <<'EOF'
SECRET_KEY=local-dev-only-do-not-deploy
DATABASE_URL=sqlite+aiosqlite:///./reelpush.db
PUBLIC_BASE_URL=http://localhost:8001
API_URL=http://localhost:8001
LOCAL_STORAGE_PATH=./storage
STORAGE_BACKEND=local
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=changeme123!
ENVIRONMENT=development
LOG_LEVEL=INFO
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
EOF
```

(This .env is gitignored; safe.)

- [ ] **Step 2: Migrate**

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m alembic upgrade head
```

- [ ] **Step 3: Seed admin**

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 scripts/seed_admin.py
```

- [ ] **Step 4: Run uvicorn**

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

In another terminal, hit endpoints:

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
# Expected: {"status": "ok", "environment": "development"}

curl -s -X POST http://localhost:8001/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@example.com","password":"changeme123!"}' | python3 -m json.tool
# Expected: {"access_token": "...", "token_type": "bearer"} (or similar — match existing auth route shape)
```

- [ ] **Step 5: Verify scheduler is running in logs**

The uvicorn process should log:
- `reelpush_starting environment=development`
- `scheduler_started`
- `periodic_jobs_registered`

If `scheduler_started` is missing, the lifespan wiring from Task 11 didn't take. Debug there.

- [ ] **Step 6: Stop uvicorn**

Ctrl-C the uvicorn process. The logs should show `scheduler_stopped` then `reelpush_shutdown`.

- [ ] **Step 7: No commit** — smoke verification only.

---

## Phase 2-Mac: Mac client refactor

### Task 19: Create `reelpush_client/` package + config module

**Files:**
- Create: `reelpush_client/__init__.py`
- Create: `reelpush_client/config.py`
- Create: `reelpush_client_requirements.txt`

- [ ] **Step 1: Create the package**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
mkdir -p reelpush_client
touch reelpush_client/__init__.py
```

- [ ] **Step 2: Write `config.py`**

Create `reelpush_client/config.py`:

```python
"""Mac client config loader.

Reads from ~/Library/Application Support/ReelPush/config.json with
REELPUSH_API_URL env override for local dev. No provider secrets ever
land here — those live only on the server.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_API_URL = "https://reelpush-host.duckdns.org"  # placeholder; overridden in production


@dataclass
class ClientConfig:
    api_url: str = DEFAULT_API_URL
    developer_mode: bool = False


def _config_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "ReelPush"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def load() -> ClientConfig:
    path = _config_path()
    if not path.exists():
        return ClientConfig(api_url=os.environ.get("REELPUSH_API_URL", DEFAULT_API_URL))
    raw = json.loads(path.read_text())
    cfg = ClientConfig(
        api_url=raw.get("api_url", DEFAULT_API_URL),
        developer_mode=bool(raw.get("developer_mode", False)),
    )
    env_override = os.environ.get("REELPUSH_API_URL")
    if env_override:
        cfg.api_url = env_override
    return cfg


def save(cfg: ClientConfig) -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)
    _config_path().write_text(json.dumps(asdict(cfg), indent=2))
```

- [ ] **Step 3: Create `reelpush_client_requirements.txt`**

```
httpx==0.27.0
keyring==24.3.1
tenacity==8.3.0
```

- [ ] **Step 4: Install Mac-side deps**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install -r reelpush_client_requirements.txt
```

- [ ] **Step 5: Smoke**

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "
from reelpush_client import config
c = config.load()
print('api_url:', c.api_url)
print('dev:', c.developer_mode)
"
```

- [ ] **Step 6: Commit**

```bash
git add reelpush_client/__init__.py reelpush_client/config.py reelpush_client_requirements.txt
git commit -m "client: add reelpush_client package + config loader

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 20: Implement `ServerAPI` httpx wrapper

**Files:**
- Create: `reelpush_client/api.py`
- Create: `tests/test_reelpush_client_api.py` (Mac-side test)

- [ ] **Step 1: Decide test location**

The repo has no `tests/` at the root for Mac code currently — only `backend/tests/`. Create `tests/` at the repo root for Mac client tests so they don't pollute the backend test directory.

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_reelpush_client_api.py`:

```python
"""ServerAPI tests using httpx mock transport — no real network."""

import httpx
import pytest

from reelpush_client.api import ServerAPI, AuthError


class _MockKeychain:
    def __init__(self):
        self.store = {}
    def get_password(self, service, user):
        return self.store.get((service, user))
    def set_password(self, service, user, password):
        self.store[(service, user)] = password
    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture
def keychain():
    return _MockKeychain()


def _transport(handler):
    return httpx.MockTransport(handler)


def test_login_stores_jwt_in_keychain(keychain):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/login"
        assert request.method == "POST"
        return httpx.Response(200, json={"access_token": "JWT123", "token_type": "bearer"})

    api = ServerAPI(api_url="http://x", keychain=keychain, transport=_transport(handler))
    api.login("a@b.com", "pw")
    assert keychain.store[("com.reelpush.app", "default")] == "JWT123"


def test_authenticated_calls_send_bearer(keychain):
    keychain.set_password("com.reelpush.app", "default", "TOKEN")

    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    api = ServerAPI(api_url="http://x", keychain=keychain, transport=_transport(handler))
    api.list_accounts()
    assert captured["auth"] == "Bearer TOKEN"


def test_401_clears_keychain_and_raises_auth_error(keychain):
    keychain.set_password("com.reelpush.app", "default", "OLD")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "expired"})

    api = ServerAPI(api_url="http://x", keychain=keychain, transport=_transport(handler))
    with pytest.raises(AuthError):
        api.list_accounts()
    assert keychain.store.get(("com.reelpush.app", "default")) is None
```

- [ ] **Step 3: Run test, confirm fail**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_reelpush_client_api.py -v
```

Expected: ImportError (api module not yet present)

- [ ] **Step 4: Implement `api.py`**

Create `reelpush_client/api.py`:

```python
"""ServerAPI — httpx wrapper for the ReelPush server backend.

Stores JWT in macOS Keychain (service: com.reelpush.app, account: default).
On 401 → clear keychain and raise AuthError so the UI can prompt re-login.
On 5xx → raise ServerError with response body.
On network errors → tenacity retries with exponential backoff for idempotent calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

KEYCHAIN_SERVICE = "com.reelpush.app"
KEYCHAIN_ACCOUNT = "default"


class AuthError(Exception):
    """Raised when the server rejects the JWT (401)."""


class ServerError(Exception):
    """Raised when the server returns 5xx."""


def _real_keychain():
    import keyring
    return keyring


class ServerAPI:
    def __init__(
        self,
        api_url: str,
        keychain=None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.api_url = api_url.rstrip("/")
        self._keychain = keychain or _real_keychain()
        self._client = httpx.Client(
            base_url=self.api_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )

    # ───── token storage ─────

    def _get_token(self) -> Optional[str]:
        return self._keychain.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)

    def _set_token(self, token: str) -> None:
        self._keychain.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token)

    def _clear_token(self) -> None:
        self._keychain.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)

    def is_authenticated(self) -> bool:
        return self._get_token() is not None

    # ───── auth ─────

    def login(self, email: str, password: str) -> None:
        r = self._client.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            raise AuthError(r.text)
        self._set_token(r.json()["access_token"])

    def logout(self) -> None:
        self._clear_token()

    # ───── core request helper ─────

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = self._get_token()
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = self._client.request(method, path, headers=headers, **kwargs)
        if r.status_code == 401:
            self._clear_token()
            raise AuthError("session expired — please log in again")
        if 500 <= r.status_code < 600:
            raise ServerError(f"{r.status_code}: {r.text[:500]}")
        return r

    # ───── domain methods ─────

    def me(self) -> dict[str, Any]:
        r = self._request("GET", "/api/auth/me")
        r.raise_for_status()
        return r.json()

    def list_accounts(self) -> list[dict[str, Any]]:
        r = self._request("GET", "/api/oauth/accounts")
        r.raise_for_status()
        return r.json()

    def start_oauth(self, provider: str) -> str:
        return f"{self.api_url}/api/oauth/{provider}/start"

    def upload_media(self, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path)
        with path.open("rb") as fh:
            r = self._request(
                "POST",
                "/api/uploads/",
                files={"file": (path.name, fh, "video/mp4")},
            )
        r.raise_for_status()
        return r.json()

    def create_publish_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._request("POST", "/api/jobs/", json=payload)
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.NetworkError),
        reraise=True,
    )
    def get_job(self, job_id: str) -> dict[str, Any]:
        r = self._request("GET", f"/api/jobs/{job_id}")
        r.raise_for_status()
        return r.json()

    def list_jobs(self) -> list[dict[str, Any]]:
        r = self._request("GET", "/api/jobs/")
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 5: Run tests**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest tests/test_reelpush_client_api.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add reelpush_client/api.py tests/__init__.py tests/test_reelpush_client_api.py
git commit -m "client: add ServerAPI with JWT-in-Keychain + tenacity retries

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 21: Login window UI

**Files:**
- Create: `reelpush_client/auth.py`

- [ ] **Step 1: Implement**

Create `reelpush_client/auth.py`:

```python
"""Login window for first-launch JWT acquisition."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from reelpush_client.api import AuthError, ServerAPI


class LoginWindow(tk.Toplevel):
    """Modal login dialog. Sets self.success = True on successful login."""

    def __init__(self, parent: tk.Misc, api: ServerAPI):
        super().__init__(parent)
        self.title("Sign in to ReelPush")
        self.api = api
        self.success = False
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        frm = ttk.Frame(self, padding=20)
        frm.grid()

        ttk.Label(frm, text="Email").grid(row=0, column=0, sticky="w")
        self.email = ttk.Entry(frm, width=32)
        self.email.grid(row=0, column=1, pady=4)

        ttk.Label(frm, text="Password").grid(row=1, column=0, sticky="w")
        self.password = ttk.Entry(frm, show="•", width=32)
        self.password.grid(row=1, column=1, pady=4)

        ttk.Button(frm, text="Sign in", command=self._submit).grid(row=2, column=1, sticky="e", pady=(12, 0))

        self.email.focus()
        self.bind("<Return>", lambda _: self._submit())

    def _submit(self) -> None:
        email = self.email.get().strip()
        password = self.password.get()
        if not email or not password:
            messagebox.showwarning("Sign in", "Email and password required.", parent=self)
            return
        try:
            self.api.login(email, password)
        except AuthError as e:
            messagebox.showerror("Sign in failed", str(e), parent=self)
            return
        except Exception as e:
            messagebox.showerror("Sign in failed", f"Server unreachable: {e}", parent=self)
            return
        self.success = True
        self.destroy()


def prompt_login_if_needed(parent: tk.Misc, api: ServerAPI) -> bool:
    """Show login window if not authenticated. Returns True if logged in."""
    if api.is_authenticated():
        return True
    win = LoginWindow(parent, api)
    parent.wait_window(win)
    return win.success
```

- [ ] **Step 2: Manual smoke (optional but recommended)**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "
import tkinter as tk
from reelpush_client.api import ServerAPI
from reelpush_client.auth import LoginWindow

root = tk.Tk()
root.geometry('1x1')  # tiny invisible parent
api = ServerAPI(api_url='http://localhost:8001')
win = LoginWindow(root, api)
root.wait_window(win)
print('success:', win.success)
"
```

(Requires the local backend running from Task 18.)

- [ ] **Step 3: Commit**

```bash
git add reelpush_client/auth.py
git commit -m "client: add LoginWindow + prompt_login_if_needed helper

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 22: OAuth browser-flow helper

**Files:**
- Create: `reelpush_client/oauth_browser.py`

- [ ] **Step 1: Implement**

Create `reelpush_client/oauth_browser.py`:

```python
"""Open OAuth in system browser; poll for completion."""

from __future__ import annotations

import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from reelpush_client.api import ServerAPI


def connect_provider(parent: tk.Misc, api: ServerAPI, provider: str, timeout_seconds: int = 180) -> bool:
    """Open browser to /api/oauth/{provider}/start and wait until the
    account appears in /api/oauth/accounts. Returns True on success."""

    initial = {a.get("provider") + ":" + str(a.get("id")) for a in api.list_accounts() if a.get("provider") == provider}
    webbrowser.open(api.start_oauth(provider))

    win = tk.Toplevel(parent)
    win.title(f"Connect {provider.title()}")
    win.transient(parent)
    win.grab_set()
    ttk.Label(win, text=f"Complete the {provider} sign-in in your browser…", padding=20).grid()
    progress = ttk.Progressbar(win, mode="indeterminate")
    progress.grid(padx=20, pady=(0, 10), sticky="ew")
    progress.start(50)
    cancelled = {"v": False}
    ttk.Button(win, text="Cancel", command=lambda: (cancelled.update(v=True), win.destroy())).grid(pady=(0, 16))

    deadline = time.time() + timeout_seconds
    success = False
    try:
        while time.time() < deadline and not cancelled["v"]:
            try:
                current = api.list_accounts()
            except Exception:
                current = []
            new = {a.get("provider") + ":" + str(a.get("id")) for a in current if a.get("provider") == provider}
            if new - initial:
                success = True
                break
            parent.update()  # let Tk process events
            time.sleep(2)
    finally:
        if win.winfo_exists():
            progress.stop()
            win.destroy()

    if not success and not cancelled["v"]:
        messagebox.showwarning("Connect", f"Timed out waiting for {provider}.", parent=parent)
    return success
```

- [ ] **Step 2: Commit**

```bash
git add reelpush_client/oauth_browser.py
git commit -m "client: add browser-OAuth helper that polls /api/oauth/accounts

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 23: Rewire `reelpush_desktop.py` to use `ServerAPI`

**Files:**
- Modify: `reelpush_desktop.py` (4500+ lines)
- Delete: `reelpush_desktop.py.bak`

**Approach:** This is a massive surface change across one ~173 KB file. Use Codex with high effort. Claude reviews the diff in chunks.

- [ ] **Step 1: Map every call site to be replaced**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
grep -n "graph.facebook.com\|googleapis.com\|tiktokapis.com\|google.auth\|google_auth\|requests_oauthlib\|dotenv\|load_dotenv\|YOUTUBE_CLIENT_SECRET\|INSTAGRAM_APP_SECRET\|TIKTOK_CLIENT_SECRET" reelpush_desktop.py | head -60
```

This is the call-site inventory. Capture it for Codex.

- [ ] **Step 2: Dispatch Codex**

```bash
codex exec -m gpt-5.5 -c model_reasoning_effort="high" \
  --cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac" <<'PROMPT'
Project: ReelPush
Goal: Convert reelpush_desktop.py from a fat client (does its own OAuth and provider API calls) into a thin client that delegates to the new ServerAPI.
Folder: /Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac

Inspect first:
- reelpush_desktop.py (the entire file)
- reelpush_client/api.py (the new ServerAPI)
- reelpush_client/config.py
- reelpush_client/auth.py
- reelpush_client/oauth_browser.py

Edit only:
- reelpush_desktop.py

Do not touch:
- backend/
- reelpush_client/ (already implemented)
- packaging/, build/, dist/

Constraints:
1. At app startup: load reelpush_client.config, instantiate ServerAPI(api_url=cfg.api_url), then call reelpush_client.auth.prompt_login_if_needed(root, api). If it returns False, exit cleanly.
2. Replace every direct call to graph.facebook.com / googleapis.com / tiktokapis.com / google.auth / requests_oauthlib with the corresponding ServerAPI method:
   - "list connected accounts" → api.list_accounts()
   - "connect Instagram/YouTube/TikTok" → reelpush_client.oauth_browser.connect_provider(root, api, "instagram"|"youtube"|"tiktok")
   - "upload a video" → api.upload_media(path)
   - "publish/queue a job" → api.create_publish_job(payload)
   - "fetch job status" → api.get_job(job_id) or api.list_jobs()
3. Remove every read of YOUTUBE_CLIENT_*, INSTAGRAM_APP_*, TIKTOK_CLIENT_*, refresh tokens, access tokens. The Mac app must contain zero provider secrets.
4. Remove every dotenv.load_dotenv() call. The Mac app reads only its own client config (api_url, developer_mode), never .env.
5. If any code persists provider tokens to a local file or local SQLite, remove that — server is the source of truth.
6. Preserve every UI element, layout, callback wiring, video-preview logic, drag-and-drop, and visual styling. Do not refactor the UI.
7. Add a small "Sign out" menu item that calls api.logout() and re-prompts login.
8. Keep developer_mode handling: if cfg.developer_mode is True, allow REELPUSH_API_URL env override (already handled in config.load(), so just trust the api object).

Rules:
1. Inspect before editing.
2. Match existing code style (tkinter ttk, structured callbacks).
3. Smallest safe change — do not refactor what isn't on the call-site list.
4. After your changes, the file must be importable: python3 -c "import reelpush_desktop"  must succeed without raising.
5. Do not commit.
6. Do not hide failures.

Report:
- Files changed
- Approximate count of replaced call sites
- Any place where the mapping was ambiguous (let the user decide)
- Any provider-side feature that ServerAPI doesn't currently expose (those become follow-up items, not blockers)
PROMPT
```

- [ ] **Step 3: Claude reviews the Codex diff**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
git diff reelpush_desktop.py | head -300
git diff --stat reelpush_desktop.py
```

Review checklist:
- [ ] No remaining `graph.facebook.com`, `googleapis.com`, `tiktokapis.com` strings
- [ ] No `dotenv.load_dotenv()` calls
- [ ] No reads of `YOUTUBE_CLIENT_SECRET`, `INSTAGRAM_APP_SECRET`, `TIKTOK_CLIENT_SECRET`
- [ ] `from reelpush_client...` imports present
- [ ] `prompt_login_if_needed` called at startup
- [ ] UI structure intact (search for the main menu/window classes that exist before)

Spot-check 3 random call sites by reading 20-line context blocks.

- [ ] **Step 4: Smoke import test**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "import reelpush_desktop; print('imports OK')"
```

If this fails, capture the traceback and re-dispatch Codex with the traceback in the prompt.

- [ ] **Step 5: Delete the .bak file**

```bash
git rm reelpush_desktop.py.bak
```

- [ ] **Step 6: Commit**

```bash
git add reelpush_desktop.py
git commit -m "client: rewire reelpush_desktop.py to use ServerAPI; drop direct provider/secret access

All provider OAuth, token storage, and publish API calls now go through the server.
Mac app no longer reads provider client secrets or .env.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 24: End-to-end local smoke (Mac client + local backend)

**Files:**
- (Verification only)

- [ ] **Step 1: Start local backend**

In one terminal:

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/backend"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

- [ ] **Step 2: Configure Mac client to point at local**

```bash
mkdir -p ~/Library/Application\ Support/ReelPush
cat > ~/Library/Application\ Support/ReelPush/config.json <<'EOF'
{
  "api_url": "http://localhost:8001",
  "developer_mode": true
}
EOF
```

- [ ] **Step 3: Launch Mac app**

In a second terminal:

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 reelpush_desktop.py
```

- [ ] **Step 4: Manual smoke**

- App opens to Login window.
- Sign in: `admin@example.com` / `changeme123!`.
- App main window appears.
- Open Keychain Access → confirm a `com.reelpush.app` / `default` entry exists.
- Trigger an upload (any small mp4) → verify upload completes; backend log shows POST /api/uploads/.
- Try Connect Instagram (or any provider) → browser opens to `http://localhost:8001/api/oauth/instagram/start` (will fail since no IG_APP_ID is configured, but the flow should at least open the browser).
- Sign out → JWT removed from Keychain → relaunching prompts login again.

- [ ] **Step 5: No commit** — verification only.

---

## Phase 3: Server deployment

### Task 25: Provision `/home/ubuntu/reelpush-backend/` on the VM

**Files:**
- Create: server-side `/home/ubuntu/reelpush-backend/` tree

- [ ] **Step 1: Create directory tree**

```bash
ssh ubuntu@129.213.126.251 '
set -e
mkdir -p /home/ubuntu/reelpush-backend/data
mkdir -p /home/ubuntu/reelpush-backend/storage/uploads
mkdir -p /home/ubuntu/reelpush-backend/storage/thumbnails
mkdir -p /home/ubuntu/reelpush-backend/logs
chmod 750 /home/ubuntu/reelpush-backend
chmod 700 /home/ubuntu/reelpush-backend/data
chmod 750 /home/ubuntu/reelpush-backend/storage
ls -la /home/ubuntu/reelpush-backend
'
```

Expected: directory listing with correct permissions.

- [ ] **Step 2: Verify Discord bot still healthy**

```bash
ssh ubuntu@129.213.126.251 'systemctl status discordbot --no-pager | head -5'
```

Expected: `Active: active (running)`.

- [ ] **Step 3: No commit** — server-side only.

---

### Task 26: Install Python 3.11 venv on the VM

**Files:**
- Create: `/home/ubuntu/reelpush-backend/venv`

- [ ] **Step 1: Confirm Python availability**

```bash
ssh ubuntu@129.213.126.251 '
python3 --version
which python3
apt list --installed 2>/dev/null | grep -E "python3.[0-9]+ "
'
```

If Ubuntu 22.04 system Python is 3.10 (the default), that's fine — pinned-version is not strictly required. If you prefer 3.11+, install via apt:

```bash
ssh ubuntu@129.213.126.251 '
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip libmagic1 ffmpeg
python3.11 --version
'
```

(`libmagic1` is required by `python-magic`; `ffmpeg` is required for video processing.)

- [ ] **Step 2: Create venv**

```bash
ssh ubuntu@129.213.126.251 '
python3.11 -m venv /home/ubuntu/reelpush-backend/venv
/home/ubuntu/reelpush-backend/venv/bin/python -V
/home/ubuntu/reelpush-backend/venv/bin/python -m pip install --upgrade pip wheel
'
```

If 3.11 wasn't installed, swap `python3.11` → `python3` (system 3.10).

- [ ] **Step 3: No commit**

---

### Task 27: rsync backend code from local to server

**Files:**
- Sync: `backend/` → `/home/ubuntu/reelpush-backend/`

- [ ] **Step 1: Dry-run rsync**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
rsync -avzn \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.db' \
  --exclude='*.db-shm' \
  --exclude='*.db-wal' \
  --exclude='storage/uploads/*' \
  --exclude='storage/thumbnails/*' \
  --exclude='.env' \
  --exclude='.venv*' \
  backend/ ubuntu@129.213.126.251:/home/ubuntu/reelpush-backend/
```

Review the file list. Verify no `.env`, no DBs, no uploads.

- [ ] **Step 2: Real rsync**

Drop the `-n` flag:

```bash
rsync -avz \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.db' \
  --exclude='*.db-shm' \
  --exclude='*.db-wal' \
  --exclude='storage/uploads/*' \
  --exclude='storage/thumbnails/*' \
  --exclude='.env' \
  --exclude='.venv*' \
  backend/ ubuntu@129.213.126.251:/home/ubuntu/reelpush-backend/
```

- [ ] **Step 3: Install Python deps on server**

```bash
ssh ubuntu@129.213.126.251 '
cd /home/ubuntu/reelpush-backend
./venv/bin/pip install -r requirements.txt
./venv/bin/python -c "import fastapi, sqlalchemy, aiosqlite, apscheduler, itsdangerous; print(\"server deps OK\")"
'
```

Expected: `server deps OK`.

- [ ] **Step 4: Verify Discord bot still healthy**

```bash
ssh ubuntu@129.213.126.251 'systemctl status discordbot --no-pager | head -5; git -C /home/ubuntu/discord-bot status'
```

Expected: bot still active; discord-bot git status identical to Phase 1 baseline.

- [ ] **Step 5: No commit on local** — server state changed; no local file changes to commit.

---

### Task 28: Generate server `.env` interactively

**Files:**
- Create: `/home/ubuntu/reelpush-backend/.env`

- [ ] **Step 1: Generate server-only secrets locally on the server**

```bash
ssh ubuntu@129.213.126.251 '
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_PASSWORD=$(openssl rand -hex 16)
echo "Generated SECRET_KEY=$SECRET_KEY"
echo "Generated ADMIN_PASSWORD=$ADMIN_PASSWORD"
echo
echo "(Save the ADMIN_PASSWORD now — you will paste it into the Mac client login.)"
'
```

**Capture both values to a password manager immediately.** They are needed for the next step and (admin password) for Mac login.

- [ ] **Step 2: Open `.env` in nano on the server**

```bash
ssh -t ubuntu@129.213.126.251 'nano /home/ubuntu/reelpush-backend/.env'
```

Paste this template, replacing the two `<...>` values:

```bash
SECRET_KEY=<paste from step 1>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

DATABASE_URL=sqlite+aiosqlite:////home/ubuntu/reelpush-backend/data/reelpush.db

PUBLIC_BASE_URL=https://placeholder.duckdns.org
API_URL=https://placeholder.duckdns.org

STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=/home/ubuntu/reelpush-backend/storage
MEDIA_URL_TTL_SECONDS=3600
MEDIA_SIGNING_KEY=

ADMIN_EMAIL=admin@reelpush.local
ADMIN_PASSWORD=<paste from step 1>

ENVIRONMENT=production
LOG_LEVEL=INFO
SQLALCHEMY_ECHO=false

# Provider credentials — paste from your local .env. Values, not placeholders.
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
```

`PUBLIC_BASE_URL` is a placeholder for now; updated in Phase 4 once DuckDNS is registered. The provider credentials should be copied from `/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac/.env` — read those locally, paste into nano, save.

- [ ] **Step 3: Lock down permissions**

```bash
ssh ubuntu@129.213.126.251 '
chmod 600 /home/ubuntu/reelpush-backend/.env
chown ubuntu:ubuntu /home/ubuntu/reelpush-backend/.env
ls -la /home/ubuntu/reelpush-backend/.env
'
```

Expected: `-rw------- 1 ubuntu ubuntu …`.

- [ ] **Step 4: Sanity-check that .env loads without printing values**

```bash
ssh ubuntu@129.213.126.251 '
cd /home/ubuntu/reelpush-backend
./venv/bin/python -c "
from app.core.config import get_settings
s = get_settings()
# Print ONLY key names and presence, never values
keys = [\"secret_key\", \"database_url\", \"public_base_url\", \"admin_email\", \"youtube_client_id\", \"instagram_app_id\", \"tiktok_client_key\"]
for k in keys:
    v = getattr(s, k)
    print(f\"{k}: {\"<set>\" if v else \"<empty>\"}\")
"
'
```

Expected: every key reports `<set>` (except provider keys you haven't filled yet — those are OK to be `<empty>` until needed).

- [ ] **Step 5: No local commit**

---

### Task 29: Run alembic migrations + seed admin

**Files:**
- Server: `/home/ubuntu/reelpush-backend/data/reelpush.db`

- [ ] **Step 1: Run migrations**

```bash
ssh ubuntu@129.213.126.251 '
cd /home/ubuntu/reelpush-backend
./venv/bin/python -m alembic upgrade head
ls -la data/
'
```

Expected: `reelpush.db` (and a `-shm`/`-wal` file once it's been used).

- [ ] **Step 2: Seed admin**

```bash
ssh ubuntu@129.213.126.251 '
cd /home/ubuntu/reelpush-backend
./venv/bin/python scripts/seed_admin.py
'
```

Expected: confirmation log line; `admin_email` user row in DB.

- [ ] **Step 3: Spot-check the DB**

```bash
ssh ubuntu@129.213.126.251 '
sqlite3 /home/ubuntu/reelpush-backend/data/reelpush.db ".tables"
sqlite3 /home/ubuntu/reelpush-backend/data/reelpush.db "PRAGMA journal_mode;"
'
```

Expected: tables list includes users/accounts/jobs/uploads; journal_mode = `wal`.

- [ ] **Step 4: No commit**

---

### Task 30: Install systemd unit

**Files:**
- Create: `/etc/systemd/system/reelpush-backend.service` (server-side)

- [ ] **Step 1: Write the unit**

```bash
ssh -t ubuntu@129.213.126.251 'sudo nano /etc/systemd/system/reelpush-backend.service'
```

Paste:

```ini
[Unit]
Description=ReelPush FastAPI backend
Documentation=https://github.com/<your-org>/ReelPush
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/reelpush-backend
EnvironmentFile=/home/ubuntu/reelpush-backend/.env
ExecStart=/home/ubuntu/reelpush-backend/venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8001 --workers 1 --proxy-headers
Restart=on-failure
RestartSec=10
MemoryMax=400M
MemoryHigh=300M
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/reelpush-backend
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Note: `ProtectHome=read-only` plus `ReadWritePaths=/home/ubuntu/reelpush-backend` lets uvicorn write to its own dir while leaving the rest of `/home/ubuntu` (and `/home/ubuntu/.env`, the Discord bot) read-only to this unit. The Discord bot's own service is unaffected — these settings apply per-unit.

- [ ] **Step 2: Reload systemd, enable, and start**

```bash
ssh ubuntu@129.213.126.251 '
sudo systemctl daemon-reload
sudo systemctl enable reelpush-backend
sudo systemctl start reelpush-backend
sleep 3
sudo systemctl status reelpush-backend --no-pager
'
```

Expected: `Active: active (running)`. If it failed, examine `journalctl -u reelpush-backend --since -1m`.

- [ ] **Step 3: Verify Discord bot health and untouched state**

```bash
ssh ubuntu@129.213.126.251 '
echo "=== discordbot ==="
sudo systemctl status discordbot --no-pager | head -5
echo
echo "=== discord-bot git status ==="
git -C /home/ubuntu/discord-bot status
'
```

Expected: discordbot still `active (running)`; discord-bot git status identical to Phase 1 baseline (run `diff` against the saved file).

- [ ] **Step 4: Localhost smoke**

```bash
ssh ubuntu@129.213.126.251 'curl -fsS http://127.0.0.1:8001/api/health'
```

Expected: `{"status":"ok","environment":"production"}`.

- [ ] **Step 5: Auth smoke**

```bash
ssh ubuntu@129.213.126.251 '
curl -fsS -X POST http://127.0.0.1:8001/api/auth/login \
  -H "content-type: application/json" \
  -d "{\"email\":\"admin@reelpush.local\",\"password\":\"<paste ADMIN_PASSWORD>\"}" | head -c 200
echo
'
```

Expected: `{"access_token":"...","token_type":"bearer"}`.

- [ ] **Step 6: No local commit**

---

## Phase 4: HTTPS + DNS

### Task 31: Register DuckDNS subdomain

**Files:**
- (Manual: DuckDNS account)

- [ ] **Step 1: Pick a hostname**

Suggested format: `reelpush-<your-handle>.duckdns.org`. Avoid generic names — DuckDNS subdomains are first-come-first-served.

- [ ] **Step 2: User action — register**

This step is manual. The user (or executor) must:
1. Sign in at https://www.duckdns.org with their preferred provider.
2. Add subdomain `<reelpush-host>` (your chosen value).
3. Set the IPv4 to `129.213.126.251`.
4. Note the DuckDNS token (used for renewal).

- [ ] **Step 3: Verify DNS resolves**

```bash
dig +short <reelpush-host>.duckdns.org
```

Expected: `129.213.126.251`. May take 1–2 minutes to propagate.

- [ ] **Step 4: Set up DDNS auto-update on the server**

```bash
ssh -t ubuntu@129.213.126.251 'sudo nano /etc/systemd/system/duckdns-update.service'
```

Paste:

```ini
[Unit]
Description=DuckDNS update

[Service]
Type=oneshot
ExecStart=/bin/bash -c '/usr/bin/curl -fsS "https://www.duckdns.org/update?domains=<reelpush-host>&token=<DUCKDNS_TOKEN>&ip="'
```

```bash
ssh -t ubuntu@129.213.126.251 'sudo nano /etc/systemd/system/duckdns-update.timer'
```

```ini
[Unit]
Description=Run DuckDNS update every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

```bash
ssh ubuntu@129.213.126.251 '
sudo systemctl daemon-reload
sudo systemctl enable --now duckdns-update.timer
sudo systemctl list-timers | grep duckdns
'
```

- [ ] **Step 5: Update PUBLIC_BASE_URL in the server .env**

```bash
ssh -t ubuntu@129.213.126.251 'sudo nano /home/ubuntu/reelpush-backend/.env'
```

Replace the `placeholder.duckdns.org` lines:

```bash
PUBLIC_BASE_URL=https://<reelpush-host>.duckdns.org
API_URL=https://<reelpush-host>.duckdns.org
```

- [ ] **Step 6: Restart backend so it picks up the new env**

```bash
ssh ubuntu@129.213.126.251 '
sudo systemctl restart reelpush-backend
sleep 3
sudo systemctl status reelpush-backend --no-pager | head -5
'
```

- [ ] **Step 7: No local commit**

---

### Task 32: Install Caddy (or wire into existing reverse proxy)

**Decision tree from Phase 1 preflight:**
- Caddy already installed → skip to Task 33
- Only Nginx installed → fall back: install Certbot + add Nginx site (alternative path; not detailed here — request fallback plan if needed)
- Nothing serving 80/443 → install Caddy fresh (this task)

**Files:**
- Modify: server packages

- [ ] **Step 1: Install Caddy via official apt repo**

```bash
ssh ubuntu@129.213.126.251 '
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy
caddy version
'
```

Expected: Caddy 2.x version string.

- [ ] **Step 2: Verify Caddy started**

```bash
ssh ubuntu@129.213.126.251 'sudo systemctl status caddy --no-pager | head -5'
```

Expected: `active (running)`. The default Caddyfile serves `localhost:80` — that's harmless until we change it in Task 33.

- [ ] **Step 3: No local commit**

---

### Task 33: Add ReelPush vhost to Caddyfile

**Files:**
- Modify: `/etc/caddy/Caddyfile` (server-side)

- [ ] **Step 1: Back up current Caddyfile**

```bash
ssh ubuntu@129.213.126.251 'sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date +%s)'
```

- [ ] **Step 2: Append ReelPush block**

```bash
ssh -t ubuntu@129.213.126.251 'sudo nano /etc/caddy/Caddyfile'
```

Append (do NOT remove existing blocks):

```caddyfile
<reelpush-host>.duckdns.org {
    reverse_proxy 127.0.0.1:8001
    encode gzip
    request_body {
        max_size 200MB
    }
    log {
        output file /var/log/caddy/reelpush.log {
            roll_size 10mb
            roll_keep 5
        }
    }
}
```

- [ ] **Step 3: Validate config**

```bash
ssh ubuntu@129.213.126.251 'sudo caddy validate --config /etc/caddy/Caddyfile'
```

Expected: `Valid configuration`.

- [ ] **Step 4: Reload Caddy**

```bash
ssh ubuntu@129.213.126.251 'sudo systemctl reload caddy && sudo systemctl status caddy --no-pager | head -5'
```

- [ ] **Step 5: Verify HTTPS**

From your Mac:

```bash
curl -fsS https://<reelpush-host>.duckdns.org/api/health
```

Expected: `{"status":"ok","environment":"production"}`. Let's Encrypt cert issuance can take 30–90s on first request — if you get a TLS error initially, wait and retry.

- [ ] **Step 6: Verify Discord bot still healthy**

```bash
ssh ubuntu@129.213.126.251 'sudo systemctl status discordbot --no-pager | head -5'
```

Expected: still `active (running)`.

- [ ] **Step 7: No local commit**

---

### Task 34: Hand OAuth callback URLs to user for provider registration

**Files:**
- (Manual; user-side)

- [ ] **Step 1: Provide the exact URLs**

The executor outputs these to the user, who registers them in each provider's dashboard before Phase 5:

```
YouTube/Google:
  Console: https://console.cloud.google.com → APIs & Services → Credentials
  → OAuth 2.0 Client IDs → "Authorized redirect URIs":
  https://<reelpush-host>.duckdns.org/api/oauth/youtube/callback

  Optional dev variant (keep for local dev):
  http://localhost:8001/api/oauth/youtube/callback

Meta/Instagram:
  Console: https://developers.facebook.com → My Apps → ReelPush →
  Facebook Login → Settings → "Valid OAuth Redirect URIs":
  https://<reelpush-host>.duckdns.org/api/oauth/instagram/callback

  Optional dev variant:
  http://localhost:8001/api/oauth/instagram/callback

TikTok:
  Console: https://developers.tiktok.com → My apps → ReelPush →
  Login Kit → "Redirect URI":
  https://<reelpush-host>.duckdns.org/api/oauth/tiktok/callback

  Optional dev variant:
  http://localhost:8001/api/oauth/tiktok/callback
```

- [ ] **Step 2: Wait for user confirmation**

Do not start Phase 5 until the user confirms all three providers are configured.

- [ ] **Step 3: No commit**

---

## Phase 5: Mac client cutover + smoke tests

### Task 35: Update Mac config to point at HTTPS endpoint

**Files:**
- Modify: `~/Library/Application Support/ReelPush/config.json`

- [ ] **Step 1: Edit config**

```bash
cat > ~/Library/Application\ Support/ReelPush/config.json <<EOF
{
  "api_url": "https://<reelpush-host>.duckdns.org",
  "developer_mode": false
}
EOF
```

- [ ] **Step 2: Clear stale Keychain entry from local-dev testing**

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "
import keyring
try:
    keyring.delete_password('com.reelpush.app', 'default')
    print('cleared old token')
except keyring.errors.PasswordDeleteError:
    print('no old token to clear')
"
```

- [ ] **Step 3: No commit**

---

### Task 36: JWT login flow against production server

**Files:**
- (Verification)

- [ ] **Step 1: Launch Mac app**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 reelpush_desktop.py
```

- [ ] **Step 2: Sign in**

Email: `admin@reelpush.local` (whatever was set in server `.env`).
Password: the `ADMIN_PASSWORD` from Task 28.

Expected: login succeeds; main app window opens.

- [ ] **Step 3: Verify JWT in Keychain**

Open macOS Keychain Access, search `com.reelpush.app` — entry exists.

- [ ] **Step 4: No commit**

---

### Task 37: OAuth-connect each provider

**Files:**
- (Verification)

- [ ] **Step 1: Connect Instagram**

In the Mac app, find "Connect Instagram" → click → browser opens to `https://<reelpush-host>.duckdns.org/api/oauth/instagram/start` → complete Meta sign-in → confirmation page → Mac modal closes.

```bash
ssh ubuntu@129.213.126.251 'sudo journalctl -u reelpush-backend --since -2m | grep -i oauth'
```

Expected: log lines showing the OAuth callback hit + a `accounts` row insertion.

```bash
ssh ubuntu@129.213.126.251 'sqlite3 /home/ubuntu/reelpush-backend/data/reelpush.db "SELECT provider, id, created_at FROM accounts ORDER BY created_at DESC LIMIT 5;"'
```

Expected: a new `instagram` row.

- [ ] **Step 2: Connect YouTube**

Repeat the flow.

- [ ] **Step 3: Connect TikTok**

Repeat the flow.

- [ ] **Step 4: No commit**

---

### Task 38: Publish round-trip per provider

**Files:**
- (Verification)

- [ ] **Step 1: Pick a small mp4**

Any short video (~5–15s, < 50 MB) on the Mac. Avoid sensitive content.

- [ ] **Step 2: For each provider, create a job**

In the Mac app, upload + publish to Instagram. Verify:
- Upload completes (Mac app shows progress).
- `https://<reelpush-host>.duckdns.org/media/<token>` returns 200 when curled (open it in a browser if Tk doesn't expose it).
- Backend log shows `run_publish_job` execution + IG Container API success.
- A new post appears on the connected IG account.

Repeat for YouTube (a YouTube Short) and TikTok.

If any provider fails, capture the journalctl output and the `jobs` row to debug:

```bash
ssh ubuntu@129.213.126.251 '
sudo journalctl -u reelpush-backend --since -10m | tail -100
sqlite3 /home/ubuntu/reelpush-backend/data/reelpush.db "SELECT id, provider, state, last_error FROM jobs ORDER BY created_at DESC LIMIT 5;"
'
```

- [ ] **Step 3: No commit**

---

### Task 39: Final verification suite

**Files:**
- (Verification only)

- [ ] **Step 1: Discord bot integrity (must equal Phase 1 baseline)**

```bash
ssh ubuntu@129.213.126.251 '
echo "=== discordbot status ==="
sudo systemctl status discordbot --no-pager | head -5
echo
echo "=== discord-bot git status ==="
git -C /home/ubuntu/discord-bot status
echo
echo "=== /home/ubuntu/.env mtime ==="
stat -c "%y %n" /home/ubuntu/.env
'
```

Expected:
- `Active: active (running)` ✓
- `git status` exactly matches the saved Phase 1 baseline ✓
- `.env` mtime is unchanged from Phase 1 ✓

If any of these fail, **stop and investigate before declaring done**.

- [ ] **Step 2: ReelPush service health**

```bash
ssh ubuntu@129.213.126.251 '
sudo systemctl is-active reelpush-backend
sudo systemctl is-enabled reelpush-backend
sudo systemctl status reelpush-backend --no-pager | head -10
'
```

Expected: `active`, `enabled`.

- [ ] **Step 3: HTTPS health**

```bash
curl -fsS https://<reelpush-host>.duckdns.org/api/health
```

Expected: `{"status":"ok","environment":"production"}`.

- [ ] **Step 4: Secrets-leak audit**

```bash
ssh ubuntu@129.213.126.251 '
sudo journalctl -u reelpush-backend --since today | \
  grep -iE "client_secret|app_secret|access_token|refresh_token|password=" | head -20
'
```

Expected: **no matches** (or only redacted entries like `password=***`).

If any actual secret values appear, stop and tighten the structlog config to redact.

- [ ] **Step 5: Memory headroom check**

```bash
ssh ubuntu@129.213.126.251 '
free -h
sudo systemctl show reelpush-backend -p MemoryCurrent --value
sudo systemctl show discordbot -p MemoryCurrent --value
'
```

Expected: total free + buff/cache ≥ 200 MB; reelpush-backend < 400 MB.

- [ ] **Step 6: Full end-to-end Mac round-trip**

Repeat one publish from Task 38 to confirm everything still works after the verification ssh churn.

- [ ] **Step 7: Final commit (plan completion)**

```bash
cd "/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac"
git status
git log --oneline server-backend-migration ^main | head
```

If everything's committed branch-side, the migration branch is ready for the user to merge to `main` when comfortable.

---

## Plan Self-Review

Reviewed against the spec at `docs/superpowers/specs/2026-05-01-reelpush-server-backend-design.md`:

- **Domain placeholder (DuckDNS):** Tasks 31, 33 — covered.
- **Stack reduction (drop Postgres/Redis/Celery; SQLite + APScheduler):** Tasks 4–11, 17 — covered.
- **JWT auth with macOS Keychain:** Tasks 20–21 — covered.
- **No Docker on VM:** Tasks 25–30 — explicit systemd-on-venv path.
- **Backend file-by-file refactor (Section 5 of spec):** Tasks 4–17 — covered, file-by-file.
- **Mac client refactor (Section 6 of spec):** Tasks 19–24 — covered.
- **OAuth callback URL list (Section 8 of spec):** Task 34 — covered.
- **Phase 1–5 rollout (Section 9 of spec):** Tasks 1–39 — covered.
- **Risks/mitigations (Section 10):** memory caps in Task 30 unit; LE staging swap mentioned but not pre-staged in plan (acceptable — first cert request handles it); WAL mode in Task 5; no `Wants=`/`Requires=` discord coupling in Task 30 unit; secrets-leak audit in Task 39.
- **Discord-bot baseline-vs-final diff:** Phase 1 captures baseline, Task 39 verifies — covered.

**Gaps closed:**
- Task 1 (WIP triage) added because the existing dirty tree had ~25 modified files that weren't part of this migration.
- Task 31 (DuckDNS auto-update timer) added because static IP isn't guaranteed on Oracle ephemeral instances.

**Placeholder scan:** No "TBD"/"TODO"/"fill in"/"add appropriate" remaining; every code change has explicit code or an explicit Codex prompt; every command has expected output.

---

## Execution

Plan complete. Two execution options:

1. **Subagent-driven** (recommended for this plan — many tasks, easy to parallelize backend tasks 4–13 and Mac tasks 19–22 across separate subagents) — fresh subagent per task with two-stage review between tasks.

2. **Inline execution** — execute task-by-task in the current session with batch checkpoints (e.g., review after Phase 2 backend, after Phase 2-Mac, after Phase 3, after Phase 4).

Per CLAUDE.md, both options should still drive the heavy code via Codex prompts (already embedded in tasks 8 and 23) and use Claude for review/scoping.
