"""
Endpoint cho Studio "một chạm":
  GET  /api/capabilities            công cụ nào khả dụng trên server (để UI bật/tắt lựa chọn)
  POST /api/assets/logo             tải logo của người dùng (PNG/JPG/WEBP)
  GET  /api/assets/logo/{id}        lấy lại logo đã tải (để hiện xem trước)
  GET  /api/videos/{id}             thông tin video
  GET  /api/videos/{id}/frame?t=    khung hình xem trước (để vẽ vùng phụ đề/logo)
  POST /api/pipeline                bắt đầu xử lý NGẦM
  GET  /api/jobs                    lịch sử/đang chạy của người dùng
  GET  /api/jobs/{id}/stream        phát video kết quả (hỗ trợ Range để tua/Safari)
"""
import importlib.util
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_owned, require_user
from app.core.security import safe_join
from app.models.db import Job, JobStatus, User, Video, get_session, new_id
from app.models.pipeline import PipelineRequest
from app.services import ai_providers, ffmpeg_service, pipeline_runner, translation_providers, voice_clones

router = APIRouter(prefix="/api", tags=["studio"])

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
MAX_LOGOS_PER_USER = 20
MAX_ACTIVE_JOBS_PER_USER = 2
_ACTIVE = (JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.TRANSCRIBING, JobStatus.TRANSLATING,
           JobStatus.GENERATING_AUDIO, JobStatus.RENDERING, JobStatus.DOWNLOADING)


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


@router.get("/fonts")
async def fonts():
    """Kiểu chữ phụ đề: font tự thêm (data/fonts) + font hệ thống hỗ trợ tiếng Việt."""
    from app.services import fonts as fontsvc
    return {"fonts": fontsvc.list_fonts(settings.FONTS_DIR), "default": "Arial"}


@router.get("/capabilities")
async def capabilities():
    """Chỉ trả về true/false — không lộ cấu hình hay bí mật."""
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "tesseract": bool(shutil.which("tesseract")) and _has("pytesseract"),
        "paddleocr": _has("paddleocr"),
        "rapidocr": _has("rapidocr") or _has("rapidocr_onnxruntime"),
        "whisper": _has("faster_whisper"),
        "edge_tts": _has("edge_tts"),
        "vieneu": _has("vieneu"),
        "argos": _has("argostranslate"),
        "yt_dlp": _has("yt_dlp"),
        "translation_ready": translation_providers.get_translation_provider(None).name != "demo",
        "openai_tts_ready": bool(settings.OPENAI_API_KEY),
    }


# ---------------------------------------------------------------------------
# Logo người dùng
# ---------------------------------------------------------------------------

