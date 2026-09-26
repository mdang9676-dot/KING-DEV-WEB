"""
/api/ai/*  — kết nối AI bằng API key của người dùng + danh sách giọng + nghe thử giọng.

- Key được MÃ HOÁ (Fernet) trước khi lưu, không bao giờ trả lại đầy đủ cho trình duyệt (chỉ dạng che: sk-p…abcd).
- Chỉ gọi tới nhà cung cấp đã chọn (bảng cố định, không nhận URL tự do) -> không SSRF, không gửi key sang bên thứ ba.
"""
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core import auth_utils, crypto
from app.core.config import settings
from app.models.db import User, UserAISettings, async_session
from app.core.deps import require_user
from app.services import ai_providers as ai
from app.services import tts_providers, voice_clones

router = APIRouter(prefix="/api/ai", tags=["ai"])
PREVIEW_TEXT = "KINGWEB DEV"
_VOICE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_preview_throttle = auth_utils.Throttle(limit=20, window=60)   # 20 lần nghe thử / phút / người dùng


class DetectIn(BaseModel):
    api_key: str = Field(max_length=400)


class ModelsIn(BaseModel):
    provider: str = Field(max_length=40)
    api_key: str | None = Field(default=None, max_length=400)   # bỏ trống = dùng key đã lưu


class SettingsIn(BaseModel):
    provider: str = Field(max_length=40)
    model: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/\-]+$")
    api_key: str | None = Field(default=None, max_length=400)   # bỏ trống = giữ key cũ, chỉ đổi model


class PreviewIn(BaseModel):
    provider: str = Field(pattern=r"^(edge_tts|gemini_tts|vieneu)$")
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_.:\-]{1,80}$")


async def _saved(user_id: str) -> UserAISettings | None:
    async with async_session() as s:
        return (await s.execute(select(UserAISettings).where(UserAISettings.user_id == user_id))).scalar_one_or_none()


def _vieneu_for(user_id: str) -> tts_providers.VieNeuProvider:
    return tts_providers.VieNeuProvider(
        clones_dir=voice_clones.user_dir(settings.VOICES_DIR, user_id), owner=user_id,
        mode=settings.VIENEU_MODE, precision=settings.VIENEU_PRECISION, backend=settings.VIENEU_BACKEND)


def _raise(e: Exception):
    if isinstance(e, ai.AIKeyRejected):
        raise HTTPException(400, str(e))
    raise HTTPException(502, str(e))


@router.get("/providers")
async def providers():
    return ai.provider_list()


@router.post("/detect")
async def detect(body: DetectIn):
    """Đoán nhà cung cấp từ dạng key. Chỉ xử lý cục bộ — key KHÔNG được gửi đi đâu và không được lưu."""
    return ai.detect_provider(body.api_key)


@router.post("/models")
async def models(body: ModelsIn, user: User = Depends(require_user)):
    if body.provider not in ai.PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không được hỗ trợ.")
    key = (body.api_key or "").strip()
    if not key:
        row = await _saved(user.id)
        if not row or row.provider != body.provider:
            raise HTTPException(400, "Chưa có API key. Hãy nhập key.")
        key = crypto.decrypt_text(row.key_enc)
    try:
        return {"models": await ai.list_models(body.provider, key)}
    except ai.AIError as e:
        _raise(e)


@router.get("/settings")
async def get_settings(user: User = Depends(require_user)):
    row = await _saved(user.id)
    if not row:
        return {"configured": False}
    return {"configured": True, "provider": row.provider, "label": ai.PROVIDERS.get(row.provider, {}).get("label", row.provider),
            "model": row.model, "key_masked": ai.mask_key(crypto.decrypt_text(row.key_enc))}


@router.put("/settings")
async def save_settings(body: SettingsIn, user: User = Depends(require_user)):
    if body.provider not in ai.PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không được hỗ trợ.")
    row = await _saved(user.id)
    key = (body.api_key or "").strip()
    if not key:
        if not row or row.provider != body.provider:
            raise HTTPException(400, "Hãy nhập API key.")
        key = crypto.decrypt_text(row.key_enc)
    try:                                                  # xác minh key thật sự dùng được TRƯỚC khi lưu
        available = await ai.list_models(body.provider, key)
    except ai.AIError as e:
        _raise(e)
    if available and body.model not in available:
        raise HTTPException(400, f"Model '{body.model}' không có trong danh sách của {ai.PROVIDERS[body.provider]['label']}.")
    async with async_session() as s:
        row = (await s.execute(select(UserAISettings).where(UserAISettings.user_id == user.id))).scalar_one_or_none()
        if row:
            row.provider, row.model, row.key_enc = body.provider, body.model, crypto.encrypt_text(key)
        else:
            s.add(UserAISettings(user_id=user.id, provider=body.provider, model=body.model, key_enc=crypto.encrypt_text(key)))
        await s.commit()
    return {"ok": True, "provider": body.provider, "model": body.model, "key_masked": ai.mask_key(key)}


@router.delete("/settings")
async def delete_settings(user: User = Depends(require_user)):
    async with async_session() as s:
        row = (await s.execute(select(UserAISettings).where(UserAISettings.user_id == user.id))).scalar_one_or_none()
        if row:
            await s.delete(row)
            await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Giọng đọc
