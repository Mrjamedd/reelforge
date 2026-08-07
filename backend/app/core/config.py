from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    secret_key: str = "dev-insecure-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    sqlalchemy_echo: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://reelpush:reelpush@localhost:5432/reelpush"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "./storage"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    r2_account_id: str = ""  # Optional: set S3_ENDPOINT_URL automatically for R2

    # Admin seed
    admin_email: str = "admin@example.com"
    admin_password: str | None = None

    # App URLs
    api_url: str = "http://localhost:8000"

    # TikTok — TODO: requires developer app approval
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    # Instagram / Meta — TODO: requires Meta app review + Business account
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    # Override via META_GRAPH_API_VERSION to match the version shown in the
    # Meta Developer dashboard / Graph API Upgrade Tool.
    meta_graph_api_version: str = "v21.0"

    # YouTube / Google — TODO: requires Google Cloud credentials
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

    # SMTP — for email verification
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""

    @property
    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)

    @property
    def instagram_configured(self) -> bool:
        return bool(self.instagram_app_id and self.instagram_app_secret)

    @property
    def instagram_media_url_configured(self) -> bool:
        """
        True when a real public URL provider is configured for Instagram publishing.
        Local storage is NOT sufficient - S3 or R2 credentials must be present.
        """
        if self.storage_backend != "s3":
            return False
        return bool(
            self.s3_bucket and self.aws_access_key_id and self.aws_secret_access_key
        )

    @property
    def youtube_configured(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_client_secret)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Runtime platform credential overrides (DB values take precedence over env vars).
_runtime_creds: dict[str, str] = {}


def set_runtime_creds(creds: dict[str, str]) -> None:
    """Override platform credentials at runtime without restarting the server."""
    for k, v in creds.items():
        if v:
            _runtime_creds[k] = v


def get_effective_cred(attr_name: str) -> str:
    """Return a platform credential, preferring runtime DB overrides over env vars."""
    return _runtime_creds.get(attr_name) or getattr(get_settings(), attr_name, "")
