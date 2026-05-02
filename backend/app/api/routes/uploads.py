import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import AdminUser, Upload
from app.schemas.schemas import UploadOut
from app.services.media_service import MediaService, MediaValidationError

router = APIRouter(prefix="/uploads", tags=["uploads"])
media_svc = MediaService()


@router.post("/", response_model=UploadOut, status_code=201)
async def upload_video(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    """
    Upload a video file.
    - Validates MIME type and file size
    - Stores original file
    - Extracts duration/dimensions via ffprobe
    - Generates a thumbnail
    Returns the created Upload record.
    """
    file_bytes = await file.read()

    # Validate
    try:
        validation = media_svc.validate_video(file_bytes, file.filename or "upload")
    except MediaValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    video_meta = {}
    source_meta = {}
    validation_warnings = []
    thumbnail_key = None
    cleanup_metadata_path = None

    try:
        source_meta = media_svc.extract_source_metadata(tmp_path)
        validation_warnings = media_svc.short_form_compatibility_warnings(
            source_meta,
            file_size_bytes=validation["file_size_bytes"],
        )
        storage_key = await media_svc.store_upload_from_path(
            tmp_path,
            file.filename or "upload.mp4",
        )
        stored_video_path = media_svc.get_local_path(storage_key)
        metadata_video_path = str(stored_video_path)
        if media_svc.backend != "local":
            cleanup_metadata_path = media_svc.normalize_for_short_form(tmp_path)
            metadata_video_path = cleanup_metadata_path

        normalized_validation = media_svc.validate_video_file(metadata_video_path)
        validation.update(normalized_validation)
        video_meta = media_svc.extract_metadata(metadata_video_path)

        # Generate thumbnail
        thumb_tmp = metadata_video_path + "_thumb.jpg"
        if media_svc.generate_thumbnail(metadata_video_path, thumb_tmp):
            thumb_bytes = open(thumb_tmp, "rb").read()
            thumbnail_key = await media_svc.store_thumbnail(thumb_bytes, storage_key)
            os.unlink(thumb_tmp)
    except MediaValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        os.unlink(tmp_path)
        if cleanup_metadata_path:
            os.unlink(cleanup_metadata_path)

    # Persist Upload record
    upload = Upload(
        original_filename=file.filename or "upload.mp4",
        storage_key=storage_key,
        thumbnail_key=thumbnail_key,
        mime_type=validation["mime_type"],
        file_size_bytes=validation["file_size_bytes"],
        duration_seconds=video_meta.get("duration_seconds"),
        width=video_meta.get("width"),
        height=video_meta.get("height"),
        source_metadata=source_meta,
        validation_warnings=validation_warnings,
        uploaded_by_id=current_user.id,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    return upload


@router.get("/", response_model=list[UploadOut])
async def list_uploads(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    result = await db.execute(
        select(Upload)
        .where(Upload.uploaded_by_id == current_user.id)
        .order_by(Upload.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get("/{upload_id}", response_model=UploadOut)
async def get_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_user),
):
    import uuid
    upload = await db.get(Upload, uuid.UUID(upload_id))
    if not upload or upload.uploaded_by_id != current_user.id:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return upload
