"""POST /api/dub, POST /api/subtitle/remove, POST /api/logo/remove."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_owned
from app.core.security import safe_join
from app.models.db import get_session, Video, Job, JobStatus, SubtitleSegment, async_session
from app.services import ffmpeg_service, tts_providers

router = APIRouter(prefix="/api", tags=["processing"])


# ---------- AI DUBBING ----------

class DubRequest(BaseModel):
    video_id: str
    subtitle_job_id: str  # job chứa segments đã dịch (translated_text)
    voice_provider: str = "edge_tts"
    voice_id: str
    original_volume: float = 0.15
    dubbed_volume: float = 1.0
    voice_reference_confirmed: bool = False  # user xác nhận có quyền dùng giọng (nếu cloning)


@router.post("/dub")
async def create_dub(req: DubRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video không tồn tại")

    if not await get_owned(session, Job, req.subtitle_job_id):
        raise HTTPException(404, "Phụ đề nguồn không tồn tại")
    vp = tts_providers.get_voice_provider(req.voice_provider)
    if getattr(vp, "supports_cloning", False) and not req.voice_reference_confirmed:
        raise HTTPException(
            400,
            "Provider này hỗ trợ voice cloning. Bạn phải xác nhận (voice_reference_confirmed=true) "
            "rằng bạn có quyền sử dụng giọng nói được cung cấp.",
        )

    job = Job(video_id=video.id, user_id=video.user_id, job_type="dub", status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()

    background_tasks.add_task(
        _run_dub, job.id, video.file_path, req.subtitle_job_id,
        req.voice_provider, req.voice_id, req.original_volume, req.dubbed_volume,
    )
    return {"job_id": job.id, "status": job.status}


async def _run_dub(job_id, video_path, subtitle_job_id, voice_provider, voice_id, orig_vol, dub_vol):
    async with async_session() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.GENERATING_AUDIO
        await session.commit()
        try:
            result = await session.execute(
                select(SubtitleSegment).where(SubtitleSegment.job_id == subtitle_job_id).order_by(SubtitleSegment.index)
            )
            segments = result.scalars().all()
            if not segments:
                raise ValueError("Không có subtitle đã dịch để dubbing")

            vp = tts_providers.get_voice_provider(voice_provider)
            job_dir = safe_join(settings.JOBS_DIR, job_id)

            # Sinh audio cho từng segment rồi ghép lại theo timeline (đơn giản hoá:
            # nối tuần tự + căn theo thời điểm bắt đầu segment đầu tiên).
            # Với hệ thống production thật, nên dùng pydub/AudioSegment để chèn đúng
            # vị trí start_ms của từng segment thay vì nối liên tục.
            segment_files = []
            for seg in segments:
                text = seg.translated_text or seg.text
                out_file = job_dir / f"seg_{seg.index}.mp3"
                await vp.synthesize(text, voice_id, out_file)
                segment_files.append(str(out_file))

            concat_list = job_dir / "concat.txt"
            concat_list.write_text("\n".join(f"file '{f}'" for f in segment_files), encoding="utf-8")

            merged_audio = job_dir / "dubbed_audio.mp3"
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", str(merged_audio),
            )
            await proc.communicate()

            out_path = safe_join(settings.OUTPUT_DIR, job_id, "dubbed.mp4")
            job.status = JobStatus.RENDERING
            await session.commit()
            await ffmpeg_service.mix_audio(Path(video_path), merged_audio, out_path, orig_vol, dub_vol)

            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.result_path = str(out_path)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        await session.commit()


# ---------- REMOVE SUBTITLE / LOGO (region-based, blur/mosaic fallback) ----------

class RemoveRegionRequest(BaseModel):
    video_id: str
    x: int
    y: int
    width: int
    height: int
    method: str = "blur"  # blur | mosaic | crop (inpainting nếu có model, không cam kết xóa hoàn hảo)


@router.post("/subtitle/remove")
async def remove_subtitle(req: RemoveRegionRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    return await _create_region_job(req, "remove_subtitle", background_tasks, session)


@router.post("/logo/remove")
async def remove_logo(req: RemoveRegionRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    return await _create_region_job(req, "remove_logo", background_tasks, session)


async def _create_region_job(req: RemoveRegionRequest, job_type: str, background_tasks: BackgroundTasks, session: AsyncSession):
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video không tồn tại")

    job = Job(video_id=video.id, user_id=video.user_id, job_type=job_type, status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()

    background_tasks.add_task(_run_region_removal, job.id, video.file_path, req.x, req.y, req.width, req.height, req.method)
    return {
        "job_id": job.id, "status": job.status,
        "note": "Kết quả dùng blur/mosaic fallback. Không cam kết xóa hoàn hảo 100% mọi video.",
    }


async def _run_region_removal(job_id, video_path, x, y, w, h, method):
    async with async_session() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.PROCESSING
        await session.commit()
        try:
            out_path = safe_join(settings.OUTPUT_DIR, job_id, "output.mp4")
            # method inpainting thật (vd ProPainter/E2FGVI) có thể được cắm thêm ở đây
            # như một implementation khác của cùng interface. Hiện tại dùng blur fallback
            # vì đây là phương án chạy được ngay không cần model nặng / GPU.
            await ffmpeg_service.blur_region(Path(video_path), out_path, x, y, w, h)

            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.result_path = str(out_path)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
        await session.commit()
