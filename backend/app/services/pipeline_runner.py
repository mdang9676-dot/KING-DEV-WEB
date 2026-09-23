"""
Điều phối pipeline "một chạm" chạy NGẦM trên server:

  chuẩn bị -> (OCR/Whisper) -> dịch -> lồng tiếng -> render 1 lượt FFmpeg (che/xoá, nâng nét, phụ đề mới, logo, trộn tiếng)

- Chạy hoàn toàn phía server: người dùng đóng tab / mất mạng thì job VẪN chạy; mở lại trang sẽ thấy tiến trình
  (trạng thái nằm trong DB). Lưu ý: nếu tiến trình server bị dừng/khởi động lại, job đang chạy sẽ được đánh dấu lỗi.
- Tiến trình là số THẬT: mỗi bước có trọng số, riêng bước render lấy % từ `ffmpeg -progress`.
- Huỷ được giữa chừng (kể cả đang render); một số job chạy đồng thời giới hạn bởi MAX_CONCURRENT_JOBS.
- Cần chạy 1 worker (uvicorn không dùng --workers > 1) vì hàng đợi/semaphore nằm trong RAM.
"""
import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import settings
from app.core.security import safe_join
from app.models.db import async_session, Job, JobStatus, Video, SubtitleSegment
from app.models.pipeline import PipelineRequest
from app.services import (
    ai_providers, dubbing_service, ffmpeg_service, ocr_service, region_detection, translation_providers, tts_providers,
    voice_clones, whisper_service,
)
from app.services import filter_builder as fb
from app.services.errors import JobCancelled, gather_or_cancel
from app.services import vi_tts_normalize
from app.services.glossary import parse_glossary
from app.services.subtitle_cleanup import filter_junk
from app.services.subtitle_qc import find_overloaded
from app.services.ocr_text import LANG_LABEL, detect_script_lang, script_family
from app.services.subtitle_service import Segment, to_ass_styled, to_srt

log = logging.getLogger("pipeline")
_sem = asyncio.Semaphore(max(1, settings.MAX_CONCURRENT_JOBS))
_TERMINAL = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)


# ---------------------------------------------------------------------------
# Trọng số & ngữ cảnh tiến trình
# ---------------------------------------------------------------------------

def compute_weights(req: PipelineRequest) -> dict[str, float]:
    w: dict[str, float] = {"prepare": 2}
    sub = req.subtitle
    if sub.enabled:
        w["analyze"] = {"ocr": 35, "whisper": 30, "job": 1}[sub.source]
        if sub.translate:
            w["translate"] = 8
    if req.dub.enabled:
        w["dub"] = 15
    w["render"] = 55 if req.enhance.enabled else 40
    return w


class Ctx:
    """Theo dõi tiến trình tổng (0..99%) từ các bước có trọng số, ghi DB có điều tiết, và kiểm tra huỷ."""

    def __init__(self, job_id: str, weights: dict[str, float]):
        total = sum(weights.values())
        self.w = {k: v / total for k, v in weights.items()}
        self.job_id = job_id
        self.done = 0.0
        self.cur: str | None = None
        self.frac = 0.0
        self._last_write = 0.0
        self._last_pct = -1
        self._last_cancel_check = 0.0
        self._cancelled = False

    def pct(self) -> int:
        return int(min(99, (self.done + self.w.get(self.cur, 0.0) * self.frac) * 100))

    async def _db(self, **fields) -> bool:
        async with async_session() as s:
            job = await s.get(Job, self.job_id)
            if job is None:
                return False
            if job.status == JobStatus.CANCELLED:
                self._cancelled = True
                return False
            for k, v in fields.items():
                setattr(job, k, v)
            await s.commit()
            return True

    async def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if now - self._last_cancel_check < 1.0:
            return False
        self._last_cancel_check = now
        async with async_session() as s:
            job = await s.get(Job, self.job_id)
            if job is not None and job.status == JobStatus.CANCELLED:
                self._cancelled = True
        return self._cancelled

    async def start(self, key: str, text: str, status: JobStatus) -> None:
        self.cur, self.frac = key, 0.0
        self._last_pct = self.pct()
        await self._db(status=status, stage=text, progress=self.pct())
        if self._cancelled:
            raise JobCancelled()

    async def set_stage(self, text: str) -> None:
        await self._db(stage=text)

    async def progress(self, frac: float) -> None:
        self.frac = max(0.0, min(1.0, frac))
        now = time.monotonic()
        pct = self.pct()
        if pct != self._last_pct and now - self._last_write >= 0.7:
            self._last_pct, self._last_write = pct, now
            await self._db(progress=pct)
        if self._cancelled or await self.is_cancelled():
            raise JobCancelled()

    def end(self) -> None:
        self.done += self.w.get(self.cur, 0.0)
        self.frac = 0.0


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------

