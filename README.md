# ReelPush 🎬

A production-minded multi-platform short-form video publishing framework.
Upload once → publish to TikTok, Instagram Reels, and YouTube Shorts from the local desktop app.

---

## Architecture Overview

```
reelpush/
├── backend/          # FastAPI application
│   └── app/
│       ├── api/      # HTTP route handlers
│       ├── core/     # Config, security, auth
│       ├── db/       # SQLAlchemy models, Alembic migrations
│       ├── models/   # ORM models
│       ├── schemas/  # Pydantic request/response models
│       ├── services/ # Business logic layer
│       ├── providers/ # Platform adapter pattern (TikTok, Instagram, YouTube)
│       └── workers/  # Celery background job handlers
├── reelpush_desktop.py # Local desktop UI
├── infra/            # Docker, Nginx configs
└── scripts/          # Dev/migration helpers
```

### Key Design Decisions

- **FastAPI** for async-first Python API
- **PostgreSQL + SQLAlchemy 2.0 + Alembic** for relational storage and migrations
- **Celery + Redis** for async background job processing (publish jobs, retries, scheduling)
- **Provider Adapter Pattern** — each platform implements a common `PlatformProvider` interface
- **Object Storage Abstraction** — local filesystem in dev, S3-compatible in production
- **JWT-based admin auth** with bcrypt password hashing

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing secret (generate with `openssl rand -hex 32`) |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `STORAGE_BACKEND` | `local` or `s3` |
| `S3_BUCKET` | S3 bucket name (if `STORAGE_BACKEND=s3`) |
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `AWS_REGION` | AWS region |
| `TIKTOK_CLIENT_KEY` | TikTok developer app client key |
| `TIKTOK_CLIENT_SECRET` | TikTok developer app client secret |
| `INSTAGRAM_APP_ID` | Meta/Instagram app ID |
| `INSTAGRAM_APP_SECRET` | Meta/Instagram app secret |
| `META_GRAPH_API_VERSION` | Meta Graph API version for Instagram OAuth/publishing, e.g. `v21.0`. Keep this on a supported version from the Meta Developer dashboard/API Upgrade Tool. |
| `YOUTUBE_CLIENT_ID` | Google OAuth2 client ID |
| `YOUTUBE_CLIENT_SECRET` | Google OAuth2 client secret |
| `ADMIN_EMAIL` | Initial admin account email |
| `ADMIN_PASSWORD` | Initial admin account password |
| `API_URL` | Backend base URL. Docker Compose exposes the local API at `http://localhost:8100`. |
| `SQLALCHEMY_ECHO` | Optional SQL debug logging. Keep `false` unless actively debugging because SQL logs may include user identifiers. |

---

## Local Development Setup

### Prerequisites
- Docker + Docker Compose
- Python 3.11+
- `ffmpeg` and `libmagic` if you run the backend directly outside Docker

### Start everything

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your values

# 2. Start infrastructure (Postgres, Redis)
docker-compose up -d db redis

# 3. Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python scripts/seed_admin.py   # creates initial admin user
uvicorn app.main:app --reload --port 8000

# 4. Celery worker (separate terminal)
cd backend
celery -A app.workers.celery_app worker --loglevel=info

# 5. Celery beat scheduler (separate terminal)
cd backend
celery -A app.workers.celery_app beat --loglevel=info

