"""
POST /api/subtitle/detect-region  - tự động phát hiện vùng chứa phụ đề cứng
POST /api/subtitle/extract-ocr    - trích phụ đề cứng bằng OCR (có thể tự phát hiện vùng)
"""
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_owned
from app.core.security import safe_join
from app.models.db import get_session, Video, Job, JobStatus, SubtitleSegment, async_session, new_id
from app.services import ocr_service, region_detection

router = APIRouter(prefix="/api", tags=["ocr"])


class DetectRegionRequest(BaseModel):
    video_id: str
    language: str = "vi"
    engine: str = "tesseract"  # tesseract | paddleocr
    samples: int = Field(default=24, ge=6, le=60)


@router.post("/subtitle/detect-region")
async def detect_region(req: DetectRegionRequest, session: AsyncSession = Depends(get_session)):
    """Phân tích ~24 khung hình mẫu để đoán vùng phụ đề. Có thể mất 20-60 giây trên CPU."""
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video không tồn tại")

    work_dir = safe_join(settings.JOBS_DIR, f"detect_{new_id()}")
    try:
        region = await region_detection.auto_detect_region(
            Path(video.file_path), work_dir, req.language, req.engine, req.samples,
            video.width, video.height,
        )
    except ImportError as e:
        raise HTTPException(501, f"Thiếu thư viện OCR: {e}. Xem INSTALL.md mục Tesseract.")
    except Exception as e:
        raise HTTPException(500, f"Phát hiện vùng thất bại: {e}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "x": region.x, "y": region.y, "width": region.width, "height": region.height,
        "frame_width": region.frame_width, "frame_height": region.frame_height,
        "confidence": region.confidence,
        "fallback": region.fallback,
        "reason": region.reason,
        "frames_analyzed": region.frames_analyzed,
        "frames_with_text": region.frames_with_text,
        "other_candidates": region.candidates,
        "note": (
            "Đây là vùng ĐOÁN mặc định ở đáy khung hình vì không tìm thấy phụ đề rõ ràng. Hãy kiểm tra và chỉnh tay."
            if region.fallback else
            "Vùng được ước lượng tự động, không đảm bảo đúng 100%. Hãy xem lại trước khi trích xuất."
        ),
    }


class ExtractOCRRequest(BaseModel):
    video_id: str
    # Toạ độ tuỳ chọn: nếu bỏ trống (hoặc auto_detect=True) hệ thống tự phát hiện vùng
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    auto_detect: bool = False
    language: str = "vi"
    fps: float = 2.0
    engine: str = "tesseract"  # tesseract | paddleocr


@router.post("/subtitle/extract-ocr")
async def extract_ocr(req: ExtractOCRRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video không tồn tại")

    has_manual_region = None not in (req.x, req.y, req.width, req.height)
    use_auto = req.auto_detect or not has_manual_region

    job = Job(video_id=video.id, user_id=video.user_id, job_type="extract_ocr", status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()

    background_tasks.add_task(
        _run_extract_ocr, job.id, video.file_path, video.width, video.height,
        (req.x, req.y, req.width, req.height), use_auto, req.language, req.fps, req.engine,
    )
    return {
        "job_id": job.id, "status": job.status, "auto_detect": use_auto,
        "note": "Độ chính xác phụ thuộc chất lượng video/font. Nên xem lại kết quả trước khi dùng.",
    }


async def _run_extract_ocr(job_id, video_path, vw, vh, manual, use_auto, lang, fps, engine):
    async with async_session() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.PROCESSING
        await session.commit()
        try:
            work_dir = safe_join(settings.JOBS_DIR, job_id)

            if use_auto:
                detected = await region_detection.auto_detect_region(
                    Path(video_path), work_dir, lang, engine, 24, vw, vh,
                )
                region = ocr_service.Region(detected.x, detected.y, detected.width, detected.height)
                job.params_json = (
                    f'{{"auto_detect": true, "confidence": {detected.confidence}, '
                    f'"fallback": {"true" if detected.fallback else "false"}}}'
                )
                await session.commit()
            else:
                x, y, w, h = manual
                region = ocr_service.Region(x, y, w, h)

            segments = await ocr_service.extract_subtitle_from_video(
                Path(video_path), work_dir, region, lang, fps, engine,
            )
            if not segments:
                raise ValueError("Không phát hiện được text nào trong vùng đã chọn.")

            for seg in segments:
                session.add(SubtitleSegment(
                    job_id=job_id, index=seg.index,
                    start_ms=seg.start_ms, end_ms=seg.end_ms, text=seg.text,
                ))
            job.status = JobStatus.COMPLETED
            job.progress = 100
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        await session.commit()