async def resolve_translator(user_id: str, provider_name: str | None):
    """Chọn bộ dịch: AI của người dùng (ưu tiên) -> provider cấu hình trên server. Trả (translator, None) hoặc (None, lỗi)."""
    if provider_name in (None, "user_ai"):
        ua = await ai_providers.load_user_ai(user_id)
        if ua:
            return ai_providers.LLMTranslator(ua), None
        if provider_name == "user_ai":
            return None, "Bạn chưa lưu API key AI. Hãy nhập ở mục Kết nối AI."
    p = translation_providers.get_translation_provider(None if provider_name == "user_ai" else provider_name)
    if p.name == "demo":
        return None, "Chưa có AI để dịch: hãy nhập API key AI (Gemini, OpenAI…) ở mục Kết nối AI."
    return p, None


def find_user_logo(user_id: str, logo_id: str) -> Path:
    base = (settings.ASSETS_DIR / user_id).resolve()
    for cand in base.glob(f"{logo_id}.*"):
        if cand.resolve().parent == base:
            return cand
    raise ValueError("Không tìm thấy logo đã tải lên (có thể đã bị xoá). Hãy tải lại logo.")


def _rect_px_to_norm(px: tuple[int, int, int, int], w: int, h: int) -> fb.Rect:
    x, y, rw, rh = px
    return fb.Rect(x / w, y / h, rw / w, rh / h)


def _friendly(e: Exception) -> str:
    if isinstance(e, ffmpeg_service.FFmpegError):
        return "Lỗi xử lý video (FFmpeg): " + str(e).strip()[-350:]
    if isinstance(e, ValueError):
        return str(e)
    return f"Lỗi không mong muốn ({type(e).__name__}): {str(e)[:300]}"


# ---------------------------------------------------------------------------
# Thân pipeline
# ---------------------------------------------------------------------------

