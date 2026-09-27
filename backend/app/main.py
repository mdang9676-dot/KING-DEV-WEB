"""
Video Studio - Backend entry point.
Chạy: uvicorn app.main:app --host 0.0.0.0 --port 8000        (chạy 1 worker: hàng đợi job nằm trong RAM)
Giao diện web được phục vụ luôn tại "/" (cùng origin với API => cookie phiên httpOnly hoạt động, không cần CORS).
"""
import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core import auth_utils
from app.core.config import settings
from app.core.deps import require_user
from app.models.db import async_session, init_db, User
from app.routers import admin, ai, auth, import_url, jobs, ocr, pipeline, processing, upload, voices, wallet
from app.services.cleanup import cleanup_loop
from app.services import tts_providers
from app.services.pipeline_runner import mark_orphan_jobs

log = logging.getLogger("app")


async def _seed_admin() -> None:
    """Tạo sẵn 1 tài khoản admin nếu chưa có (email/mật khẩu lấy từ settings, xem core/config.py).
    Không tự sửa nếu tài khoản đã tồn tại (tránh ghi đè mật khẩu admin đã tự đổi)."""
    from sqlalchemy import select
    email = auth_utils.normalize_email(settings.ADMIN_SEED_EMAIL)
    async with async_session() as s:
        exists = (await s.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
        if exists:
            return
        s.add(User(
            email=email, name="Admin", is_admin=True,
            password_hash=auth_utils.hash_password(settings.ADMIN_SEED_PASSWORD),
        ))
        await s.commit()
        log.info("Đã tạo tài khoản admin gieo sẵn: %s (hãy đổi mật khẩu sau khi đăng nhập lần đầu)", email)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_admin()
    orphaned = await mark_orphan_jobs()
    if orphaned:
        log.warning("Đã đánh dấu %d job dở dang từ lần chạy trước là lỗi", orphaned)
    cleanup_task = asyncio.create_task(cleanup_loop())
    if settings.VIENEU_PRELOAD and tts_providers.vieneu_installed():
        # nạp model VieNeu nền, không chặn việc khởi động (lần đầu sẽ tải model từ Hugging Face)
        async def _preload():
            try:
                await asyncio.to_thread(tts_providers.get_vieneu_engine, settings.VIENEU_MODE,
                                        settings.VIENEU_PRECISION, settings.VIENEU_BACKEND)
                log.info("VieNeu-TTS đã sẵn sàng")
            except Exception as e:                     # noqa: BLE001
                log.warning("Không nạp trước được VieNeu-TTS: %s", e)
        app.state.preload_task = asyncio.create_task(_preload())   # giữ tham chiếu để không bị thu gom
    yield
    cleanup_task.cancel()


app = FastAPI(
    title=settings.APP_NAME, lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,      # production: không lộ tài liệu API công khai
    redoc_url=None, openapi_url="/openapi.json" if settings.DEBUG else None,
)

if settings.CORS_ORIGINS:                                # chỉ bật khi frontend ở domain khác (nêu rõ từng origin)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "Authorization"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# Đăng ký/đăng nhập: công khai. Mọi API nghiệp vụ còn lại BẮT BUỘC đăng nhập.
protected = [Depends(require_user)]
app.include_router(auth.router)
for r in (upload.router, import_url.router, jobs.router, processing.router, ocr.router, pipeline.router, ai.router,
          voices.router, wallet.router, admin.router):
    app.include_router(r, dependencies=protected)   # admin.router tự kiểm tra thêm is_admin bên trong (require_admin)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def _find_frontend() -> Path | None:
    here = Path(__file__).resolve()
    for cand in (settings.FRONTEND_DIR, here.parents[2] / "frontend", here.parents[1] / "frontend"):
        if cand and Path(cand).is_dir() and (Path(cand) / "index.html").exists():
            return Path(cand)
    return None


_fe = _find_frontend()
if _fe:
    app.mount("/", StaticFiles(directory=str(_fe), html=True), name="frontend")   # phải đặt SAU các route API
