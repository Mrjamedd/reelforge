# ReelPush Server-Backend Migration — Design

**Date:** 2026-05-01
**Status:** Draft for user review
**Owner:** Anthony Murphy
**Target server:** Oracle Cloud Ubuntu 22.04, `ubuntu@129.213.126.251`, ~2 vCPU / ~1 GB RAM / 49 GB disk
**Local project:** `/Users/anthonymurphy/Personal_Python/ReelPush Mac+Windows/ReelPush-Mac`

---

## 1. Goal

Move ReelPush's secure backend, secrets, OAuth handling, token storage, and publishing logic to the Oracle Cloud VM. The local Mac app becomes a thin HTTPS client. The existing Discord bot at `/home/ubuntu/discord-bot` (`discordbot.service`) must stay untouched and continuously running through the entire migration.

## 2. Non-goals

- Multi-user or multi-tenant support.
- Replacing the existing Tk UI of `reelpush_desktop.py` (only its data/network layer changes).
- Building observability beyond `journalctl`, Caddy access logs, and a `/health` endpoint.
- Migrating any production data — the project has only 2 git commits, so SQLite is initialized empty.
- Touching the Discord bot, its venv, its env file, its assets, its service, or its port.

## 3. Decisions captured up-front

| Decision | Choice | Reason |
|---|---|---|
| Public hostname | DuckDNS subdomain `<reelpush-host>.duckdns.org` as the placeholder; sslip.io as fallback only if DuckDNS rate-limits during setup. Real domain comes later. | User has no domain yet; provider OAuth callbacks must be re-registered when migrating to a real domain |
| Backend stack | **Single uvicorn process** with SQLite + APScheduler (no Postgres, no Redis, no Celery) | 1 GB RAM shared with Discord bot makes the full stack OOM-prone; single-user app doesn't need distributed workers |
| Reverse proxy | **Caddy** with Let's Encrypt auto-HTTPS | Auto-cert renewal, minimal config, lighter to operate solo than Nginx + Certbot |
| Mac → server auth | **JWT login flow** reusing the existing `python-jose` scaffold; JWT stored in macOS Keychain | Reuses existing backend auth code; clean upgrade path if multi-user is added later |
| Containerization | **No Docker on the VM** — plain Python venv + systemd | RAM headroom, simpler ops; `docker-compose.yml` can stay for local dev only |
| Database | **SQLite** with WAL mode at `/home/ubuntu/reelpush-backend/data/reelpush.db` | Smallest footprint; sufficient for single-user write rate; handles APScheduler + request concurrency under WAL |

## 4. Architecture

### 4.1 Server topology

```
┌─────────────────────────────────────────────────────────┐
│ Oracle Cloud VM (Ubuntu 22.04, ~1 GB RAM)               │
│                                                         │
│  ┌──────────────────────┐    ┌──────────────────────┐   │
│  │ discordbot.service   │    │ reelpush-backend.    │   │
│  │ (UNTOUCHED)          │    │   service (NEW)      │   │
│  │ /home/ubuntu/        │    │ /home/ubuntu/        │   │
│  │   discord-bot        │    │   reelpush-backend   │   │
│  │ /home/ubuntu/.env    │    │   .env (separate)    │   │
│  └──────────────────────┘    └──────────┬───────────┘   │
│                                         │ 127.0.0.1:8001│
│                              ┌──────────┴───────────┐   │
│                              │ caddy.service (NEW   │   │
│                              │   or existing)       │   │
│                              │ ports 80/443         │   │
│                              └──────────┬───────────┘   │
└────────────────────────────────────────┼────────────────┘
                                         │ HTTPS
                              ┌──────────┴───────────────┐
                              │ Mac client (Tk app)      │
                              │ JWT in macOS Keychain    │
                              │ API_URL → DDNS hostname  │
                              └──────────────────────────┘
```

### 4.2 Filesystem layout

