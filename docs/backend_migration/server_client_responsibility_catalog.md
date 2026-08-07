# ReelPush Server / Client Responsibility Catalog

_Prepared: 2026-05-02. Read-only audit. No code was changed._

---

## 1. Executive Summary

ReelPush already has a clean FastAPI backend (`backend/`) and a Tkinter desktop frontend (`reelpush_desktop.py`). The backend runs today inside Docker Compose on the user's Mac. After migration, the same FastAPI/Celery/PostgreSQL/Redis stack will run persistently on an OCI Ubuntu server managed by systemd, behind Caddy for HTTPS. The desktop becomes a pure API client: it opens a file picker, uploads video to the OCI server, sets platform metadata, and polls job status. All publishing logic, OAuth token handling, media storage, scheduling, and secrets stay on OCI.

The thinning work is largely about cutting two responsibilities out of the desktop that do not belong in a remote-backend world:

1. **Docker lifecycle management** — `_start_local_services`, `_refresh_backend_service`, `_collect_runtime_statuses` (docker compose ps), the entire bootstrap screen. These disappear entirely; the desktop just checks `/api/health`.
2. **Credential write-through to `.env`** — `save_platform_credentials` currently writes API keys to a local `.env` file and restarts Docker. After migration, credentials must be managed on the OCI server via environment variables at deploy time, or via a new `/api/settings` endpoint. The desktop cannot write to the server's filesystem.

**Highest-risk areas:**

- Platform credential flow (desktop currently writes secrets to local `.env` + restarts Docker — this pattern cannot survive a remote backend)
- Instagram media URL requirement (Instagram requires a publicly accessible HTTPS URL; local storage is explicitly blocked; S3/R2 must be provisioned)
- OAuth callback redirect URI (currently `http://localhost:8100/api/oauth/{platform}/callback`; must become the OCI HTTPS domain)
- OAuth CSRF state store (currently an in-memory Python dict `_pending_states` in `oauth_service.py:31` — must move to Redis before multi-process or multi-restart deploy)
- Desktop hardcoded API base (`API_BASE = "http://localhost:8100/api"` at `reelpush_desktop.py:205`) — must be pointed at OCI via `REELPUSH_API_BASE` env var

---

## 2. Server Backend Responsibilities

### 2.1 Platform OAuth Token Storage and Refresh

**Why server:** Tokens are secrets. Storing them on a desktop means they travel with the machine and are at risk of .env leakage. The server encrypts tokens at rest with `SECRET_KEY`.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/services/oauth_service.py`, `backend/app/models/models.py` (`PlatformAccount`), `backend/app/api/routes/oauth.py` |
| Key functions | `OAuthService.generate_auth_url`, `OAuthService.handle_callback`, `OAuthService.ensure_fresh_token`, `OAuthService.refresh_account_token`, `OAuthService._upsert_account` |
| Required endpoint | `GET /api/oauth/{platform}/connect-url`, `GET /api/oauth/{platform}/callback`, `DELETE /api/oauth/{platform}/disconnect` |
| Migration risk | **High** — `_pending_states` dict in `oauth_service.py:31` is in-process memory. On OCI a process restart loses all pending OAuth states. Must be moved to Redis before deploying. |

### 2.2 API Credentials and Platform Secrets

**Why server:** TikTok/Instagram/YouTube client secrets must never appear in desktop builds. They are environment variables loaded by the backend at startup via `pydantic_settings`.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/core/config.py` (`Settings`), `.env`, `.env.example` |
| Key items | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `SECRET_KEY` |
| Current problem | The desktop's `save_platform_credentials` (line 3269) writes these to `ROOT_DIR/.env` and calls `docker compose up --force-recreate` to reload them. After OCI migration this workflow is completely severed. Credentials must be set on OCI via `/etc/environment`, a systemd `EnvironmentFile`, or a new backend settings API. |
| Migration risk | **High** — the desktop credential-save workflow is the most architecturally broken piece for OCI. |

### 2.3 YouTube / Instagram / TikTok Publishing Calls

