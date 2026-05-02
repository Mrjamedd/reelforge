"""
Media Service
=============
Handles video upload, validation, metadata extraction, and thumbnail generation.
Abstracts storage backend (local filesystem or S3-compatible).
"""

import os
import shutil
import tempfile
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
NORMALIZED_WIDTH = 1080
NORMALIZED_HEIGHT = 1920
NORMALIZED_VIDEO_BITRATE = "6000k"
NORMALIZED_AUDIO_BITRATE = "128k"
SHORTS_MAX_SECONDS = 180
COMMON_SHORT_FORM_MIN_SECONDS = 3
TIKTOK_MIN_DIMENSION = 360
RECOMMENDED_VERTICAL_RATIO = 9 / 16


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

    def validate_video_file(self, file_path: str) -> dict:
        """
        Validate a video already present on disk without loading it fully into memory.
        Returns metadata dict if valid; raises MediaValidationError if not.
        """
        path = Path(file_path)
        if not path.is_file():
            raise MediaValidationError(f"Video file not found: {file_path}")

        mime = magic.from_file(str(path), mime=True)
        if mime not in ALLOWED_MIME_TYPES:
            raise MediaValidationError(
                f"Unsupported file type: {mime}. "
                f"Allowed types: {', '.join(ALLOWED_MIME_TYPES)}"
            )

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            raise MediaValidationError(
                f"File too large: {file_size / (1024**3):.1f} GB. Max: 4 GB."
            )

        return {"mime_type": mime, "file_size_bytes": file_size}

    # ─── Storage ──────────────────────────────────────────────────────────────

    async def store_upload(self, file_bytes: bytes, original_filename: str) -> str:
        """
        Normalize and persist file bytes to the configured storage backend.
        Returns a storage key (relative path or S3 key).
        """
        suffix = Path(original_filename).suffix.lower() or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            source_path = tmp.name

        try:
            return await self.store_upload_from_path(source_path, original_filename)
        finally:
            Path(source_path).unlink(missing_ok=True)

    async def store_upload_from_path(self, source_path: str, original_filename: str) -> str:
        """
        Normalize and persist a local source file to the configured storage backend.
        Returns a storage key (relative path or S3 key).
        """
        normalized_path = self.normalize_for_short_form(source_path)
        key = f"uploads/{uuid.uuid4()}.mp4"

        try:
            if self.backend == "local":
                dest = self.local_path / key
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(normalized_path, dest)
                logger.info(
                    "video_stored_local_normalized",
                    key=key,
                    source=source_path,
                    size=dest.stat().st_size,
                )
            else:
                with open(normalized_path, "rb") as handle:
                    data = handle.read()
                await self._s3_put(key, data, content_type="video/mp4")
                logger.info(
                    "video_stored_s3_normalized",
                    key=key,
                    source=source_path,
                    size=len(data),
                )
        finally:
            Path(normalized_path).unlink(missing_ok=True)

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

    async def get_instagram_public_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """
        Return a publicly accessible HTTPS URL for Instagram publishing.

        Raises ValueError with a user-facing message if the configured storage
        backend cannot produce a public URL (e.g. local storage).
        """
        from app.services.storage_providers import get_storage_provider

        provider = get_storage_provider()
        if not provider.is_publicly_accessible:
            errors = provider.configuration_errors
            raise ValueError(
                "Instagram requires a publicly accessible HTTPS video URL. "
                + " ".join(errors)
            )
        return await provider.get_public_url(storage_key, expires_in)

    def has_public_url_provider(self) -> bool:
        """True when the current storage backend can produce a publicly accessible URL."""
        from app.services.storage_providers import get_storage_provider

        return get_storage_provider().is_publicly_accessible

    # ─── ffmpeg Metadata + Thumbnail ──────────────────────────────────────────

    def extract_metadata(self, file_path: str) -> dict:
        """
        Use ffprobe (via ffmpeg-python) to extract video duration, dimensions.
        Returns dict with duration_seconds, width, height.
        """
        metadata = self.extract_source_metadata(file_path)
        return {
            "duration_seconds": metadata.get("duration_seconds"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
        }

    def extract_source_metadata(self, file_path: str) -> dict:
        """Return source video metadata used for user-facing compatibility checks."""
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
            frame_rate = self._parse_frame_rate(video_stream.get("avg_frame_rate"))
            audio_stream = next(
                (s for s in probe["streams"] if s["codec_type"] == "audio"), None
            )

            return {
                "duration_seconds": round(duration, 2),
                "width": width,
                "height": height,
                "video_codec": video_stream.get("codec_name"),
                "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
                "frame_rate": frame_rate,
            }
        except ffmpeg.Error as e:
            logger.error("ffprobe_failed", error=str(e))
            return {}

    @staticmethod
    def _parse_frame_rate(raw_rate: str | None) -> float | None:
        if not raw_rate or raw_rate == "0/0":
            return None
        try:
            if "/" in raw_rate:
                numerator, denominator = raw_rate.split("/", 1)
                denominator_value = float(denominator)
                if denominator_value == 0:
                    return None
                return round(float(numerator) / denominator_value, 2)
            return round(float(raw_rate), 2)
        except (TypeError, ValueError):
            return None

    def short_form_compatibility_warnings(
        self,
        source_metadata: dict,
        *,
        file_size_bytes: int,
    ) -> list[str]:
        """Return actionable warnings before ReelPush normalizes the source file."""
        warnings: list[str] = []
        duration = source_metadata.get("duration_seconds")
        width = source_metadata.get("width")
        height = source_metadata.get("height")
        frame_rate = source_metadata.get("frame_rate")
        video_codec = str(source_metadata.get("video_codec") or "").lower()
        audio_codec = str(source_metadata.get("audio_codec") or "").lower()

        if isinstance(duration, (int, float)):
            if duration < COMMON_SHORT_FORM_MIN_SECONDS:
                warnings.append(
                    "Video is under 3 seconds. Some short-form publishing APIs reject very short clips."
                )
            elif duration > SHORTS_MAX_SECONDS:
                warnings.append(
                    "Video is over 3 minutes. YouTube Shorts and some TikTok accounts may require trimming."
                )
            elif duration > 60:
                warnings.append(
                    "Video is over 60 seconds. YouTube Shorts can accept up to 3 minutes, but copyright claims can block longer Shorts."
                )

        if isinstance(width, int) and isinstance(height, int) and width and height:
            ratio = width / height
            if width < TIKTOK_MIN_DIMENSION or height < TIKTOK_MIN_DIMENSION:
                warnings.append(
                    "Video is below 360px on at least one side. TikTok's Content Posting API requires at least 360px."
                )
            if width > height:
                warnings.append(
                    "Video is landscape. ReelPush will letterbox it into 9:16, which can look poor on Shorts, Reels, and TikTok."
                )
            elif abs(ratio - RECOMMENDED_VERTICAL_RATIO) > 0.04:
                warnings.append(
                    "Video is not close to 9:16. ReelPush will pad it to 1080x1920 before publishing."
                )

        if isinstance(frame_rate, (int, float)) and (frame_rate < 23 or frame_rate > 60):
            warnings.append("Video frame rate is outside the common 23-60 FPS API range.")

        if video_codec and video_codec not in {"h264", "hevc", "h265", "vp8", "vp9"}:
            warnings.append(
                f"Source video codec is {video_codec}. ReelPush will transcode it to H.264 for compatibility."
            )
        if audio_codec and audio_codec not in {"aac", "mp3", "opus", "vorbis"}:
            warnings.append(
                f"Source audio codec is {audio_codec}. ReelPush will transcode audio to AAC for compatibility."
            )

        if file_size_bytes > MAX_FILE_SIZE_BYTES:
            warnings.append("Video exceeds ReelPush's 4 GB upload limit.")

        for warning in warnings:
            logger.warning("video_compatibility_warning", message=warning)
        return warnings

    def generate_thumbnail(self, video_path: str, output_path: str, at_second: float = 0.1) -> bool:
        """
        Extract a single frame from the video as a JPEG thumbnail.
        Returns True on success.
        """
        last_error: Exception | None = None
        for seek_position in (at_second, 0):
            try:
                (
                    ffmpeg
                    .input(video_path, ss=seek_position)
                    .filter("scale", 720, -1)
                    .output(output_path, vframes=1, format="image2", vcodec="mjpeg")
                    .overwrite_output()
                    .run(quiet=True)
                )
                return True
            except ffmpeg.Error as e:
                last_error = e

        logger.error("thumbnail_generation_failed", error=str(last_error))
        return False

    # ─── Platform normalization ───────────────────────────────────────────────

    def normalize_for_short_form(self, source_path: str) -> str:
        """
        Convert any accepted input video into a conservative MP4 preset accepted
        by YouTube Shorts, Instagram Reels, and TikTok.
        """
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            output_path = tmp.name

        try:
            (
                ffmpeg
                .input(source_path)
                .filter(
                    "scale",
                    NORMALIZED_WIDTH,
                    NORMALIZED_HEIGHT,
                    force_original_aspect_ratio="decrease",
                )
                .filter(
                    "pad",
                    NORMALIZED_WIDTH,
                    NORMALIZED_HEIGHT,
                    "(ow-iw)/2",
                    "(oh-ih)/2",
                    color="black",
                )
                .output(
                    output_path,
                    format="mp4",
                    vcodec="libx264",
                    acodec="aac",
                    pix_fmt="yuv420p",
                    r=30,
                    movflags="+faststart",
                    preset="medium",
                    **{
                        "b:v": NORMALIZED_VIDEO_BITRATE,
                        "b:a": NORMALIZED_AUDIO_BITRATE,
                    },
                )
                .overwrite_output()
                .run(quiet=True)
            )
            return output_path
        except ffmpeg.Error as e:
            Path(output_path).unlink(missing_ok=True)
            stderr = e.stderr.decode("utf-8", errors="ignore") if e.stderr else str(e)
            logger.error("short_form_normalization_failed", error=stderr)
            raise MediaValidationError("Video could not be converted to the ReelPush MP4 preset.") from e

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