```
/home/ubuntu/discord-bot/                   ← UNTOUCHED
/home/ubuntu/.env                           ← UNTOUCHED
/home/ubuntu/reelpush-backend/
├── app/                                    ← FastAPI code (rsynced from local backend/)
├── alembic/                                ← migrations
├── alembic.ini
├── data/reelpush.db                        ← SQLite, 0600 perms
├── storage/                                ← uploaded media, served at /media/<token>
├── venv/                                   ← isolated Python venv
├── logs/
├── requirements.txt
├── scripts/
│   └── seed_admin.py
└── .env                                    ← ReelPush secrets, 0600 perms
/etc/systemd/system/reelpush-backend.service
/etc/caddy/Caddyfile                        ← additive vhost only
```

### 4.3 systemd unit (`reelpush-backend.service`)

```ini
[Unit]
Description=ReelPush FastAPI backend
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/reelpush-backend
EnvironmentFile=/home/ubuntu/reelpush-backend/.env
ExecStart=/home/ubuntu/reelpush-backend/venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8001 --workers 1
Restart=on-failure
RestartSec=10
MemoryMax=400M
MemoryHigh=300M
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

The unit deliberately declares **no relationship** to `discordbot.service` so a ReelPush failure cannot cascade. Memory caps protect the Discord bot from a runaway uvicorn (e.g., during ffmpeg processing).

### 4.4 Caddy vhost (additive)

```caddyfile
<reelpush-host>.duckdns.org {
    reverse_proxy 127.0.0.1:8001
    encode gzip
    request_body {
        max_size 200MB
    }
    log {
        output file /var/log/caddy/reelpush.log
    }
}
```

Existing Caddyfile blocks (if any) are preserved verbatim. If preflight reveals Nginx already terminating 80/443, fall back to Nginx + Certbot rather than fight for the ports.

### 4.5 Data flows

**Publish job:**
1. Mac → `POST /api/uploads` (multipart) → server saves to `storage/`, returns `https://<host>/media/<token>`
2. Mac → `POST /api/jobs` `{provider, media_url, caption, …}`
3. Server creates job row, schedules execution via APScheduler/`BackgroundTasks`
4. In-process worker calls provider API (YouTube/Instagram/TikTok); for IG, passes `media_url` to the Container API
5. Mac polls `GET /api/jobs/{id}` for status

**OAuth account connect:**
1. Mac → opens `https://<host>/api/oauth/{provider}/start` in system browser
2. Provider redirects to `https://<host>/api/oauth/{provider}/callback?code=…&state=…`
3. Server validates `state`, exchanges code for tokens, persists encrypted tokens in SQLite
4. Server redirects browser to a "you can return to ReelPush" confirmation page
5. Mac polls `GET /api/accounts` to detect the new connection, dismisses its modal

**Auth (Mac → server):**
1. First launch: Mac shows login window → `POST /api/auth/login` `{email, password}` → returns JWT
2. Mac stores JWT in macOS Keychain via `keyring`
3. Every subsequent request carries `Authorization: Bearer <jwt>`
4. On 401 → Mac transparently re-auths; on persistent 401 → re-prompt login

## 5. Backend refactor scope

### 5.1 Files that change substantially