**Why server:** Platform APIs have server-side rate limits and require long-running async operations (chunked video uploads, polling for processing status). They must run on a persistent server process with retry logic.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/providers/youtube.py`, `backend/app/providers/instagram.py`, `backend/app/providers/tiktok.py`, `backend/app/providers/base.py`, `backend/app/providers/registry.py` |
| Key functions | `create_upload`, `publish_now`, `schedule_publish`, `delete_post`, `validate_post_payload` (all on provider classes) |
| Required endpoint | `POST /api/jobs/`, `POST /api/jobs/bulk`, `POST /api/workspace/publish-now` |
| Migration risk | **Medium** — code is already server-side. Risk is in correct public URL availability for Instagram and ensuring the Celery worker process has network access to platform APIs. |

### 2.4 Scheduling and Background Jobs

**Why server:** Jobs must execute on a schedule even when the desktop is closed. Celery + Redis beat provides ETA-based and cron-based scheduling.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/workers/tasks.py`, `backend/app/workers/celery_app.py`, `backend/app/services/publish_service.py` |
| Key functions | `run_publish_job` (Celery task), `poll_scheduled_jobs` (beat task), `PublishService.create_job`, `PublishService._enqueue`, `PublishService.retry_job`, `PublishService.cancel_job` |
| Required services | `celery worker` (publish queue), `celery beat` (scheduler) — both in `docker-compose.yml` |
| Current state | Desktop UI has a `schedule_var` widget but publish_now (line 3698–3703) explicitly blocks scheduled desktop publishing with: "Scheduled desktop publishing is not wired into this local flow yet." |
| Migration risk | **Medium** — backend scheduling is implemented. Desktop UI needs to wire the `scheduled_for` datetime into the `/api/jobs/` payload. |

### 2.5 Database and State Storage

**Why server:** All publish history, platform accounts, uploads, job audit logs, and staged state must persist across sessions and desktop restarts.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/models/models.py`, `backend/app/db/session.py`, `backend/app/db/migrations/` |
| Key models | `AdminUser`, `PlatformAccount`, `Upload`, `PublishJob`, `AuditLog`, `StagedPublish`, `PublishProfile` |
| Engine | PostgreSQL 15 (Docker service `db`) |
| On OCI | Run as a systemd service or managed PostgreSQL. Alembic migrations (`alembic.ini`, `migrations/versions/`) must run at deploy time. |
| Migration risk | **Low** — already server-side. Risk is only in data migration from local Docker volume to OCI. |

### 2.6 Media Upload Receiving and Storage

**Why server:** The server validates, normalizes, thumbnails, and stores video files. The desktop sends the raw bytes; the server decides where they live.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/api/routes/uploads.py`, `backend/app/services/media_service.py`, `backend/app/services/storage_providers.py` |
| Key functions | `upload_video` (route), `MediaService.validate_video`, `MediaService.extract_source_metadata`, `MediaService.normalize_for_short_form`, `MediaService.store_upload_from_path`, `MediaService.generate_thumbnail` |
| Storage | `STORAGE_BACKEND=local` (Docker volume `./storage`) or `s3`. Local currently suffices for YouTube/TikTok but Instagram explicitly requires S3 (`media_service.py` → `publish_service.py:87-96`). |
| Required endpoint | `POST /api/uploads/`, `GET /api/uploads/`, `GET /api/uploads/{id}` |
| Migration risk | **Medium** — local storage path (`./storage`) works on OCI with a mounted volume. Instagram requires S3/R2 provisioning and the `instagram_media_url_configured` property must return True. |

### 2.7 Media Token Generation and Public URL Serving

**Why server:** Signed or public URLs for media files must originate from the server to avoid exposing S3 credentials to the desktop.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/services/media_service.py` (`get_public_url`, `get_instagram_public_url`), `backend/app/main.py:48-52` (static file mount for local dev) |
| On OCI | In `STORAGE_BACKEND=local` mode the backend mounts `./storage` as `/media` via FastAPI `StaticFiles`. In production, switch to S3 presigned URLs. The `/media` static mount should be behind Caddy authentication for non-Instagram content. |
| Migration risk | **Medium** — Instagram will not work without S3. YouTube and TikTok accept direct upload paths, so `/media` can remain local for them. |

### 2.8 Server Logs

**Why server:** Structured publish job logs (`core/logging.py`) and Celery task logs must be retained on OCI for debugging.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/core/logging.py` |
| Key items | structlog-based structured logging, `LOG_LEVEL` setting. Celery worker logs go to stdout and are captured by Docker. On OCI, systemd journal captures these. |
| Migration risk | **Low** — no code change needed; systemd journal replaces Docker stdout capture. |

### 2.9 User / Workspace / Account State

