"""
Media Service
=============
Handles video upload, validation, metadata extraction, and thumbnail generation.
Abstracts storage backend (local filesystem or S3-compatible).
"""

import os
import uuid
from pathlib import Path

import ffmpeg
import magic

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger("service.media")

# Acceptable MIME types for short-form video
ALLOWED_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/webm",
    "video/x-matroska",
}

# Soft limits for short-form compatibility checks (warn, not block)
MAX_DURATION_SECONDS = 180  # 3 min — TikTok/Reels max is ~90s for most cases
MAX_FILE_SIZE_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB


class MediaValidationError(Exception):
    pass


class MediaService:
    def __init__(self):
        self.backend = settings.storage_backend
        self.local_path = Path(settings.local_storage_path)
        if self.backend == "local":
            (self.local_path / "uploads").mkdir(parents=True, exist_ok=True)
            (self.local_path / "thumbnails").mkdir(parents=True, exist_ok=True)

    # ─── Validation ───────────────────────────────────────────────────────────

    def validate_video(self, file_bytes: bytes, filename: str) -> dict:
        """
        Validate raw file bytes as a supported video.
        Returns metadata dict if valid; raises MediaValidationError if not.
        """
        # MIME type check via magic bytes (not filename extension)
        mime = magic.from_buffer(file_bytes[:2048], mime=True)
        if mime not in ALLOWED_MIME_TYPES:
            raise MediaValidationError(
                f"Unsupported file type: {mime}. "
                f"Allowed types: {', '.join(ALLOWED_MIME_TYPES)}"
            )

        file_size = len(file_bytes)
        if file_size > MAX_FILE_SIZE_BYTES:
            raise MediaValidationError(
                f"File too large: {file_size / (1024**3):.1f} GB. Max: 4 GB."
            )

        return {"mime_type": mime, "file_size_bytes": file_size}

    # ─── Storage ──────────────────────────────────────────────────────────────

    async def store_upload(self, file_bytes: bytes, original_filename: str) -> str:
        """
        Persist file bytes to the configured storage backend.
        Returns a storage key (relative path or S3 key).
        """
        ext = Path(original_filename).suffix.lower() or ".mp4"
        key = f"uploads/{uuid.uuid4()}{ext}"

        if self.backend == "local":
            dest = self.local_path / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file_bytes)
            logger.info("video_stored_local", key=key, size=len(file_bytes))
        else:
            await self._s3_put(key, file_bytes, content_type="video/mp4")
            logger.info("video_stored_s3", key=key, size=len(file_bytes))

        return key

    async def store_thumbnail(self, thumb_bytes: bytes, base_key: str) -> str:
        """Store a generated thumbnail image. Returns storage key."""
        thumb_key = base_key.replace("uploads/", "thumbnails/").rsplit(".", 1)[0] + ".jpg"

        if self.backend == "local":
            dest = self.local_path / thumb_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(thumb_bytes)
        else:
            await self._s3_put(thumb_key, thumb_bytes, content_type="image/jpeg")

        return thumb_key

    def get_local_path(self, storage_key: str) -> Path:
        """Return absolute local path for a storage key (local backend only)."""
        return self.local_path / storage_key

    async def get_public_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """
        Return a publicly accessible URL for the given storage key.
        Local: served via /media/ route.
        S3: presigned URL.
        """
        if self.backend == "local":
            return f"{settings.api_url}/media/{storage_key}"
        return await self._s3_presign(storage_key, expires_in)

    # ─── ffmpeg Metadata + Thumbnail ──────────────────────────────────────────

    def extract_metadata(self, file_path: str) -> dict:
        """
        Use ffprobe (via ffmpeg-python) to extract video duration, dimensions.
        Returns dict with duration_seconds, width, height.
        """
        try:
            probe = ffmpeg.probe(file_path)
            video_stream = next(
                (s for s in probe["streams"] if s["codec_type"] == "video"), None
            )
            if not video_stream:
                return {}

            duration = float(probe["format"].get("duration", 0))
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))

            # Warn on short-form compatibility
            warnings = []
            if duration > 60:
                warnings.append(f"Duration {duration:.1f}s exceeds 60s — may not qualify as a Short/Reel.")
            if width and height and width > height:
                warnings.append("Video is landscape — short-form platforms prefer vertical (9:16).")

            if warnings:
                for w in warnings:
                    logger.warning("video_compatibility_warning", message=w)

            return {
                "duration_seconds": round(duration, 2),
                "width": width,
                "height": height,
            }
        except ffmpeg.Error as e:
            logger.error("ffprobe_failed", error=str(e))
            return {}

    def generate_thumbnail(self, video_path: str, output_path: str, at_second: float = 1.0) -> bool:
        """
        Extract a single frame from the video as a JPEG thumbnail.
        Returns True on success.
        """
        try:
            (
                ffmpeg
                .input(video_path, ss=at_second)
                .filter("scale", 720, -1)
                .output(output_path, vframes=1, format="image2", vcodec="mjpeg")
                .overwrite_output()
                .run(quiet=True)
            )
            return True
        except ffmpeg.Error as e:
            logger.error("thumbnail_generation_failed", error=str(e))
            return False

    # ─── Transcoding stub ─────────────────────────────────────────────────────

    async def transcode(self, storage_key: str, target_format: str = "mp4") -> str:
        """
        TODO: Implement platform-specific transcoding here if needed.
        Structure: read input key, run ffmpeg transcode job (ideally as a Celery task),
        store output to a new key, return the output key.
        """
        raise NotImplementedError(
            "Transcoding pipeline not yet implemented. "
            "Add ffmpeg-python transcode logic here and dispatch as a Celery task."
        )

    # ─── S3 helpers ───────────────────────────────────────────────────────────

    async def _s3_put(self, key: str, data: bytes, content_type: str) -> None:
        import boto3
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            **({"endpoint_url": settings.s3_endpoint_url} if settings.s3_endpoint_url else {}),
        )
        s3.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def _s3_presign(self, key: str, expires_in: int) -> str:
        import boto3
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            **({"endpoint_url": settings.s3_endpoint_url} if settings.s3_endpoint_url else {}),
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