async def _do(ctx: Ctx, job_id: str, req: PipelineRequest, video_path: Path, user_id: str) -> dict:
    work = safe_join(settings.JOBS_DIR, job_id)
    out_dir = safe_join(settings.OUTPUT_DIR, job_id)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    info: dict = {}

    # ---- 1) chuẩn bị ----
    await ctx.start("prepare", "Đang kiểm tra video", JobStatus.PROCESSING)
    meta = await ffmpeg_service.probe(video_path)
    if not meta.width or not meta.height:
        raise ValueError("Không đọc được hình ảnh của file này (file hỏng hoặc không phải video).")
    W, H = meta.width, meta.height
    ctx.end()

    sub = req.subtitle
    segments: list[Segment] = []
    region_px: tuple[int, int, int, int] | None = None
    if sub.enabled and sub.region:
        region_px = fb.rect_to_px(fb.Rect(**sub.region.model_dump()), W, H)

    # ---- 2) lấy phụ đề gốc ----
    if sub.enabled:
        if sub.source == "ocr":
            await ctx.start("analyze", "Đang tìm vùng phụ đề trong video…", JobStatus.PROCESSING)
            lang = sub.source_lang or "en"
            base = 0.0
            if region_px is None:
                det = await region_detection.auto_detect_region(
                    video_path, work, lang, sub.ocr_engine, 24, W, H)
                region_px = (det.x, det.y, det.width, det.height)
                info["detected_region"] = {"x": det.x, "y": det.y, "w": det.width, "h": det.height,
                                           "confidence": det.confidence, "fallback": det.fallback}
                if det.fallback:
                    warnings.append("Không tìm thấy vùng phụ đề rõ ràng nên đã dùng vùng đoán ở đáy khung hình. "
                                    "Nếu kết quả sai, hãy tự vẽ vùng phụ đề rồi chạy lại. Lý do: " + det.reason)
                base = 0.3
                await ctx.progress(base)
            await ctx.set_stage("Đang đọc phụ đề trong video (OCR)…")

            async def ocr_prog(f: float):
                await ctx.progress(base + (1 - base) * f)

            segments = await ocr_service.extract_subtitle_from_video(
                video_path, work, ocr_service.Region(*region_px), lang, sub.ocr_fps, sub.ocr_engine,
                on_progress=ocr_prog, is_cancelled=ctx.is_cancelled)
            segments = filter_junk(segments)               # bỏ rác OCR còn sót (chữ lẻ chớp nhoáng, toàn ký hiệu)
            if not segments:
                raise ValueError("OCR không đọc được chữ nào trong vùng đã chọn. Hãy kiểm tra ngôn ngữ chữ trên video, "
                                 "vẽ lại vùng phụ đề cho khít, hoặc dùng nguồn 'Nghe giọng nói'.")

        elif sub.source == "whisper":
            await ctx.start("analyze", "Đang nhận dạng giọng nói…", JobStatus.TRANSCRIBING)
            if not meta.has_audio:
                raise ValueError("Video không có âm thanh để nhận dạng giọng nói.")
            wav = work / "audio.wav"
            await ffmpeg_service.extract_audio(video_path, wav)
            await ctx.progress(0.15)
            segs, detected = await asyncio.to_thread(whisper_service.transcribe_audio, str(wav), sub.source_lang, sub.whisper_model)
            segments = [Segment(i, s.start_ms, s.end_ms, s.text) for i, s in enumerate(segs)]
            info["detected_language"] = detected
            if not segments:
                raise ValueError("Không nhận dạng được lời nói nào trong video.")

        else:  # job có sẵn
            await ctx.start("analyze", "Đang nạp phụ đề có sẵn…", JobStatus.PROCESSING)
            async with async_session() as s:
                src = await s.get(Job, sub.source_job_id)
                if not src or src.user_id != user_id:
                    raise ValueError("Không tìm thấy phụ đề nguồn.")
                rows = (await s.execute(select(SubtitleSegment).where(SubtitleSegment.job_id == src.id)
                                        .order_by(SubtitleSegment.index))).scalars().all()
            segments = [Segment(r.index, r.start_ms, r.end_ms, r.text, r.translated_text) for r in rows]
            if not segments:
                raise ValueError("Phụ đề nguồn đang trống.")
        # đoán ngôn ngữ thật của chữ vừa đọc: cảnh báo nếu người dùng chọn sai, và gợi ý cho bước dịch
        guessed = detect_script_lang(" ".join(x.text for x in segments)) if sub.source == "ocr" else None
        if guessed:
            info["detected_language"] = guessed
            if sub.source_lang and script_family(guessed) != script_family(sub.source_lang):
                warnings.append(f"Chữ đọc được trong video có vẻ là {LANG_LABEL.get(guessed, guessed)}, không khớp ngôn ngữ gốc bạn chọn "
                                f"({sub.source_lang}). Nếu bản dịch lạ, hãy chọn lại ngôn ngữ gốc rồi chạy lại.")
        src_lang = sub.source_lang or (guessed if guessed and guessed != "en" else None)
        ctx.end()

        # ---- 3) dịch (theo LÔ bằng AI của người dùng nếu có -> ít request/token) ----
        if sub.translate:
            await ctx.start("translate", "Đang dịch phụ đề…", JobStatus.TRANSLATING)
            translator, problem = await resolve_translator(user_id, sub.provider)
            if problem:
                raise ValueError(problem)
            todo = [x for x in segments if not x.translated_text]     # job nguồn có thể đã có bản dịch
            if hasattr(translator, "translate_all"):
                outs = await translator.translate_all([x.text for x in todo], sub.target_lang, src_lang,
                                                      on_progress=ctx.progress, glossary=parse_glossary(sub.glossary),
                                                      style=sub.style_genre)
                for x, t in zip(todo, outs):
                    x.translated_text = t
            else:                                                       # provider cấu hình trên server: từng dòng
                if parse_glossary(sub.glossary):
                    warnings.append("Từ điển thuật ngữ chỉ áp dụng khi dịch bằng AI của bạn (mục Kết nối AI); lần này đã bỏ qua.")
                sem = asyncio.Semaphore(4)
                done = 0

                async def tr(seg: Segment):
                    nonlocal done
                    async with sem:
                        for attempt in range(3):
                            try:
                                seg.translated_text = await translator.translate(seg.text, sub.target_lang, sub.source_lang)
                                break
                            except Exception:          # noqa: BLE001  (lỗi mạng thoáng qua -> thử lại)
                                if attempt == 2:
                                    raise
                                await asyncio.sleep(1.5 * (attempt + 1))
                    done += 1
                    await ctx.progress(done / max(1, len(todo)))

                await gather_or_cancel(*(tr(x) for x in todo))

            if sub.style_genre != "auto" and not hasattr(translator, "translate_all"):
                warnings.append("Quy tắc xưng hô chỉ áp dụng khi dịch bằng AI của bạn (mục Kết nối AI).")
            if sub.condense:                                            # rút gọn dòng quá dài (ký tự/giây vượt ngưỡng)
                over = find_overloaded(segments, sub.max_cps)
                if over and hasattr(translator, "shorten"):
                    await ctx.set_stage(f"Đang rút gọn {len(over)} dòng quá dài…")
                    shorter = await translator.shorten([(segments[i].translated_text, mc) for i, mc in over], sub.target_lang)
                    changed = 0
                    for (i, _), new in zip(over, shorter):
                        if new:
                            segments[i].translated_text, changed = new, changed + 1
                    info["condensed"] = changed
                elif over:
                    warnings.append(f"Có {len(over)} dòng dịch đọc quá nhanh; rút gọn tự động chỉ có khi dịch bằng AI của bạn.")
            ctx.end()

        # lưu phụ đề (cho export/SRT) — đánh lại số thứ tự liên tục
        for i, seg in enumerate(segments):
            seg.index = i
        async with async_session() as s:
            s.add_all([SubtitleSegment(job_id=job_id, index=x.index, start_ms=x.start_ms, end_ms=x.end_ms,
                                       text=x.text, translated_text=x.translated_text) for x in segments])
            await s.commit()
        (out_dir / "subtitle.srt").write_text(to_srt(segments), encoding="utf-8")

    # ---- 4) lồng tiếng ----
    dub_path: Path | None = None
    if req.dub.enabled:
        await ctx.start("dub", "Đang tạo giọng lồng tiếng…", JobStatus.GENERATING_AUDIO)
        if req.dub.provider == "openai_tts" and not settings.OPENAI_API_KEY:
            raise ValueError("Chưa cấu hình OPENAI_API_KEY cho OpenAI TTS. Hãy chọn Edge TTS hoặc điền API key.")
        if req.dub.provider == "demo":
            warnings.append("Đang dùng giọng DEMO (im lặng) — chỉ để thử luồng, không có tiếng đọc thật.")
        if req.dub.provider == "gemini_tts":
            ua = await ai_providers.load_user_ai(user_id)
            if not ua or ua.provider != "gemini":
                raise ValueError("Giọng Gemini cần API key Gemini ở mục Kết nối AI.")
            vp = tts_providers.GeminiTTSProvider(ua.api_key, settings.GEMINI_TTS_MODEL)
        elif req.dub.provider == "vieneu":
            vp = tts_providers.VieNeuProvider(
                clones_dir=voice_clones.user_dir(settings.VOICES_DIR, user_id), owner=user_id,
                mode=settings.VIENEU_MODE, precision=settings.VIENEU_PRECISION, backend=settings.VIENEU_BACKEND)
            if req.dub.voice_id.startswith("clone:") and not settings.VOICE_CLONING_ENABLED:
                raise ValueError("Tính năng nhân bản giọng đang tắt trên máy chủ này.")
            if req.dub.voice_id.startswith("clone:"):        # báo lỗi rõ ràng nếu mẫu giọng đã bị xoá giữa chừng
                voice_clones.clone_path(vp.clones_dir, req.dub.voice_id[len("clone:"):])
        else:
            vp = tts_providers.get_voice_provider(req.dub.provider, api_key=settings.OPENAI_API_KEY)
        dub_path = await dubbing_service.build_dub_track(
            segments, vp, req.dub.voice_id, work / "dub", int(meta.duration_sec * 1000),
            on_progress=ctx.progress, is_cancelled=ctx.is_cancelled, borrow_gap=(req.dub.fit == "natural"),
            text_fn=vi_tts_normalize.normalize_vi if (sub.enabled and sub.translate and sub.target_lang.startswith("vi")) else None)
        ctx.end()

    # ---- 5) dựng spec render ----
    covers: list = []
    if req.logo_remove.enabled and req.logo_remove.cover.mode != "none":
        c = req.logo_remove.cover
        for r in req.logo_remove.regions:
            covers.append((fb.Rect(**r.model_dump()), fb.CoverSpec(c.mode, c.strength, c.color, c.opacity)))
    if sub.enabled and sub.cover.mode != "none" and region_px:
        c = sub.cover
        covers.append((_rect_px_to_norm(region_px, W, H), fb.CoverSpec(c.mode, c.strength, c.color, c.opacity)))

    enhance = None
    if req.enhance.enabled:
        e = req.enhance
        enhance = fb.EnhanceSpec(target_height=e.target_height, sharpen=e.sharpen, denoise=e.denoise)
    ow, oh = fb.output_size(W, H, enhance)

    ass_path = None
    if sub.enabled and sub.burn and segments:
        st = sub.style
        k = oh / H
        reg_out = tuple(int(v * k) for v in region_px) if region_px else None
        font_px = int(oh * st.size_pct / 100)
        if reg_out:
            font_px = max(12, min(font_px, int(reg_out[3] / 1.8)))
        ass = to_ass_styled(segments, ow, oh, font=st.font, font_px=font_px, color=st.color,
                            outline_color=st.outline_color, outline_px=st.outline_px, bold=st.bold,
                            box=st.box, box_color=st.box_color, box_opacity=st.box_opacity,
                            region=reg_out, bilingual=st.bilingual and sub.translate)
        ass_path = work / "subs.ass"
        ass_path.write_text(ass, encoding="utf-8")

    logo_spec = None
    if req.logo_overlay.enabled:
        lg = req.logo_overlay
        logo_file = find_user_logo(user_id, lg.logo_id)
        lm = await ffmpeg_service.probe(logo_file)
        if not lm.width:
            raise ValueError("Không đọc được file logo.")
        logo_spec = fb.LogoSpec(str(logo_file), lm.width, lm.height, lg.x, lg.y, lg.w, lg.opacity)

    spec = fb.RenderSpec(
        src_w=W, src_h=H, has_audio=meta.has_audio, covers=covers, enhance=enhance,
        ass_path=str(ass_path) if ass_path else None, logo=logo_spec,
        fonts_dir=str(settings.FONTS_DIR.resolve()) if any(settings.FONTS_DIR.glob('*')) else None,
        dub_audio_path=str(dub_path) if dub_path else None,
        original_volume=req.dub.original_volume, dubbed_volume=req.dub.dubbed_volume,
        crf=settings.VIDEO_CRF, preset=settings.VIDEO_PRESET,
    )
    out_path = out_dir / "output.mp4"
    plan = fb.build_render_plan(spec, str(video_path), str(out_path))

    # ---- 6) render ----
    result_path: str | None = None
    if plan.reencodes_video or dub_path:
        await ctx.start("render", "Đang xuất video…", JobStatus.RENDERING)
        await ffmpeg_service.run_with_progress(plan.cmd, meta.duration_sec, ctx.progress, ctx.is_cancelled)
        ctx.end()
        result_path = str(out_path)
        info["output"] = {"width": plan.out_w, "height": plan.out_h}

    info["warnings"] = warnings
    info["has_srt"] = bool(segments)
    info["result_path"] = result_path
    return info