# 6. Desktop app
cd ..
python3 reelpush_desktop.py
```

### Or use Docker Compose for local services

```bash
docker-compose up --build
```

The supported UI is the local desktop app:

```bash
Open ReelPush.command
```

---

## Desktop App

ReelPush now includes a local desktop client as the primary interface.

Open it with:

```bash
Open ReelPush.command
```

The desktop app:

- starts the local backend services automatically
- shows a login screen for the local admin account
- separates `Publishing` and `Accounts`
- saves title, caption, hashtags, privacy, and staged upload in the database
- publishes directly from the desktop UI

Advanced shell entrypoints still exist if you want them:

- `./start-reelpush.sh`
- `./publish-reelpush.sh`

Saved publishing details now persist in the database, so title, caption, hashtags, and privacy survive app restarts for each admin user.

Current limitations:

- `YouTube` is the only fully automatic local-mode target in this scaffold
- `Instagram` needs a publicly reachable video URL, so local filesystem storage is not enough by itself
- `TikTok` still needs a real upload implementation plus approved app scopes

---

## How Platform Connections Work

Each platform uses OAuth 2.0. The flow:

1. Admin clicks "Connect [Platform]" in the desktop app
2. Backend generates an OAuth authorization URL and redirects the user
3. Platform redirects back to `/api/oauth/[platform]/callback` with an auth code
4. Backend exchanges the code for access + refresh tokens
5. Tokens are stored encrypted in the database
6. The platform shows as "Connected" in the desktop app

Token refresh is handled automatically before each publish job.

---

## Implementation Status

| Feature | Status |
|---|---|
| Admin auth (JWT) | ✅ Fully implemented |
| Video upload + validation | ✅ Fully implemented |
| Thumbnail generation | ✅ Fully implemented (ffmpeg) |
| Job queue (Celery + Redis) | ✅ Fully implemented |
| Scheduling (publish later) | ✅ Fully implemented |
| Audit log / history | ✅ Fully implemented |
| Provider adapter interface | ✅ Fully implemented |
| TikTok OAuth flow | ✅ Scaffolded (official API structure) — **pending TikTok app approval** |
| TikTok video publish | ✅ Scaffolded — **pending credentials + app review** |
| Instagram OAuth flow | ✅ Scaffolded (official Meta API structure) — **pending Meta app review** |
| Instagram Reels publish | ✅ Scaffolded — **pending credentials + Business account requirement** |
| YouTube OAuth flow | ✅ Scaffolded (official Google API structure) — **pending Google credentials** |
| YouTube Shorts publish | ✅ Scaffolded — **pending credentials** |
| S3 storage | ✅ Scaffolded — **needs real AWS credentials** |
| Local desktop app | ✅ Fully implemented |

---

## What Still Requires Platform Approval or Credentials

### TikTok
- Apply for developer access at https://developers.tiktok.com
- Your app must be approved for the `video.upload` and `video.publish` scopes
- Business accounts may require additional review
- Set `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` once approved

### Instagram / Meta
- Create a Meta Developer app at https://developers.facebook.com
- App must be approved for `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`
- Requires a Facebook Page linked to the Instagram Business/Creator account
- Set `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET` once approved
- Set `META_GRAPH_API_VERSION` to a supported Meta Graph API version and redeploy the backend after changing it
- Register the exact redirect URI for the server the desktop app uses: `{API_URL}/api/oauth/instagram/callback`. Avoid ephemeral tunnel URLs for production because a changed host breaks OAuth until Meta is updated.

### YouTube
- Create a project in Google Cloud Console at https://console.cloud.google.com
- Enable the YouTube Data API v3
- Create OAuth 2.0 credentials (Web Application type)
- Add your redirect URI: `{API_URL}/api/oauth/youtube/callback`. For the default desktop Docker setup, use `http://localhost:8100/api/oauth/youtube/callback`.
- Set `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`
- If Google shows `Error 400: redirect_uri_mismatch`, open ReelPush Settings and copy the displayed Google redirect URI. It must match the Google Cloud authorized redirect URI exactly, including `localhost` vs `127.0.0.1`, the port, path, scheme, and trailing slash.
- YouTube Shorts classification depends on current YouTube rules. ReelPush validates and warns on source duration, orientation, and resolution before publishing.

---

## Future Expansion Points

- **Multiple accounts per platform** — DB schema supports it (`platform_accounts` table has no unique constraint on platform alone); UI just needs a multi-account selector
- **Analytics view** — Add a `post_analytics` table; each provider's `getPostStatus()` can return view/like/comment counts
- **Bulk caption variations** — Add a `caption_variants` table linked to uploads; let users A/B test captions per platform
- **Additional platforms** — Implement `PinterestProvider` and `SnapchatProvider` following the same `PlatformProvider` interface in `providers/`
- **Transcoding pipeline** — The `MediaService` has a `transcode()` stub; plug in `ffmpeg-python` jobs via Celery
- **Webhook callbacks** — Platforms like TikTok can push status updates; add `/api/webhooks/[platform]` routes
