"""Endpoint import video từ LINK (mọi trang yt-dlp hỗ trợ + link file trực tiếp): POST /api/import-url, GET /api/import-url/{jobId}."""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import current_user_id, get_owned
from app.core.security import safe_join
from app.models.db import get_session, Project, Video, Job, JobStatus, new_id, async_session
from app.services import url_import_service as uis
from app.services import ffmpeg_service

router = APIRouter(prefix="/api", tags=["import-url"])
MAX_ACTIVE_IMPORTS_PER_USER = 2   # số tác vụ tải link chạy cùng lúc của mỗi người (tránh lạm dụng băng thông/ổ đĩa)
_MEM_PROGRESS: dict[str, int] = {}   # tiến độ tải (0-99) do thread yt-dlp cập nhật


class ImportURLRequest(BaseModel):
    url: str
    format_id: str | None = None


@router.post("/import-url")
async def import_url(req: ImportURLRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    active = (await session.execute(select(Job.id).where(
        Job.user_id == current_user_id.get(), Job.job_type == "import_url",
        Job.status.in_([JobStatus.QUEUED, JobStatus.DOWNLOADING])))).scalars().all()
    if len(active) >= MAX_ACTIVE_IMPORTS_PER_USER:
        raise HTTPException(429, f"Bạn đang tải {len(active)} link cùng lúc. Hãy đợi xong rồi thêm link mới.")

    # Bước 1: kiểm tra chính sách (SSRF, loại link) + lấy thông tin, chưa tải
    try:
        meta = await uis.fetch_metadata(req.url)
    except uis.UnsupportedURLError as e:
        raise HTTPException(400, str(e))
    except uis.RestrictedContentError as e:
        raise HTTPException(403, str(e))
    except uis.URLImportError as e:
        raise HTTPException(502, str(e))

    project = Project(name=meta.title)
    session.add(project)
    await session.flush()

    video = Video(project_id=project.id, user_id=current_user_id.get(), source_type="url", source_url=req.url, duration_sec=meta.duration_sec)
    session.add(video)
    await session.flush()

    job = Job(video_id=video.id, user_id=video.user_id, job_type="import_url", status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()

    job_id = job.id
    video_id = video.id

    background_tasks.add_task(_run_download, job_id, video_id, req.url, req.format_id, meta.kind)

    return {
        "job_id": job_id,
        "video_id": video_id,
        "project_id": project.id,
        "metadata": {
            "title": meta.title,
            "duration_sec": meta.duration_sec,
            "thumbnail": meta.thumbnail,
            "provider": meta.provider,
            "kind": meta.kind,
            "formats": meta.available_formats[:20],
        },
    }


async def _run_download(job_id: str, video_id: str, url: str, format_id: str | None, kind: str | None = None):
    async with async_session() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.DOWNLOADING
        await session.commit()

        try:
            job_dir = safe_join(settings.JOBS_DIR, job_id)
            path = await uis.download_video(
                url, job_dir, format_id,
                on_progress=lambda f: _MEM_PROGRESS.__setitem__(job_id, int(f * 100)), kind=kind)
            meta = await ffmpeg_service.probe(path)

            video = await session.get(Video, video_id)
            video.file_path = str(path)
            video.duration_sec = meta.duration_sec
            video.width = meta.width
            video.height = meta.height

            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.result_path = str(path)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        _MEM_PROGRESS.pop(job_id, None)
        await session.commit()


@router.get("/import-url/{job_id}")
async def get_import_status(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await get_owned(session, Job, job_id)
    if not job:
        raise HTTPException(404, "Job không tồn tại")
    return {
        "job_id": job.id,
        "status": job.status,
        "progress": 100 if job.status == JobStatus.COMPLETED else max(job.progress or 0, _MEM_PROGRESS.get(job.id, 0)),
        "error_message": job.error_message,
        "video_id": job.video_id,
    }
