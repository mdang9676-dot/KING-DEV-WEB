"""Endpoint POST /api/upload - upload video/audio với validate MIME, extension, size."""
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import current_user_id
from app.core.security import sanitize_filename, validate_extension, safe_join
from app.models.db import get_session, Project, Video, new_id
from app.services import ffmpeg_service

router = APIRouter(prefix="/api", tags=["upload"])

CHUNK_SIZE = 1024 * 1024  # 1MB


@router.post("/upload")
async def upload_video(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    filename = sanitize_filename(file.filename or "upload.mp4")

    allowed = settings.ALLOWED_VIDEO_EXT | settings.ALLOWED_AUDIO_EXT
    if not validate_extension(filename, allowed):
        raise HTTPException(400, f"Định dạng file không được hỗ trợ. Cho phép: {sorted(allowed)}")

    # Tạo project + video record, job_id ngẫu nhiên tránh đoán được đường dẫn
    project = Project(name=filename)
    session.add(project)
    await session.flush()

    video_id = new_id()
    dest_dir = safe_join(settings.UPLOAD_DIR, video_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    size = 0
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    with open(dest_path, "wb") as f:
        while chunk := await file.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                f.close()
                dest_path.unlink(missing_ok=True)
                raise HTTPException(413, f"File vượt quá giới hạn {settings.MAX_UPLOAD_MB}MB")
            f.write(chunk)

    # Lấy metadata thật bằng ffprobe (không tin metadata client gửi lên)
    try:
        meta = await ffmpeg_service.probe(dest_path)
        duration, width, height = meta.duration_sec, meta.width, meta.height
    except Exception:
        duration = width = height = None

    video = Video(
        id=video_id,
        user_id=current_user_id.get(),
        project_id=project.id,
        source_type="upload",
        original_filename=filename,
        file_path=str(dest_path),
        duration_sec=duration,
        width=width,
        height=height,
    )
    session.add(video)
    await session.commit()

    return {
        "video_id": video.id,
        "project_id": project.id,
        "filename": filename,
        "duration_sec": duration,
        "width": width,
        "height": height,
    }
