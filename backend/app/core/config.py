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
    admin_password: str = "changeme123!"

    # App URLs
    api_url: str = "http://localhost:8000"

    # TikTok — TODO: requires developer app approval
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    # Instagram / Meta — TODO: requires Meta app review + Business account
    instagram_app_id: str = ""
    instagram_app_secret: str = ""

    # YouTube / Google — TODO: requires Google Cloud credentials
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