# ---------------------------------------------------------------------------

@router.get("/voices")
async def voices(provider: str = Query(pattern=r"^(edge_tts|gemini_tts|vieneu)$"), lang: str | None = Query(default=None, max_length=8),
                 user: User = Depends(require_user)):
    if provider == "vieneu":
        if not tts_providers.vieneu_installed():
            raise HTTPException(400, "Máy chủ chưa cài VieNeu-TTS (pip install -r requirements-voice.txt).")
        udir = voice_clones.user_dir(settings.VOICES_DIR, user.id)
        mine = [] if not settings.VOICE_CLONING_ENABLED else [{"id": tts_providers.CLONE_PREFIX + c["id"], "name": f"{c['name']} (giọng của bạn)", "locale": "vi",
                 "gender": "", "cloned": True, "clone_id": c["id"]} for c in voice_clones.list_clones(udir)]
        try:
            vp = _vieneu_for(user.id)
            return mine + await vp.list_voices(lang)
        except Exception as e:                            # noqa: BLE001
            raise HTTPException(502, f"Không nạp được VieNeu-TTS: {e}")
    if provider == "gemini_tts":
        row = await _saved(user.id)
        if not row or row.provider != "gemini":
            raise HTTPException(400, "Giọng Gemini cần API key Gemini (mục Kết nối AI).")
        vp = tts_providers.GeminiTTSProvider("x", settings.GEMINI_TTS_MODEL)   # chỉ để lấy danh sách tĩnh
    else:
        vp = tts_providers.EdgeTTSProvider()
    try:
        return await vp.list_voices(lang)
    except Exception as e:                                # noqa: BLE001
        raise HTTPException(502, f"Không lấy được danh sách giọng: {e}")


@router.post("/voices/preview")
async def preview(body: PreviewIn, user: User = Depends(require_user)):
    """Đọc thử đúng cụm 'KINGWEB DEV'. Kết quả được cache theo (nhà cung cấp, giọng) để tiết kiệm request/token."""
    if _preview_throttle.blocked(user.id):
        raise HTTPException(429, "Nghe thử quá nhanh, hãy đợi vài giây.")
    _preview_throttle.fail(user.id)

    if body.provider == "vieneu":
        if not tts_providers.vieneu_installed():
            raise HTTPException(400, "Máy chủ chưa cài VieNeu-TTS (pip install -r requirements-voice.txt).")
        if body.voice_id.startswith(tts_providers.CLONE_PREFIX):     # chỉ chính chủ mới nghe thử được giọng nhân bản
            if not settings.VOICE_CLONING_ENABLED:
                raise HTTPException(403, "Tính năng nhân bản giọng đang tắt trên máy chủ này.")
            try:
                voice_clones.clone_path(voice_clones.user_dir(settings.VOICES_DIR, user.id), body.voice_id[len(tts_providers.CLONE_PREFIX):])
            except voice_clones.CloneError as e:
                raise HTTPException(404, str(e))
        elif not body.voice_id.startswith(tts_providers.PRESET_PREFIX):
            raise HTTPException(400, "Giọng VieNeu không hợp lệ.")

    ext = "wav" if body.provider in ("gemini_tts", "vieneu") else "mp3"
    model = settings.GEMINI_TTS_MODEL if body.provider == "gemini_tts" else (
        f"{settings.VIENEU_MODE}|{settings.VIENEU_PRECISION}" if body.provider == "vieneu" else "")
    tag = hashlib.sha1(f"{body.provider}|{body.voice_id}|{model}|{PREVIEW_TEXT}".encode()).hexdigest()[:20]
    cache = settings.DATA_DIR / "voice_previews"
    cache.mkdir(parents=True, exist_ok=True)
    path: Path = cache / f"{tag}.{ext}"
    if body.provider == "vieneu" and body.voice_id.startswith(tts_providers.CLONE_PREFIX):
        # bản nghe thử của GIỌNG NHÂN BẢN nằm cạnh mẫu giọng (không vào cache dùng chung) để xoá giọng là xoá luôn cả bản nghe thử
        path = voice_clones.user_dir(settings.VOICES_DIR, user.id) / f"{body.voice_id[len(tts_providers.CLONE_PREFIX):]}.preview.wav"

    if not path.exists():
        if body.provider == "gemini_tts":
            ua = await ai.load_user_ai(user.id)
            if not ua or ua.provider != "gemini":
                raise HTTPException(400, "Giọng Gemini cần API key Gemini (mục Kết nối AI).")
            vp = tts_providers.GeminiTTSProvider(ua.api_key, settings.GEMINI_TTS_MODEL)
        elif body.provider == "vieneu":
            vp = _vieneu_for(user.id)
        else:
            vp = tts_providers.EdgeTTSProvider()
        try:
            await vp.synthesize(PREVIEW_TEXT, body.voice_id, path)
        except Exception as e:                            # noqa: BLE001
            path.unlink(missing_ok=True)
            raise HTTPException(502, f"Không tạo được giọng nghe thử: {e}")
    return Response(path.read_bytes(), media_type="audio/wav" if ext == "wav" else "audio/mpeg",
                    headers={"Cache-Control": "private, max-age=86400"})
