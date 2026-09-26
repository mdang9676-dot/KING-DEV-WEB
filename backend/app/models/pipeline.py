"""
Tuỳ chọn của pipeline "một chạm". Mọi trường đều bị ràng buộc (khoảng giá trị, regex, danh sách cho phép)
vì chúng cuối cùng đi vào lệnh FFmpeg/ASS: không có chuỗi tự do nào được nối thẳng vào lệnh.
Toạ độ vùng là số CHUẨN HOÁ 0..1 theo khung hình gốc.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

HEX32 = r"^[0-9a-f]{32}$"
COLOR = r"^#[0-9a-fA-F]{6}$"
LANG = r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$"


class RectIn(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class CoverIn(BaseModel):
    mode: Literal["none", "blur", "mosaic", "erase", "solid"] = "blur"
    strength: int = Field(default=60, ge=1, le=100)
    color: str = Field(default="#000000", pattern=COLOR)
    opacity: float = Field(default=1.0, ge=0, le=1)


class SubtitleStyleIn(BaseModel):
    font: str = Field(default="Arial", pattern=r"^[A-Za-z0-9 _.()\-]{1,60}$")
    size_pct: float = Field(default=4.5, ge=1.5, le=12)       # % chiều cao video
    color: str = Field(default="#FFFFFF", pattern=COLOR)
    outline_color: str = Field(default="#000000", pattern=COLOR)
    outline_px: int = Field(default=2, ge=0, le=8)
    bold: bool = True
    box: bool = False
    box_color: str = Field(default="#000000", pattern=COLOR)
    box_opacity: float = Field(default=0.6, ge=0, le=1)
    bilingual: bool = False


class SubtitleIn(BaseModel):
    enabled: bool = False
    source: Literal["ocr", "whisper", "job"] = "ocr"        # ocr = đọc chữ cứng trong hình; whisper = nghe giọng nói
    source_job_id: str | None = Field(default=None, pattern=HEX32)
    source_lang: str | None = Field(default=None, pattern=LANG)   # ngôn ngữ chữ/giọng gốc
    target_lang: str = Field(default="vi", pattern=LANG)
    translate: bool = True
    provider: Literal["user_ai", "openai", "gemini", "deepseek", "argos_local"] | None = None
    ocr_engine: Literal["tesseract", "rapidocr", "paddleocr"] = "tesseract"
    ocr_fps: float = Field(default=2.0, ge=0.5, le=5)
    whisper_model: Literal["tiny", "base", "small", "medium", "large-v3"] | None = None   # None = mặc định của máy chủ
    region: RectIn | None = None                # vùng phụ đề CŨ; None = tự phát hiện (khi source=ocr)
    cover: CoverIn = Field(default_factory=CoverIn)   # cách che phụ đề cũ
    glossary: str = Field(default="", max_length=4000)   # từ điển thuật ngữ: mỗi dòng 'gốc = dịch' (áp dụng khi dịch bằng AI của bạn)
    style_genre: Literal["auto", "modern", "ancient", "anime"] = "auto"   # quy tắc xưng hô/giọng văn khi dịch bằng AI
    condense: bool = False                      # rút gọn dòng dịch quá dài (khó đọc) bằng AI
    max_cps: int = Field(default=20, ge=10, le=40)   # ngưỡng ký tự/giây để coi là "quá dài"
    burn: bool = True                           # False = chỉ dịch (lấy SRT / để lồng tiếng), không in vào video
    style: SubtitleStyleIn = Field(default_factory=SubtitleStyleIn)


class LogoRemoveIn(BaseModel):
    enabled: bool = False
    regions: list[RectIn] = Field(default_factory=list, max_length=8)
    cover: CoverIn = Field(default_factory=lambda: CoverIn(mode="erase"))


class LogoOverlayIn(BaseModel):
    enabled: bool = False
    logo_id: str | None = Field(default=None, pattern=HEX32)
    x: float = Field(default=0.05, ge=0, le=1)       # góc trên-trái, chuẩn hoá theo khung ĐẦU RA
    y: float = Field(default=0.05, ge=0, le=1)
    w: float = Field(default=0.15, ge=0.02, le=1)    # bề rộng logo / bề rộng khung
    opacity: float = Field(default=1.0, ge=0, le=1)


class DubIn(BaseModel):
    enabled: bool = False
    provider: Literal["edge_tts", "gemini_tts", "vieneu", "piper", "openai_tts", "demo"] = "edge_tts"
    voice_id: str = Field(default="vi-VN-HoaiMyNeural", pattern=r"^[A-Za-z0-9_.:\-]{1,80}$")   # vieneu: "preset:<slug>" | "clone:<hex32>"
    original_volume: float = Field(default=0.15, ge=0, le=1)
    dubbed_volume: float = Field(default=1.0, ge=0, le=3)
    fit: Literal["natural", "sync"] = "natural"   # natural: mượn khoảng lặng (ít tăng tốc); sync: sát khung hình


class EnhanceIn(BaseModel):
    enabled: bool = False
    target_height: Literal[0, 720, 1080, 1440, 2160] = 0
    sharpen: float = Field(default=0.5, ge=0, le=1)
    denoise: bool = False


class PipelineRequest(BaseModel):
    video_id: str = Field(pattern=HEX32)
    subtitle: SubtitleIn = Field(default_factory=SubtitleIn)
    logo_remove: LogoRemoveIn = Field(default_factory=LogoRemoveIn)
    logo_overlay: LogoOverlayIn = Field(default_factory=LogoOverlayIn)
    dub: DubIn = Field(default_factory=DubIn)
    enhance: EnhanceIn = Field(default_factory=EnhanceIn)

    @model_validator(mode="after")
    def _check(self):
        sub, lr, lo, dub = self.subtitle, self.logo_remove, self.logo_overlay, self.dub
        if not any([sub.enabled, lr.enabled, lo.enabled, dub.enabled, self.enhance.enabled]):
            raise ValueError("Hãy bật ít nhất một thao tác (phụ đề, logo, lồng tiếng hoặc nâng chất lượng).")
        if dub.enabled and not sub.enabled:
            raise ValueError("Lồng tiếng cần bật phần phụ đề/dịch để có văn bản (có thể tắt 'in phụ đề vào video').")
        if lr.enabled and not lr.regions:
            raise ValueError("Đã bật xoá logo nhưng chưa vẽ vùng logo nào.")
        if lo.enabled and not lo.logo_id:
            raise ValueError("Đã bật chèn logo nhưng chưa tải logo lên.")
        if sub.enabled:
            if sub.source == "job" and not sub.source_job_id:
                raise ValueError("Nguồn 'phụ đề đã có' cần source_job_id.")
            if sub.cover.mode != "none" and sub.source != "ocr" and sub.region is None:
                raise ValueError("Muốn che phụ đề cũ với nguồn này, hãy chọn vùng phụ đề.")
        return self
