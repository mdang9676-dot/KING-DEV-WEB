"""
Endpoints xử lý chính:
POST /api/transcribe
POST /api/translate
POST /api/subtitle/burn
POST /api/subtitle/remove
POST /api/logo/remove
POST /api/dub
GET  /api/jobs/{id}
POST /api/jobs/{id}/cancel
GET  /api/jobs/{id}/download
GET  /api/voices
GET  /api/languages
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
import json
from app.core.deps import get_owned
from app.core.security import safe_join
from app.models.db import get_session, Video, Job, JobStatus, SubtitleSegment, new_id, async_session
from app.services import whisper_service, subtitle_service, ffmpeg_service, translation_providers, tts_providers

router = APIRouter(prefix="/api", tags=["jobs"])

LANGUAGES = [
    {"code": "vi", "name": "Vietnamese"}, {"code": "en", "name": "English"},
    {"code": "zh", "name": "Chinese"}, {"code": "ja", "name": "Japanese"},
    {"code": "ko", "name": "Korean"}, {"code": "fr", "name": "French"},
    {"code": "de", "name": "German"}, {"code": "es", "name": "Spanish"},
    {"code": "pt", "name": "Portuguese"}, {"code": "ru", "name": "Russian"},
    {"code": "th", "name": "Thai"}, {"code": "id", "name": "Indonesian"},
    {"code": "ar", "name": "Arabic"}, {"code": "hi", "name": "Hindi"},
]


@router.get("/languages")
async def get_languages():
    return LANGUAGES


@router.get("/voices")
async def get_voices(provider: str = "edge_tts", language: str | None = None):
    vp = tts_providers.get_voice_provider(provider)
    try:
        return await vp.list_voices(language)
    except Exception as e:
        raise HTTPException(502, f"Không lấy được danh sách giọng: {e}")


# ---------- TRANSCRIBE ----------

class TranscribeRequest(BaseModel):
    video_id: str
    language: str | None = None  # None = auto-detect
    model_size: str | None = None


@router.post("/transcribe")
async def transcribe(req: TranscribeRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video không tồn tại hoặc chưa có file")

    job = Job(video_id=video.id, user_id=video.user_id, job_type="transcribe", status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()

    background_tasks.add_task(_run_transcribe, job.id, video.file_path, req.language)
    return {"job_id": job.id, "status": job.status}


async def _run_transcribe(job_id: str, video_path: str, language: str | None):
    async with async_session() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.TRANSCRIBING
        await session.commit()
        try:
            wav_path = safe_join(settings.JOBS_DIR, job_id, "audio.wav")
            await ffmpeg_service.extract_audio(Path(video_path), wav_path)

            import asyncio
            segments, detected_lang = await asyncio.to_thread(
                whisper_service.transcribe_audio, str(wav_path), language)

            for seg in segments:
                session.add(SubtitleSegment(
                    job_id=job_id, index=seg.index,
                    start_ms=seg.start_ms, end_ms=seg.end_ms, text=seg.text,
                ))
            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.params_json = f'{{"detected_language": "{detected_lang}"}}'
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        await session.commit()


# ---------- TRANSLATE ----------

class TranslateRequest(BaseModel):
    job_id: str  # job transcribe đã hoàn thành, chứa segments cần dịch
    target_lang: str
    provider: str | None = None  # openai | gemini | deepseek | argos_local | None(auto)


@router.post("/translate")
async def translate(req: TranslateRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    job = await get_owned(session, Job, req.job_id)
    if not job:
        raise HTTPException(404, "Job gốc không tồn tại")

    new_job = Job(video_id=job.video_id, user_id=job.user_id, job_type="translate", status=JobStatus.QUEUED)
    session.add(new_job)
    await session.commit()

    background_tasks.add_task(_run_translate, new_job.id, req.job_id, req.target_lang, req.provider)
    return {"job_id": new_job.id, "status": new_job.status}


async def _run_translate(new_job_id: str, source_job_id: str, target_lang: str, provider_name: str | None):
    from sqlalchemy import select
    async with async_session() as session:
        job = await session.get(Job, new_job_id)
        job.status = JobStatus.TRANSLATING
        await session.commit()
        try:
            result = await session.execute(
                select(SubtitleSegment).where(SubtitleSegment.job_id == source_job_id).order_by(SubtitleSegment.index)
            )
            segments = result.scalars().all()
            provider = translation_providers.get_translation_provider(provider_name)

            for seg in segments:
                translated = await provider.translate(seg.text, target_lang)
                new_seg = SubtitleSegment(
                    job_id=new_job_id, index=seg.index,
                    start_ms=seg.start_ms, end_ms=seg.end_ms,
                    text=seg.text, translated_text=translated,
                )
                session.add(new_seg)

            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.params_json = f'{{"provider": "{provider.name}", "target_lang": "{target_lang}"}}'
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        await session.commit()


# ---------- SUBTITLE EXPORT / BURN ----------

class ExportRequest(BaseModel):
    job_id: str
    format: str = "srt"  # srt | vtt | ass | txt
    bilingual: bool = False


@router.post("/subtitle/export")
async def export_subtitle(req: ExportRequest, session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select
    if not await get_owned(session, Job, req.job_id):
        raise HTTPException(404, "Không tìm thấy subtitle cho job này")
    result = await session.execute(
        select(SubtitleSegment).where(SubtitleSegment.job_id == req.job_id).order_by(SubtitleSegment.index)
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(404, "Không tìm thấy subtitle cho job này")

    segs = [subtitle_service.Segment(r.index, r.start_ms, r.end_ms, r.text, r.translated_text) for r in rows]

    generators = {
        "srt": subtitle_service.to_srt, "vtt": subtitle_service.to_vtt,
        "ass": subtitle_service.to_ass, "txt": subtitle_service.to_txt,
    }
    gen = generators.get(req.format)
    if not gen:
        raise HTTPException(400, "Format không hỗ trợ")

    content = gen(segs, req.bilingual) if req.format in ("srt", "vtt") else gen(segs)
    out_path = safe_join(settings.OUTPUT_DIR, req.job_id, f"subtitle.{req.format}")
    subtitle_service.save_file(out_path, content)
    return FileResponse(out_path, filename=f"subtitle.{req.format}")


class BurnRequest(BaseModel):
    video_id: str
    subtitle_job_id: str
    preset: str = "youtube"
    bilingual: bool = False


@router.post("/subtitle/burn")
async def burn_subtitle(req: BurnRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video không tồn tại")

    if not await get_owned(session, Job, req.subtitle_job_id):
        raise HTTPException(404, "Phụ đề nguồn không tồn tại")
    job = Job(video_id=video.id, user_id=video.user_id, job_type="burn", status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()

    background_tasks.add_task(_run_burn, job.id, video.file_path, req.subtitle_job_id, req.preset, req.bilingual)
    return {"job_id": job.id, "status": job.status}


async def _run_burn(job_id: str, video_path: str, subtitle_job_id: str, preset: str, bilingual: bool):
    from sqlalchemy import select
    async with async_session() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.RENDERING
        await session.commit()
        try:
            result = await session.execute(
                select(SubtitleSegment).where(SubtitleSegment.job_id == subtitle_job_id).order_by(SubtitleSegment.index)
            )
            rows = result.scalars().all()
            segs = [subtitle_service.Segment(r.index, r.start_ms, r.end_ms, r.text, r.translated_text) for r in rows]
            srt_content = subtitle_service.to_srt(segs, bilingual)

            srt_path = safe_join(settings.JOBS_DIR, job_id, "subtitle.srt")
            subtitle_service.save_file(srt_path, srt_content)

            out_path = safe_join(settings.OUTPUT_DIR, job_id, "output.mp4")
            await ffmpeg_service.burn_subtitle(Path(video_path), srt_path, out_path, preset)

            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.result_path = str(out_path)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        await session.commit()


# ---------- STATUS / DOWNLOAD / CANCEL ----------

@router.get("/jobs/{job_id}")
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await get_owned(session, Job, job_id)
    if not job:
        raise HTTPException(404, "Job không tồn tại")
    warnings: list = []
    if job.params_json:
        try:
            warnings = json.loads(job.params_json).get("warnings", []) or []
        except (ValueError, AttributeError):
            warnings = []
    has_result = bool(job.result_path and Path(job.result_path).exists())
    has_srt = safe_join(settings.OUTPUT_DIR, job.id, "subtitle.srt").exists()
    return {
        "id": job.id, "job_type": job.job_type, "status": job.status,
        "progress": job.progress, "stage": job.stage, "error_message": job.error_message,
        "result_path": has_result, "has_result": has_result, "has_srt": has_srt, "warnings": warnings,
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await get_owned(session, Job, job_id)
    if not job:
        raise HTTPException(404, "Job không tồn tại")
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(400, f"Không thể huỷ job đã ở trạng thái {job.status}")
    job.status = JobStatus.CANCELLED
    job.stage = "Đang huỷ…"
    await session.commit()
    return {"id": job.id, "status": job.status}


@router.get("/jobs/{job_id}/download")
async def download_result(job_id: str, kind: str = "video", session: AsyncSession = Depends(get_session)):
    job = await get_owned(session, Job, job_id)
    if not job:
        raise HTTPException(404, "Job không tồn tại")
    video = await session.get(Video, job.video_id)
    stem = Path((video.original_filename if video else None) or "video").stem[:60] or "video"

    if kind == "srt":
        srt = safe_join(settings.OUTPUT_DIR, job.id, "subtitle.srt")
        if not srt.exists():
            raise HTTPException(404, "Job này không có file phụ đề")
        return FileResponse(srt, filename=f"{stem}.srt", media_type="application/x-subrip")

    if not job.result_path:
        raise HTTPException(404, "Chưa có kết quả để tải")
    path = Path(job.result_path)
    if not path.exists():
        raise HTTPException(410, "File kết quả đã bị dọn dẹp (retention policy)")
    name = f"{stem}_edited.mp4" if job.job_type == "pipeline" else path.name
    return FileResponse(path, filename=name)