def _sniff_image(head: bytes) -> str | None:
    """Nhận diện loại ảnh bằng chữ ký file (không tin đuôi file/Content-Type do client gửi)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _user_logo_dir(user_id: str) -> Path:
    d = safe_join(settings.ASSETS_DIR, user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/assets/logo")
async def upload_logo(file: UploadFile = File(...), user: User = Depends(require_user)):
    d = _user_logo_dir(user.id)
    if len(list(d.glob("*"))) >= MAX_LOGOS_PER_USER:
        raise HTTPException(400, f"Đã đạt giới hạn {MAX_LOGOS_PER_USER} logo. Hãy dùng lại logo cũ.")

    max_bytes = settings.MAX_LOGO_MB * 1024 * 1024
    data = bytearray()
    while chunk := await file.read(1024 * 256):
        data += chunk
        if len(data) > max_bytes:
            raise HTTPException(413, f"Logo quá lớn (tối đa {settings.MAX_LOGO_MB}MB).")
    ext = _sniff_image(bytes(data[:16]))
    if not ext:
        raise HTTPException(400, "Chỉ nhận ảnh PNG, JPG hoặc WEBP (PNG nền trong suốt cho đẹp nhất).")

    logo_id = new_id()
    path = d / f"{logo_id}{ext}"
    path.write_bytes(bytes(data))
    try:
        meta = await ffmpeg_service.probe(path)
        if not meta.width or not meta.height:
            raise ValueError("no size")
    except Exception:                                   # noqa: BLE001
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Không đọc được ảnh logo (file hỏng).")
    return {"logo_id": logo_id, "width": meta.width, "height": meta.height}


@router.get("/assets/logo/{logo_id}")
async def get_logo(logo_id: str, user: User = Depends(require_user)):
    if not _HEX32.match(logo_id):
        raise HTTPException(404, "Không tìm thấy logo")
    try:
        path = pipeline_runner.find_user_logo(user.id, logo_id)
    except ValueError:
        raise HTTPException(404, "Không tìm thấy logo")
    mt = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=mt, headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------------------
# Video: thông tin + khung hình xem trước
# ---------------------------------------------------------------------------

@router.get("/videos/{video_id}")
async def video_info(video_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    v = await get_owned(session, Video, video_id)
    if not v:
        raise HTTPException(404, "Video không tồn tại")
    return {
        "id": v.id, "filename": v.original_filename, "duration_sec": v.duration_sec,
        "width": v.width, "height": v.height, "ready": bool(v.file_path and Path(v.file_path).exists()),
    }


@router.get("/videos/{video_id}/stream")
async def video_stream(video_id: str, request: Request, session: AsyncSession = Depends(get_session),
                       user: User = Depends(require_user)):
    """Phát video gốc lên màn hình chiếu (hỗ trợ Range để tua)."""
    v = await get_owned(session, Video, video_id)
    if not v or not v.file_path or not Path(v.file_path).exists():
        raise HTTPException(404, "Video chưa sẵn sàng")
    mt = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
          ".mkv": "video/x-matroska"}.get(Path(v.file_path).suffix.lower(), "video/mp4")
    return _range_response(request, Path(v.file_path), mt)


@router.get("/videos/{video_id}/frame")
async def video_frame(video_id: str, t: float = Query(default=1.0, ge=0, le=86400),
                      session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    v = await get_owned(session, Video, video_id)
    if not v or not v.file_path or not Path(v.file_path).exists():
        raise HTTPException(404, "Video chưa sẵn sàng")
    if v.duration_sec:
        t = min(t, max(0.0, v.duration_sec - 0.1))
    try:
        jpg = await ffmpeg_service.grab_frame_jpeg(Path(v.file_path), t)
    except ffmpeg_service.FFmpegError:
        raise HTTPException(500, "Không lấy được khung hình")
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=120"})


# ---------------------------------------------------------------------------
# Bắt đầu pipeline
# ---------------------------------------------------------------------------

@router.post("/pipeline")
async def start_pipeline(req: PipelineRequest, background_tasks: BackgroundTasks,
                         session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "Máy chủ chưa cài FFmpeg.")
    video = await get_owned(session, Video, req.video_id)
    if not video or not video.file_path or not Path(video.file_path).exists():
        raise HTTPException(404, "Video không tồn tại hoặc chưa tải xong.")

    sub = req.subtitle
    if sub.enabled and sub.translate:
        _, problem = await pipeline_runner.resolve_translator(user.id, sub.provider)
        if problem:
            raise HTTPException(400, problem)
    if req.dub.enabled and req.dub.provider == "gemini_tts":
        ua = await ai_providers.load_user_ai(user.id)
        if not ua or ua.provider != "gemini":
            raise HTTPException(400, "Giọng Gemini cần API key Gemini ở mục Kết nối AI.")
    if req.dub.enabled and req.dub.provider == "openai_tts" and not settings.OPENAI_API_KEY:
        raise HTTPException(400, "Chưa cấu hình OPENAI_API_KEY cho OpenAI TTS. Hãy chọn Edge TTS.")
    if req.dub.enabled and req.dub.provider == "vieneu":
        if not _has("vieneu"):
            raise HTTPException(400, "Máy chủ chưa cài VieNeu-TTS (pip install -r requirements-voice.txt).")
        vid = req.dub.voice_id
        if vid.startswith("clone:"):
            if not settings.VOICE_CLONING_ENABLED:
                raise HTTPException(403, "Tính năng nhân bản giọng đang tắt trên máy chủ này.")
            try:
                voice_clones.clone_path(voice_clones.user_dir(settings.VOICES_DIR, user.id), vid[len("clone:"):])
            except voice_clones.CloneError as e:
                raise HTTPException(400, str(e))
        elif not vid.startswith("preset:"):
            raise HTTPException(400, "Giọng VieNeu không hợp lệ. Hãy chọn lại giọng trong danh sách.")
    if req.logo_overlay.enabled:
        try:
            pipeline_runner.find_user_logo(user.id, req.logo_overlay.logo_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if sub.enabled and sub.source == "job":
        if not await get_owned(session, Job, sub.source_job_id):
            raise HTTPException(404, "Không tìm thấy phụ đề nguồn.")

    active = (await session.execute(
        select(Job.id).where(Job.user_id == user.id, Job.status.in_(_ACTIVE)))).scalars().all()
    if len(active) >= MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"Bạn đang có {len(active)} tác vụ chạy. Hãy đợi xong hoặc huỷ bớt rồi thử lại.")

    job = Job(video_id=video.id, user_id=user.id, job_type="pipeline", status=JobStatus.QUEUED,
              stage="Đang xếp hàng chờ tới lượt…")
    session.add(job)
    await session.commit()
    background_tasks.add_task(pipeline_runner.execute, job.id, req, video.file_path, user.id)
    return {"job_id": job.id, "status": job.status}


# ---------------------------------------------------------------------------
# Lịch sử + phát video kết quả
# ---------------------------------------------------------------------------

@router.get("/jobs")
async def list_jobs(limit: int = Query(default=30, ge=1, le=100), session: AsyncSession = Depends(get_session),
                    user: User = Depends(require_user)):
    rows = (await session.execute(
        select(Job, Video.original_filename).join(Video, Video.id == Job.video_id, isouter=True)
        .where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit))).all()
    out = []
    for job, fname in rows:
        out.append({
            "id": job.id, "job_type": job.job_type, "status": job.status, "progress": job.progress,
            "stage": job.stage, "error_message": job.error_message, "filename": fname,
            "created_at": job.created_at.isoformat() + "Z",
            "has_result": bool(job.result_path and Path(job.result_path).exists()),
        })
    return out


def _range_response(request: Request, path: Path, media_type: str) -> Response:
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng:
        m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
        if m and (m.group(1) or m.group(2)):
            a, b = m.groups()
            if a == "":                                  # bytes=-N (N byte cuối)
                start = max(0, size - int(b))
            else:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            if start >= size or start > end:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    length = end - start + 1

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": "private, no-store"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=headers)


@router.get("/jobs/{job_id}/stream")
async def stream_result(job_id: str, request: Request, session: AsyncSession = Depends(get_session),
                        user: User = Depends(require_user)):
    job = await get_owned(session, Job, job_id)
    if not job or not job.result_path or not Path(job.result_path).exists():
        raise HTTPException(404, "Chưa có video kết quả")
    return _range_response(request, Path(job.result_path), "video/mp4")