| File | Lines today | Change |
|---|---|---|
| `backend/app/workers/celery_app.py` | 55 | **Delete** |
| `backend/app/workers/tasks.py` | 273 | **Rewrite** as plain async functions; bodies retained, `@celery_app.task` decorators and `.delay()` call sites removed |
| `backend/app/services/publish_service.py` | 285 | Replace `task.delay(...)` enqueues with `scheduler.add_job(...)` for publish jobs (decoupled from request lifecycle, survives restart via APScheduler's SQLAlchemy job store) and `background_tasks.add_task(...)` only for sub-second post-response cleanup |
| `backend/app/db/session.py` | 38 | `asyncpg` → `aiosqlite`; URL becomes `sqlite+aiosqlite:////home/ubuntu/reelpush-backend/data/reelpush.db`; enable WAL on first connect |
| `backend/app/core/config.py` | 82 | Drop `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`; add `PUBLIC_BASE_URL`, `MEDIA_URL_TTL_SECONDS`, `ENVIRONMENT` |
| `backend/app/main.py` | 57 | Wire `AsyncIOScheduler` into FastAPI lifespan; mount `/media/{token}` static-with-auth route |

### 5.2 Files that change lightly

- `backend/app/services/oauth_service.py` — periodic token-refresh job moves from Celery beat to APScheduler interval job
- `backend/app/services/media_service.py` — add `make_signed_url(path) → str` helper returning `f"{settings.PUBLIC_BASE_URL}/media/{token}"`; existing storage logic intact
- `backend/app/api/routes/oauth.py` — replace any hardcoded `localhost:8100` redirects with `settings.PUBLIC_BASE_URL`; verify `state` parameter CSRF handling
- `backend/app/api/routes/uploads.py` — return public `/media/<token>` URL instead of local path
- `backend/requirements.txt` — drop `celery`, `kombu`, `redis`, `asyncpg`, `psycopg2-binary`; optionally drop `boto3` (kept if S3 is on the roadmap); add `aiosqlite`, `apscheduler`

### 5.3 Migrations

Existing Alembic migrations (Postgres-flavored) regenerated against SQLite as a single new baseline. Acceptable because no production data exists yet. Procedure:
1. Delete `backend/alembic/versions/`
2. Point `alembic.ini` at the SQLite URL
3. `alembic revision --autogenerate -m "init sqlite"`
4. Ship the resulting baseline.

### 5.4 Tests

- `backend/tests/conftest.py` already stubs `magic` for the host. Keep.
- DB-touching tests now hit a tmp SQLite file; SQLAlchemy is already async-DB-agnostic so most tests survive.
- Celery-specific tests deleted or rewritten as direct async function calls.
- New test: `test_signed_media_url.py` — verifies `make_signed_url` produces a URL that `/media/<token>` serves.

## 6. Mac client changes

### 6.1 New module — `reelpush_client/api.py`

Single `ServerAPI` class wrapping `httpx.Client`:
- Reads `API_URL` from `~/Library/Application Support/ReelPush/config.json` (default DDNS hostname)
- Stores JWT in macOS Keychain via `keyring`
- Methods: `login`, `logout`, `list_accounts`, `start_oauth`, `upload_media`, `create_publish_job`, `get_job`, `list_jobs`, `refresh_token_if_needed`
- Auto-retries idempotent calls via `tenacity`
- Maps 401 → re-auth, 5xx → user-visible error, network → backoff

### 6.2 New module — `reelpush_client/oauth_browser.py`

- `webbrowser.open(f"{API_URL}/api/oauth/{provider}/start")`
- Tk modal with "Waiting for OAuth completion…" + Cancel
- Polls `GET /api/accounts` every 2s, closes modal on connection appearance

### 6.3 New UI — login window

- Shown if Keychain has no JWT (or it's invalid)
- Email + password fields, "Remember me" (default on)
- On success: store JWT, dismiss, show main app

### 6.4 Removed from `reelpush_desktop.py`

- All `dotenv.load_dotenv()` calls
- All direct calls to `graph.facebook.com`, `googleapis.com`, `tiktokapis.com`
- All direct OAuth code (token exchange, refresh)
- Any local file reading provider client secrets, refresh tokens, or access tokens

### 6.5 Config layering

| Layer | Source | Notes |
|---|---|---|
| Default | `~/Library/Application Support/ReelPush/config.json` | `API_URL`, `developer_mode` |
| Override | `REELPUSH_API_URL` env var | For local dev |
| Secret | macOS Keychain (service `com.reelpush.app`) | JWT only — no provider secrets ever land on the Mac |

## 7. Required environment variables (server `.env`)

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing; rotated on deploy (do not reuse local dev key) |
| `ALGORITHM` | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | e.g. `1440` (1 day) |
| `DATABASE_URL` | `sqlite+aiosqlite:////home/ubuntu/reelpush-backend/data/reelpush.db` |
| `PUBLIC_BASE_URL` | `https://<reelpush-host>.duckdns.org` |
| `API_URL` | Same as `PUBLIC_BASE_URL` (kept for backward compat with existing code) |
| `STORAGE_BACKEND` | `local` |
| `LOCAL_STORAGE_PATH` | `/home/ubuntu/reelpush-backend/storage` |
| `MEDIA_URL_TTL_SECONDS` | e.g. `3600` |
| `YOUTUBE_CLIENT_ID` | Copied from local dev |
| `YOUTUBE_CLIENT_SECRET` | Copied from local dev |
| `INSTAGRAM_APP_ID` | Copied from local dev |
| `INSTAGRAM_APP_SECRET` | Copied from local dev |
| `TIKTOK_CLIENT_KEY` | Copied from local dev |
| `TIKTOK_CLIENT_SECRET` | Copied from local dev |
| `ADMIN_EMAIL` | For seeded admin / Mac login |
| `ADMIN_PASSWORD` | Strong randomly generated value (do not reuse) |
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |

`.env` permissions: `chmod 600`, owned by `ubuntu:ubuntu`. Never logged. Never returned by any API endpoint.

## 8. OAuth callback URLs to register (production)

After Phase 4 completes, register these in the provider dashboards:

- Google Cloud Console → OAuth 2.0 Client IDs → `https://<reelpush-host>.duckdns.org/api/oauth/youtube/callback`
- Meta for Developers → Facebook Login → Valid OAuth Redirect URIs → `https://<reelpush-host>.duckdns.org/api/oauth/instagram/callback`
- TikTok for Developers → Login Kit → Redirect URI → `https://<reelpush-host>.duckdns.org/api/oauth/tiktok/callback`

Keep dev variants (`http://localhost:8001/api/oauth/{provider}/callback`) registered alongside for local development.

## 9. Phased rollout

### Phase 1 — Read-only preflight (no server changes)

- SSH connectivity check
- `systemctl status discordbot --no-pager` → must be `active (running)`
- `git -C /home/ubuntu/discord-bot status` → record output as baseline diff
- `df -h`, `free -h`, `sudo ss -tulpn` → confirm port 8001 free, RAM headroom
- `systemctl status nginx caddy apache2 --no-pager` → identify existing reverse proxy if any
- `ls -la /home/ubuntu` → confirm directory layout matches expectations
- **Output:** preflight report; gate to Phase 2

### Phase 2 — Backend code refactor (local repo, no server contact)

- New branch `server-backend-migration`
- Apply Section 5 changes
- All tests pass: `cd backend && python3 -m pytest tests/ -v`
- Mac client `ServerAPI` module added; `reelpush_desktop.py` rewired
- Local smoke test: backend running locally + Mac app in dev mode → login + one publish round-trip succeeds against `http://localhost:8001`
- **Gate:** Claude diff review (per CLAUDE.md Full-Gate)

### Phase 3 — Deploy backend to Oracle VM

- Create `/home/ubuntu/reelpush-backend/`, venv, install deps from `requirements.txt`
- `rsync` backend code (excludes `.venv`, `__pycache__`, `storage/*`, `*.db`, `*.bak`)
- Generate server `.env` directly on the VM (never via local-to-server file transfer):
  - `SECRET_KEY` and `ADMIN_PASSWORD` produced fresh with `openssl rand -hex 32` on the server
  - Provider credentials (`YOUTUBE_CLIENT_*`, `INSTAGRAM_APP_*`, `TIKTOK_CLIENT_*`) entered interactively into the file via `nano`/`vi` over an authenticated SSH session — values read from the user's local `.env` and pasted in by the user, not piped through any logged channel
  - `chmod 600`, `chown ubuntu:ubuntu` immediately after creation
- Run Alembic migrations on empty SQLite; run `seed_admin.py`
- Install `reelpush-backend.service`; `systemctl daemon-reload`; `systemctl enable --now reelpush-backend`
- Verify Discord bot still healthy: `systemctl status discordbot`, `git -C /home/ubuntu/discord-bot status` (must equal Phase 1 baseline)
- Localhost smoke: `curl -fsS http://127.0.0.1:8001/health`

### Phase 4 — HTTPS + DNS

- Register `<reelpush-host>.duckdns.org` → `129.213.126.251` (or chosen DDNS)
- Install Caddy via Cloudsmith apt repo (or reuse existing reverse proxy)
- Add the additive vhost to `/etc/caddy/Caddyfile`
- `caddy validate && systemctl reload caddy`
- `curl -fsS https://<host>/health` → 200
- Hand the OAuth callback URL list to the user for provider-dashboard registration
- **Gate:** wait for user to register callbacks before Phase 5

### Phase 5 — Mac client cutover + smoke tests

- Update Mac config to point at the public HTTPS URL
- Login flow → JWT in Keychain
- Connect each provider via OAuth → verify token row in server DB
- Upload + publish one test post per provider
- Final verification suite:
  - `systemctl status discordbot` → `active (running)` ✓
  - `git -C /home/ubuntu/discord-bot status` → identical to Phase 1 baseline ✓
  - `systemctl status reelpush-backend` → `active (running)` ✓
  - `systemctl is-enabled reelpush-backend` → `enabled` ✓
  - `curl -fsS https://<host>/health` → 200 ✓
  - `journalctl -u reelpush-backend --since today | grep -iE 'secret|password|client_secret|access_token|refresh_token'` → no matches ✓
  - Mac app publish round-trip works end-to-end ✓

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Existing reverse proxy already on 80/443 | Phase 1 preflight detects; fall back to whatever's there |
| Memory pressure under upload + ffmpeg | `MemoryMax=400M` cap on the unit; create 2 GB swap file in Phase 3 as safety net |
| Let's Encrypt rate limits during DDNS hostname iteration | Use Caddy's staging cert during initial setup, switch to production once verified |
| Provider OAuth re-registration after moving off DuckDNS | Documented as known follow-up; spec lists exact redirect URIs to update |
| SQLite write contention from APScheduler + request handlers | Enable `PRAGMA journal_mode=WAL` on first connection — handles single-process concurrent writers cleanly |
| Cascading failure from ReelPush taking down the Discord bot | systemd unit declares no `Wants=`/`Requires=` on discordbot; memory caps prevent OOM blast |
| Secrets accidentally logged | `journalctl --since today` grep included in smoke tests; structlog config audited for secret-redaction in Phase 2 |
| Local Mac app loses backend connection mid-publish | Tenacity retries with exponential backoff; jobs are idempotent server-side via `state`/job ID |

## 11. Open follow-ups (not in this plan, intentionally)

- Real domain registration and OAuth callback re-registration once user is ready
- Off-server backups of `data/reelpush.db` and `storage/`
- Observability beyond `journalctl` (Sentry, healthchecks.io ping, etc.)
- S3/R2 storage backend (code already supports it; toggle via `STORAGE_BACKEND=s3`)
- Multi-user support (JWT scaffold can extend cleanly)

## 12. References

- CLAUDE.md (project root) — Full-Gate workflow, Codex scout patterns, env stub
- `backend/app/main.py`, `backend/app/core/config.py` — current backend wiring
- `backend/app/workers/celery_app.py`, `backend/app/workers/tasks.py` — code being deleted/rewritten
- `docker-compose.yml` — current 5-service local stack (kept for local dev only)
- Caddy docs — `https://caddyserver.com/docs/` (general; verify version-specific syntax during Phase 4)
- DuckDNS — `https://www.duckdns.org/` (free DDNS option)
