"""
Storage Provider Abstraction
============================
Defines how media files are served as public URLs for platform publishing.

Instagram requires a publicly accessible HTTPS URL. Local storage is NOT
publicly accessible and will block Instagram publishing with a clear error.

Use get_storage_provider() to get the provider matching current settings.
MockStorageProvider is for tests only - never returned by get_storage_provider().
"""

from __future__ import annotations

from abc import ABC, abstractmethod


_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"", "changeme", "change-me", "change_me", "xxx", "placeholder", "your-key-here", "todo"}
)


def _is_placeholder(value: str) -> bool:
    return not value or value.lower().strip() in _PLACEHOLDER_VALUES


class StorageProvider(ABC):
    """Abstract base - implementations return public media URLs for platform publishing."""

    @property
    @abstractmethod
    def is_publicly_accessible(self) -> bool:
        """True when get_public_url() returns a URL reachable from the public internet."""
        ...

    @property
    @abstractmethod
    def configuration_errors(self) -> list[str]:
        """
        Human-readable errors describing what is missing or misconfigured.
        Empty list means the provider is ready to use.
        Never include raw credential values in error text.
        """
        ...

    @abstractmethod
    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a URL for the given storage key. May be time-limited (presigned)."""
        ...


class LocalStorageProvider(StorageProvider):
    """Serves files from the local backend API. NOT publicly accessible - blocks Instagram."""

    def __init__(self, api_url: str) -> None:
        self._api_url = api_url

    @property
    def is_publicly_accessible(self) -> bool:
        return False

    @property
    def configuration_errors(self) -> list[str]:
        return [
            "Local storage is not publicly accessible. "
            "Instagram requires a public HTTPS URL for video uploads. "
            "Set STORAGE_BACKEND=s3 and configure S3_BUCKET, AWS_ACCESS_KEY_ID, "
            "AWS_SECRET_ACCESS_KEY in .env (or use Cloudflare R2 via S3_ENDPOINT_URL)."
        ]

    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._api_url}/media/{key}"


class S3StorageProvider(StorageProvider):
    """
    Generates presigned time-limited URLs from S3 or any S3-compatible store
    (Cloudflare R2, MinIO, etc.) via endpoint_url.

    Files are NOT publicly listable - presigned URLs grant object-level access only.
    Credentials are never logged or included in generated URLs.
    """

    def __init__(
        self,
        *,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        endpoint_url: str = "",
    ) -> None:
        self._bucket = bucket
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region
        self._endpoint_url = endpoint_url

    @property
    def is_publicly_accessible(self) -> bool:
        return not self.configuration_errors

    @property
    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if _is_placeholder(self._bucket):
            errors.append("S3_BUCKET is missing or not configured.")
        if _is_placeholder(self._access_key_id):
            errors.append("AWS_ACCESS_KEY_ID is missing or not configured.")
        if _is_placeholder(self._secret_access_key):
            errors.append("AWS_SECRET_ACCESS_KEY is missing or not configured.")
        return errors

    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        import boto3

        s3 = boto3.client(
            "s3",
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            **({"endpoint_url": self._endpoint_url} if self._endpoint_url else {}),
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


class MockStorageProvider(StorageProvider):
    """
    Test-only provider. Returns deterministic HTTPS URLs without any network calls.
    Never instantiated by get_storage_provider() in production code.
    """

    def __init__(self, base_url: str = "https://test-media.example.com") -> None:
        self._base_url = base_url.rstrip("/")

    @property
    def is_publicly_accessible(self) -> bool:
        return True

    @property
    def configuration_errors(self) -> list[str]:
        return []

    async def get_public_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._base_url}/{key}?expires={expires_in}"


def get_storage_provider() -> StorageProvider:
    """
    Factory: returns the StorageProvider matching the current STORAGE_BACKEND setting.
    Call once per request/job - does not cache the instance.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3StorageProvider(
            bucket=settings.s3_bucket,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
            region=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url,
        )
    return LocalStorageProvider(api_url=settings.api_url)