**Why server:** Staged publish, default publish profile, and platform account connections must survive desktop restarts.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/api/routes/workspace.py`, `backend/app/services/workspace_service.py`, `backend/app/models/models.py` (`StagedPublish`, `PublishProfile`) |
| Key endpoints | `GET/PUT /api/workspace/profile`, `GET/PUT/DELETE /api/workspace/staged`, `POST /api/workspace/publish-now` |
| Migration risk | **Low** — already fully server-side. |

### 2.10 Validation That Protects Backend Integrity

**Why server:** File type, size, duration, and platform payload validation must run on the server to prevent bad data entering the database or being sent to platform APIs.

| Field | Detail |
|-------|--------|
| Current files | `backend/app/services/media_service.py` (`validate_video`, `validate_video_file`, `short_form_compatibility_warnings`), `backend/app/providers/*/validate_post_payload` |
| Migration risk | **Low** — already server-side. Desktop should display returned validation errors but not re-implement them. |

---

## 3. Local Desktop Frontend Responsibilities

### 3.1 Tkinter UI Rendering

**Why client:** Tkinter is a local GUI toolkit. All widget construction, layout, styling, and theming is inherently local.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `ReelPushDesktop` class (line 950), `RoundedFrame`, `ScrollableFrame`, `DropdownField` |
| Key methods | `_build_styles`, `_build_publishing_tab`, `_build_accounts_tab`, `_build_settings_tab`, `_build_section_nav`, layout helpers |
| Backend API dependency | None (pure UI) |
| Migration risk | **Low** — no change needed except removing Docker-specific UI sections (bootstrap screen, service status panel with Docker stats). |

### 3.2 User Input Forms

**Why client:** Title, caption, hashtags, privacy level, and scheduled-for fields are local form inputs that assemble a payload to send to the backend.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `_make_entry_field`, `_make_dropdown_field`, `_install_entry_placeholder`, `_handle_post_details_changed`, `_update_character_counts`, `_parsed_hashtags` |
| Backend API dependency | Submits to `PUT /api/workspace/profile` and `POST /api/workspace/publish-now` |
| Migration risk | **Low** — no change needed. |

### 3.3 File Picker Dialog

**Why client:** `filedialog.askopenfilename` is a native OS dialog. It runs locally and returns a local file path.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py:3630` — `choose_video` |
| Current behavior | User picks a local file; desktop immediately calls `client.upload_file(file_path)` which streams the file to `POST /api/uploads/`. |
| Backend API dependency | `POST /api/uploads/` |
| Migration risk | **Low** — the pattern stays the same. File is POSTed to OCI instead of localhost. Large files (multi-GB) over the internet may be slow; see needs-decision §4.1. |

### 3.4 Local Preview of Selected Media

**Why client:** The current "preview" is only metadata display (duration, dimensions, warnings from the upload response). No video playback is implemented. This remains a display-only client-side concern.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `_update_video_preview` (line 2748), `_format_upload_meta`, `_format_upload_warnings` |
| Backend API dependency | Reads `Upload` fields from `POST /api/uploads/` response |
| Migration risk | **Low** |

### 3.5 Local User Preferences That Are Not Secrets

**Why client:** Theme selection, accent color preset, and window geometry are purely local UI preferences.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `_load_desktop_settings`, `_save_desktop_settings`, `apply_selected_theme`, `_apply_theme_to_existing_widgets`, `_select_accent_preset` |
| Storage | `storage/desktop_settings.json` (local file, currently at `USER_DATA_ROOT/desktop_settings.json`) |
| Backend API dependency | None |
| Migration risk | **Low** |

### 3.6 Calling Backend API Endpoints

**Why client:** The `ApiClient` class is the correct boundary point — it abstracts all HTTP calls to the backend.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py:815` — `ApiClient` class |
| Key methods | `login`, `oauth_statuses`, `oauth_connect_url`, `oauth_disconnect`, `oauth_test_credentials`, `get_profile`, `update_profile`, `get_staged`, `update_staged`, `clear_staged`, `publish_staged`, `publish_now`, `upload_file` |
| Change needed | `API_BASE` is hardcoded to `http://localhost:8100/api` at line 205. After migration this must read `REELPUSH_API_BASE` env var pointing to `https://your-oci-domain/api`. The env var already exists; desktop just needs to ship with the right default or be configurable. |
| Migration risk | **Low** — `ApiClient` is already clean. Only needs its base URL updated. |

### 3.7 Displaying Job Status, Results, and Errors

**Why client:** Polling job state and rendering results in the UI is a client concern.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `_poll_results`, `_show_publish_results`, `_apply_runtime_statuses`, `_apply_account_statuses`, `_apply_delivery_readiness`, `_set_readiness_item` |
| Backend API dependency | `GET /api/jobs/`, `GET /api/jobs/{id}`, `GET /api/oauth/status`, `GET /api/workspace/staged` |
| Migration risk | **Low** |

### 3.8 Docker Lifecycle Management (REMOVE after migration)

**Why client today:** The desktop currently owns `docker compose up/down/build` and Docker health checks because the backend runs locally. After migration this goes away entirely.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `_start_local_services` (line 1341), `_refresh_backend_service` (line 1354), `_wait_for_docker_ready`, `_docker_executable`, `_open_docker_desktop`, `show_bootstrap`, `_collect_runtime_statuses` (Docker portion lines 1403–1434), `_run_bootstrap_command`, `_compose_error_needs_build` |
| After migration | Desktop only needs `GET /api/health` to confirm the backend is reachable. All Docker management code can be removed or guarded behind a `REELPUSH_LOCAL_MODE` flag. |
| Migration risk | **Medium** — removing this changes the bootstrap UX significantly. The desktop will no longer need Docker installed at all. |

### 3.9 `.env` Credential Write-Through (REMOVE after migration)

**Why client today:** Platform credentials are written to the local `.env` and Docker is restarted. This is only possible because the backend is on the same machine.

| Field | Detail |
|-------|--------|
| Current files | `reelpush_desktop.py` — `save_platform_credentials` (line 3269), `_write_local_env_values` (line 314), `_load_local_env` (line 300), `reload_credential_fields` (line 3261), `post_delete_test_platform_credentials` (line 3293) |
| After migration | Credentials are set on OCI via a systemd `EnvironmentFile` at deploy time. The Settings tab in the desktop either becomes read-only (showing which platforms are configured) or is replaced by a backend `PUT /api/settings/credentials` endpoint. See §4.6. |
| Migration risk | **High** — this is the most invasive desktop change. The entire Settings tab's credential section needs to be redesigned. |

---

## 4. Needs-Decision Items

### 4.1 Media Upload: Immediately on File Select or At Publish Time?

| Field | Detail |
|-------|--------|
| Decision needed | Currently the desktop uploads the video to the server immediately when the user picks a file (`choose_video` → `client.upload_file`). On OCI this means streaming potentially large MP4s over the internet immediately. |
| Options | (A) Keep current behavior — upload immediately on file select. (B) Upload only at publish time. |
| Recommended choice | **A — keep current behavior** |
| Reason | The server already assigns an `upload_id` used throughout the staged/publish flow. Uploading early allows server-side validation and metadata extraction before the user fills in post details, giving better UX. Use a progress indicator. |
| Risk if chosen wrong | If B, the publish flow changes substantially and the staged workspace model loses meaning. |

### 4.2 Scheduled Publishing: Desktop UI or Server-Side Cron?

| Field | Detail |
|-------|--------|
| Decision needed | Desktop has a `schedule_var` input but it's explicitly blocked (line 3698): "Scheduled desktop publishing is not wired into this local flow yet." The backend has full Celery ETA support. |
| Options | (A) Desktop sends `scheduled_for` UTC datetime to `POST /api/jobs/`. Server handles all scheduling via Celery ETA + beat fallback. (B) Desktop keeps its own timer and fires the API call when due. |
| Recommended choice | **A — server-side only** |
| Reason | The desktop may be closed or the machine may sleep. Server-side scheduling is the only reliable path. |
| Risk if chosen wrong | B would lose scheduled posts whenever the desktop is not running. |

### 4.3 Logs: Server-Side Only, or Display in Desktop?

| Field | Detail |
|-------|--------|
| Decision needed | Should the desktop surface backend logs? |
| Options | (A) Server logs stay on OCI (systemd journal / log files), desktop only shows job audit log via `GET /api/jobs/{id}/audit`. (B) Stream logs to the desktop via a log endpoint. |
| Recommended choice | **A** — audit log per job is sufficient for the desktop user. Raw server logs are an ops concern. |
| Risk if chosen wrong | Low. Can add streaming later if needed. |

### 4.4 Drafts / Staged State: Local or Server?

| Field | Detail |
|-------|--------|
| Current state | `StagedPublish` model is already server-side. The desktop calls `PUT /api/workspace/staged` to save staged state. |
| Decision needed | None — this is already correct. |
| Recommendation | Keep on server. |
| Risk | Low. |

### 4.5 OAuth Login: Desktop-Initiated or Server-Initiated?

| Field | Detail |
|-------|--------|
| Current state | Desktop calls `GET /api/oauth/{platform}/connect-url`, gets back the authorization URL, opens it in the user's browser via `webbrowser.open`. The platform redirects to `{API_URL}/api/oauth/{platform}/callback` which is handled by the backend. Desktop then polls `/api/oauth/status` to detect completion. |
| Problem | After OCI migration, `API_URL` in the backend `.env` must be the OCI HTTPS domain (e.g. `https://reelpush.example.com`). The redirect URI registered in each platform's developer console must also point to this domain. |
| Recommendation | Keep the current pattern (desktop opens browser, server receives callback). Update `API_URL` on OCI. Update redirect URIs in each platform developer console. |
| Risk if ignored | **High** — OAuth callbacks to `localhost:8100` will fail from any non-local desktop. |

### 4.6 Platform Credential Management: Deploy-Time or Runtime API?

| Field | Detail |
|-------|--------|
| Decision needed | The current desktop Settings tab lets the user type in platform API keys, saves them to the local `.env`, and restarts Docker. After OCI migration this pattern breaks entirely. |
| Options | (A) Credentials are set in the OCI server's systemd `EnvironmentFile` at deploy time (one-time setup by the operator). The desktop Settings tab becomes read-only — it shows which platforms are configured but cannot change them. (B) Add a `PUT /api/settings/credentials` backend endpoint (protected by admin JWT) that allows the desktop to push new credential values which the server stores in its database and loads at runtime. |
| Recommended choice | **A for now** — set credentials via systemd EnvironmentFile, simplify the desktop Settings tab. Add option B later if there's a need to change credentials without SSH access. |
| Reason | The target is a personal single-operator tool. SSH access to set env vars is acceptable. Option B adds complexity (server restart or hot-reload logic). |
| Risk if chosen wrong | If A, user must SSH to change credentials. If B, poorly secured credential API is a severe security risk. |

### 4.7 Per-Platform Account Ownership: Local Install, User, or Workspace?

| Field | Detail |
|-------|--------|
| Current state | `PlatformAccount` has `owner_id` FK to `AdminUser`. There is one `AdminUser` (seeded via `scripts/seed_admin.py`). There is no workspace/multi-user concept. |
| Decision needed | Is this a single-user personal tool (one OCI server, one admin account) or multi-user? |
| Recommended choice | **Single-user for now.** The `AdminUser` model is sufficient. |
| Risk | Low. Multi-user can be added later as a separate feature. |

---

## 5. Current Architecture Map

| File / Path | Current Role | Should Become | Notes |
|---|---|---|---|
| `reelpush_desktop.py` | Monolithic desktop: UI + Docker mgmt + API client + `.env` writes | **Client** | Remove Docker mgmt sections; remove credential write-through; update `API_BASE` |
| `reelpush_desktop.py:815` — `ApiClient` | HTTP client to backend API | **Client** (keep, update base URL) | Already correct boundary |
| `reelpush_desktop.py:1341` — `_start_local_services` | `docker compose up` | **Remove** | Replaced by OCI systemd |
| `reelpush_desktop.py:1354` — `_refresh_backend_service` | `docker compose up --force-recreate` | **Remove** | Used only after credential save |
| `reelpush_desktop.py:1376` — `_collect_runtime_statuses` | `docker compose ps` + health check | **Client** (keep health check only) | Remove Docker portion; keep API health + internet checks |
| `reelpush_desktop.py:1715` — `show_bootstrap` | Docker startup wizard | **Remove** | Desktop just needs `/api/health` |
| `reelpush_desktop.py:3269` — `save_platform_credentials` | Writes secrets to `.env`, restarts Docker | **Remove** | Credentials move to OCI `EnvironmentFile` |
| `reelpush_desktop.py:300` — `_load_local_env` / `_write_local_env_values` | Reads/writes `.env` | **Remove** | `.env` management stays on OCI server |
| `reelpush_desktop.py:510` — `_load_desktop_settings` | Local JSON prefs | **Client** (keep) | Theme, accent — purely local |
| `backend/app/main.py` | FastAPI entry point | **Server** | No change |
| `backend/app/api/routes/auth.py` | JWT login | **Server** | No change |
| `backend/app/api/routes/oauth.py` | OAuth flow | **Server** | Update `_pending_states` to Redis |
| `backend/app/api/routes/uploads.py` | File receive + validate + store | **Server** | No change |
| `backend/app/api/routes/jobs.py` | Publish job CRUD | **Server** | No change |
| `backend/app/api/routes/workspace.py` | Staged + profile | **Server** | No change |
| `backend/app/services/oauth_service.py` | Token exchange/refresh/storage | **Server** | Move `_pending_states` to Redis |
| `backend/app/services/publish_service.py` | Job creation + Celery dispatch | **Server** | No change |
| `backend/app/services/media_service.py` | Video validation + storage + thumbnails | **Server** | Must provision S3/R2 for Instagram |
| `backend/app/services/storage_providers.py` | Storage backend abstraction | **Server** | No change |
| `backend/app/services/workspace_service.py` | Staged/profile helpers | **Server** | No change |
| `backend/app/workers/tasks.py` | Celery publish task + beat poller | **Server** | No change |
| `backend/app/workers/celery_app.py` | Celery application | **Server** | No change |
| `backend/app/models/models.py` | ORM models | **Server** | No change |
| `backend/app/core/config.py` | pydantic-settings | **Server** | `API_URL` must change to OCI domain |
| `backend/app/core/security.py` | JWT + token encryption | **Server** | `SECRET_KEY` must be a strong random value on OCI |
| `backend/app/db/migrations/` | Alembic migrations | **Server** | Run at OCI deploy time |
| `docker-compose.yml` | Local dev orchestration | **Server** (local dev only) | OCI uses systemd units instead |
| `backend/Dockerfile` | Container image | **Server** (OCI build artifact) | Used by systemd service or Docker on OCI |
| `storage/uploads/` | Local video files | **Server** | Move to OCI volume or S3 |
| `storage/desktop_settings.json` | Local desktop prefs | **Client** | Keep in `USER_DATA_ROOT` |
| `backend/scripts/seed_admin.py` | Admin user seed | **Server** (one-time) | Run once on OCI at first deploy |
| `backend/scripts/local_publish.py` | Local CLI publish | **Shared / Remove** | Dev tool; not needed in production |
| `backend/scripts/publish_staged.py` | CLI staged publish | **Shared / Remove** | Dev tool |
| `backend/tests/` | Backend tests | **Server** | Run in CI before deploying to OCI |
| `{backend/{app` (stray path) | Apparent file system artifact | **Remove** | Likely a misnamed directory; investigate |

---

## 6. Proposed API Boundary

All endpoints already exist in the backend. The desktop must call these after migration. Auth token is a JWT from `/api/auth/login` sent as `Authorization: Bearer <token>` on every request.

| Method + Path | Called by Desktop? | Backend Responsibility | Request | Response | Auth / Security Notes |
|---|---|---|---|---|---|
| `POST /api/auth/login` | Yes | Issue JWT | `{email, password}` | `{access_token}` | Credentials in JSON body, HTTPS required |
| `GET /api/auth/me` | Yes (on boot) | Return current user | — | `AdminUserOut` | Bearer token |
| `GET /api/oauth/status` | Yes (polling) | Platform connection status for all platforms | — | `list[PlatformStatusOut]` | Bearer token |
| `GET /api/oauth/{platform}/connect-url` | Yes | Return OAuth authorization URL | — | `{authorization_url, redirect_uri}` | Bearer token; desktop opens URL in browser |
| `GET /api/oauth/{platform}/callback` | No (platform → server) | Receive OAuth code, exchange tokens, store | `?code=&state=` query params | HTML completion page | State CSRF check; no auth header (public redirect target) |
| `DELETE /api/oauth/{platform}/disconnect` | Yes | Remove stored platform account | — | `{detail}` | Bearer token |
| `POST /api/oauth/{platform}/test` | Yes | Verify stored credentials against platform API | `{}` body | `{success, message, ...}` | Bearer token |
| `POST /api/oauth/{platform}/post-delete-test` | Yes | Post private video + delete to verify publish permission | `{}` body | `{success, status, message}` | Bearer token; use with caution |
| `GET /api/oauth/complete` | No (browser redirect) | Show OAuth success/failure HTML page | `?connected=` or `?oauth_error=` | HTML | No auth |
| `POST /api/uploads/` | Yes | Receive video file, validate, store, extract metadata, thumbnail | `multipart/form-data` video file | `UploadOut` | Bearer token; large payload over HTTPS |
| `GET /api/uploads/` | Yes | List user uploads | `?limit=&offset=` | `list[UploadOut]` | Bearer token |
| `GET /api/uploads/{upload_id}` | Yes | Get single upload | — | `UploadOut` | Bearer token |
| `POST /api/jobs/` | Yes | Create + enqueue single publish job | `PublishJobCreate` | `PublishJobOut` | Bearer token |
| `POST /api/jobs/bulk` | Yes | Create one job per platform for one upload | `BulkPublishRequest` | `list[PublishJobOut]` | Bearer token |
| `GET /api/jobs/` | Yes | List publish jobs | `?limit=&offset=` | `list[PublishJobOut]` | Bearer token |
| `GET /api/jobs/stats` | Optional | Dashboard stats | — | `DashboardStats` | Bearer token |
| `GET /api/jobs/{job_id}` | Yes | Get job status | — | `PublishJobOut` | Bearer token |
| `POST /api/jobs/{job_id}/retry` | Yes | Re-enqueue failed job | — | `PublishJobOut` | Bearer token |
| `POST /api/jobs/{job_id}/cancel` | Yes | Cancel queued job | — | `PublishJobOut` | Bearer token |
| `GET /api/jobs/{job_id}/audit` | Yes | Job audit trail | — | `list[AuditLogOut]` | Bearer token |
| `GET /api/workspace/profile` | Yes | Get default publish profile | — | `PublishProfileOut` | Bearer token |
| `PUT /api/workspace/profile` | Yes | Update default publish profile | `PublishProfileUpdate` | `PublishProfileOut` | Bearer token |
| `GET /api/workspace/staged` | Yes | Get current staged publish state | — | `StagedPublishOut` | Bearer token |
| `PUT /api/workspace/staged` | Yes | Update staged upload + selected platforms | `StagedPublishUpdate` | `StagedPublishOut` | Bearer token |
| `DELETE /api/workspace/staged` | Yes | Clear staged publish | — | `StagedPublishOut` | Bearer token |
| `POST /api/workspace/publish-now` | Yes | Publish staged state immediately | `WorkspacePublishRequest` | `list[PublishJobOut]` | Bearer token |
| `GET /api/health` | Yes (periodic) | Server heartbeat | — | `{status, environment}` | No auth |

---

## 7. Data and Secrets Boundary

| Data Item | Never in Frontend | May Cache Locally | OCI Only | OK in .env | DB / Storage |
|---|---|---|---|---|---|
| `SECRET_KEY` (JWT signing + token encryption) | ✓ | | ✓ (OCI EnvironmentFile) | ✓ (OCI only) | |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | ✓ | | ✓ | ✓ (OCI only) | |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | ✓ | | ✓ | ✓ (OCI only) | |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | ✓ | | ✓ | ✓ (OCI only) | |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | ✓ | | ✓ | ✓ (OCI only) | |
| `DATABASE_URL` | ✓ | | ✓ | ✓ (OCI only) | |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` (initial seed) | ✓ | | ✓ | ✓ (OCI, change after seed) | |
| Platform access tokens (decrypted) | ✓ | | ✓ | | DB encrypted |
| Platform refresh tokens (decrypted) | ✓ | | ✓ | | DB encrypted |
| Platform access tokens (encrypted at rest) | ✓ | | ✓ | | DB (`access_token_encrypted`) |
| JWT access token (desktop session) | | ✓ (in-memory only) | | | Issued by server |
| `admin_email` shown in desktop UI | | ✓ (read from `/api/auth/me`) | | | |
| Upload `id` (UUID) | | ✓ (desktop caches current upload) | | | DB |
| Upload metadata (duration, dimensions, warnings) | | ✓ (display only) | | | DB |
| Staged publish state | | ✓ (display cache) | | | DB (`staged_publishes`) |
| Default publish profile | | ✓ (display cache) | | | DB (`publish_profiles`) |
| Platform connection status | | ✓ (polled) | | | DB + live platform check |
| Video files (uploaded) | | | ✓ (OCI `./storage` or S3) | | Storage |
| Thumbnails | | | ✓ | | Storage |
| Publish job history | | | ✓ | | DB (`publish_jobs`) |
| Audit log | | | ✓ | | DB (`audit_logs`) |
| Desktop theme / accent color | | | | | `desktop_settings.json` (local) |
| Desktop `API_BASE` URL | | ✓ (desktop env var) | | | |

---

## 8. Migration Plan Impact — Safest Order

### Step 1: Backend Boot and API Health on OCI

**Why first:** Everything else depends on a running backend. Validate the systemd service, Caddy HTTPS proxy, and database connectivity before touching any feature.

**What must pass before moving on:**
- `GET https://reelpush.example.com/api/health` returns `{"status": "ok"}`
- Alembic migrations applied: `alembic upgrade head`
- Admin seed run: `python backend/scripts/seed_admin.py`
- TLS cert valid (Caddy auto or certbot)

### Step 2: DB and State Layer Verification

**Why second:** Without confirmed database connectivity (PostgreSQL + Alembic schema) all other tests are meaningless.

**What must pass:**
- `GET /api/auth/me` returns 401 (not 500) — DB is up
- `POST /api/auth/login` with seeded credentials returns a JWT
- `GET /api/workspace/profile` with JWT returns a profile

### Step 3: Media Upload and Serving

**Why third:** Uploading is the first user action. If uploads are broken, nothing else works.

**What must pass:**
- `POST /api/uploads/` with a test MP4 returns a 201 `UploadOut` with `id`, `storage_key`, `duration_seconds`
- For YouTube/TikTok: `GET /media/uploads/{storage_key}` (local) is accessible from the server
- For Instagram: `storage_backend=s3` and `instagram_media_url_configured` is True; test with a public S3/R2 URL

### Step 4: OAuth / Token Handling

**Why fourth:** Platform credentials must be verified before any publish test.

**What must pass:**
- Move `_pending_states` from in-memory dict to Redis (`oauth_service.py:31`) before this step
- `GET /api/oauth/{platform}/connect-url` returns a valid authorization URL (not 400 missing-credentials)
- Complete the browser OAuth flow: platform redirects to `https://reelpush.example.com/api/oauth/{platform}/callback`
- `GET /api/oauth/status` shows `connected=true` for the tested platform
- `POST /api/oauth/{platform}/test` returns `{success: true}`
- Redirect URIs in each platform developer console updated to OCI HTTPS domain

### Step 5: Publishing Endpoints

**Why fifth:** Only run publish tests after upload + OAuth are confirmed working.

**What must pass:**
- `POST /api/jobs/` creates a job with status `queued`
- Celery worker picks up the job (check systemd journal)
- Job transitions to `posted` in the database (poll `GET /api/jobs/{id}`)
- Platform post appears in the account (manual verification)
- TikTok: may land in `requires_manual` (creator mode) — this is expected

### Step 6: Desktop API Client Rewiring

**Why sixth:** Only update the desktop after the OCI backend is confirmed working end-to-end.

**What must change in the desktop:**
- Set `REELPUSH_API_BASE=https://reelpush.example.com/api` (env var or desktop settings)
- Remove Docker bootstrap screen (`show_bootstrap`, `_start_local_services`, `_refresh_backend_service`)
- Remove Docker section from `_collect_runtime_statuses` (keep API health + internet checks)
- Remove `save_platform_credentials` and its `.env` write-through
- Simplify Settings tab to show OCI server URL and platform connection status (read-only)

**What must pass:**
- Desktop connects to OCI and auto-logins
- Platform status panel shows correct connection states from OCI

### Step 7: Desktop UI Rewiring

**Why seventh:** UI cleanup after the API client is working.

**Changes:**
- Remove Docker-specific service status indicators from the Service Overview panel
- Update bootstrap/splash to show "Connecting to ReelPush server…" instead of Docker startup
- Settings tab: replace credential entry fields with instructions for OCI env setup (or add a backend settings API — see §4.6)

### Step 8: Scheduling and Jobs

**Why eighth:** Scheduling is the last major feature, depends on Celery beat running correctly on OCI.

**What must pass:**
- Wire desktop `schedule_var` into `scheduled_for` field of `POST /api/jobs/` payload
- `POST /api/jobs/` with `scheduled_for` in the future creates a job with status `scheduled`
- Celery beat `poll_scheduled_jobs` task fires and dispatches the job at the correct UTC time
- Desktop polls `GET /api/jobs/{id}` and shows `scheduled` → `posted` transition

### Step 9: Final Cleanup

**What to do:**
- Remove `_load_local_env`, `_write_local_env_values`, `_ensure_local_env_file`, `_ensure_desktop_api_url` from `reelpush_desktop.py`
- Remove `_docker_executable`, `_docker_desktop_app`, `_wait_for_docker_ready`, `_open_docker_desktop`, `DOCKER_PATHS`, `DOCKER_CHECK_CANDIDATES` from `reelpush_desktop.py`
- Archive or remove `backend/scripts/local_publish.py`, `backend/scripts/publish_staged.py`
- Remove the `{backend/{app` stray directory (line 118 in find output — apparent shell expansion artifact)
- Update `OAUTH_REDIRECT_URIS` dict in `reelpush_desktop.py:210` to use OCI domain

---

## 9. Final Recommendation

### What to migrate first
1. **Provision OCI** — install Ubuntu, Docker (or native systemd), PostgreSQL, Redis, Caddy
2. **Deploy the backend as-is** — no code changes needed for the backend itself to run on OCI
3. **Fix the `_pending_states` OAuth bug** — move from in-memory dict to Redis before any real OAuth testing
4. **Set all credentials in OCI EnvironmentFile** — never bring them back to the desktop
5. **Test uploads → OAuth → publish on OCI** before touching the desktop

### What to not touch yet
- Do not change `ApiClient` or any publishing UI until OCI backend is confirmed end-to-end
- Do not change the database schema (all three migrations are already applied)
- Do not provision S3/R2 until OCI basic publish is working (YouTube/TikTok can use local storage initially; Instagram is a separate effort)

### What should be handled by Codex (large implementation)
- Moving `_pending_states` from in-memory to Redis in `oauth_service.py` (straightforward but touches the OAuth callback flow)
- Stripping Docker lifecycle code from `reelpush_desktop.py` (several interconnected methods, ~400–500 lines of removal)
- Rewriting the Settings tab credential section into a read-only OCI status display

### What should be handled by Claude directly
- All OCI server provisioning and systemd unit file authoring (config files, not code)
- Updating `API_URL` in `.env` and `OAUTH_REDIRECT_URIS` in the desktop
- Caddy configuration for HTTPS proxying and media serving

### What should be saved for Opus final review
- The OAuth redirect URI update across all three platform developer consoles (platform console configuration, not code)
- Security review of the `SECRET_KEY` rotation plan and encrypted-token migration
- Final end-to-end smoke test of all three platforms (YouTube Shorts, Instagram Reels, TikTok) on the live OCI server
- Review of whether `POST /api/workspace/publish-now` vs `POST /api/jobs/` should be the canonical publish path in the desktop

---

_End of catalog. 10 server responsibilities catalogued. 9 client responsibilities catalogued. 7 needs-decision items raised._
