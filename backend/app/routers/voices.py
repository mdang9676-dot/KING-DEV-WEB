"""
/api/voice-clones — mẫu giọng NHÂN BẢN của người dùng (dùng cho lồng tiếng với nguồn giọng VieNeu).

  GET    /api/voice-clones                 danh sách giọng nhân bản của tôi
  POST   /api/voice-clones                 tạo giọng (multipart: file, name, consent=true BẮT BUỘC)
  GET    /api/voice-clones/{id}/sample     nghe lại mẫu đã tải lên (chỉ chính chủ)
  DELETE /api/voice-clones/{id}            xoá mẫu giọng

Chỉ chính chủ thấy/dùng/xoá được mẫu giọng của mình. Việc bắt buộc xác nhận quyền sử dụng giọng nằm ở
services/voice_clones.create_clone (không thể bỏ qua từ route).
"""
import asyncio
import hashlib
import hmac
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.core import auth_utils, crypto
from app.core.config import settings
from app.core.deps import client_ip, require_user
from app.models.db import User
from app.services import tts_providers, voice_clones

router = APIRouter(prefix="/api/voice-clones", tags=["voice-clones"])

# Đuôi file chỉ là cổng sơ bộ; nội dung thật do ffprobe/ffmpeg kiểm tra. Cho phép cả video ngắn (lấy tiếng nói ra).
_ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm", ".mp4", ".mov", ".mkv"}
_throttle = auth_utils.Throttle(limit=8, window=300)      # 8 lần tạo / 5 phút / người dùng


def _udir(user: User) -> Path:
    return voice_clones.user_dir(settings.VOICES_DIR, user.id)


@router.get("")
async def list_my_clones(user: User = Depends(require_user)):
    return voice_clones.list_clones(_udir(user)) if settings.VOICE_CLONING_ENABLED else []


@router.post("")
async def create_my_clone(request: Request, file: UploadFile = File(...), name: str = Form(..., max_length=120),
                          consent: bool = Form(False), user: User = Depends(require_user)):
    if not settings.VOICE_CLONING_ENABLED:
        raise HTTPException(403, "Tính năng nhân bản giọng đang tắt trên máy chủ này.")
    if consent is not True:
        raise HTTPException(400, "Bạn cần xác nhận đây là giọng của chính bạn hoặc bạn có quyền sử dụng giọng này.")
    if _throttle.blocked(user.id):
        raise HTTPException(429, "Bạn tạo giọng quá nhanh, hãy đợi vài phút rồi thử lại.")
    _throttle.fail(user.id)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise HTTPException(400, "Hãy tải file ghi âm giọng nói: WAV, MP3, M4A, OGG, FLAC hoặc WebM.")

    tmp_dir = settings.DATA_DIR / "tmp_voice"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}{suffix}"
    max_bytes = settings.MAX_VOICE_SAMPLE_MB * 1024 * 1024
    size = 0
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(1024 * 256):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"File quá lớn (tối đa {settings.MAX_VOICE_SAMPLE_MB}MB). "
                                             "Mẫu giọng chỉ cần 5–10 giây.")
                f.write(chunk)
        try:
            audit = {"consent_version": settings.VOICE_CONSENT_VERSION,
                     "consent_ip_hash": hmac.new(crypto.get_app_secret(), b"consent-ip|" + client_ip(request).encode(), hashlib.sha256).hexdigest()}
            return await voice_clones.create_clone(_udir(user), tmp, name, consent, settings.MAX_VOICE_CLONES_PER_USER, audit)
        except voice_clones.CloneError as e:
            raise HTTPException(400, str(e))
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/{clone_id}/sample")
async def get_sample(clone_id: str, user: User = Depends(require_user)):
    try:
        path = voice_clones.clone_path(_udir(user), clone_id)
    except voice_clones.CloneError as e:
        raise HTTPException(404, str(e))
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, max-age=600"})


@router.delete("/{clone_id}")
async def delete_my_clone(clone_id: str, user: User = Depends(require_user)):
    if not voice_clones.delete_clone(_udir(user), clone_id):
        raise HTTPException(404, "Không tìm thấy giọng nhân bản.")
    await asyncio.to_thread(tts_providers.forget_vieneu_clone, user.id, clone_id)     # gỡ cả khỏi RAM của engine
    return {"ok": True}