# ---------------------------------------------------------------------------
# Điểm vào: chạy nền
# ---------------------------------------------------------------------------

async def execute(job_id: str, req: PipelineRequest, video_path: str, user_id: str) -> None:
    ctx = Ctx(job_id, compute_weights(req))
    work = safe_join(settings.JOBS_DIR, job_id)
    out_dir = safe_join(settings.OUTPUT_DIR, job_id)
    try:
        await ctx._db(status=JobStatus.QUEUED, stage="Đang xếp hàng chờ tới lượt…")
        async with _sem:
            info = await _do(ctx, job_id, req, Path(video_path), user_id)
        async with async_session() as s:
            job = await s.get(Job, job_id)
            if job and job.status != JobStatus.CANCELLED:
                job.status = JobStatus.COMPLETED
                job.progress = 100
                job.stage = "Hoàn tất"
                job.result_path = info.pop("result_path")
                job.params_json = json.dumps(info, ensure_ascii=False)
                await s.commit()
    except (JobCancelled, ffmpeg_service.FFmpegCancelled):
        shutil.rmtree(out_dir, ignore_errors=True)
        async with async_session() as s:
            await s.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.CANCELLED, stage="Đã huỷ"))
            await s.commit()
    except Exception as e:                                 # noqa: BLE001
        log.exception("Pipeline %s lỗi", job_id)
        shutil.rmtree(out_dir, ignore_errors=True)
        async with async_session() as s:
            job = await s.get(Job, job_id)
            if job and job.status != JobStatus.CANCELLED:
                job.status, job.stage, job.error_message = JobStatus.FAILED, "Thất bại", _friendly(e)
                await s.commit()
    finally:
        shutil.rmtree(work, ignore_errors=True)            # dọn frame/audio tạm


async def mark_orphan_jobs() -> int:
    """Khi server khởi động: job đang dở từ lần chạy trước không thể tiếp tục -> báo lỗi rõ ràng thay vì treo mãi."""
    async with async_session() as s:
        res = await s.execute(
            update(Job).where(Job.status.notin_(_TERMINAL))
            .values(status=JobStatus.FAILED, stage="Bị gián đoạn",
                    error_message="Máy chủ đã khởi động lại trong lúc xử lý. Hãy chạy lại."))
        await s.commit()
        return res.rowcount or 0
