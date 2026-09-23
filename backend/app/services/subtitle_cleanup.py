"""Lọc rác OCR còn sót sau bước gộp khung (merge_ocr_frames): chữ lẻ chớp nhoáng, toàn ký hiệu, lặp một ký tự."""
import re
import unicodedata

from app.services.subtitle_service import Segment

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def is_junk(text: str, duration_ms: int) -> bool:
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()
    if not t:
        return True
    letters = len(_LETTER.findall(t))
    digits = sum(c.isdigit() for c in t)
    if letters == 0 and digits < 3:                               # chỉ ký hiệu / 1-2 chữ số lẻ
        return True
    if letters == 1 and digits == 0 and duration_ms < 1000:       # 1 chữ lẻ (CJK hoặc Latin) chớp nhoáng
        return True
    if len(t) >= 4 and (letters + digits) / len(t) < 0.4:         # phần lớn là dấu gạch/chấm/ký hiệu
        return True
    if not _CJK.search(t) and len(t) >= 6 and len(set(t.replace(" ", ""))) <= 2:   # "||||||" "------"
        return True
    return False


def filter_junk(segments: list[Segment]) -> list[Segment]:
    kept = [s for s in segments if not is_junk(s.text, s.end_ms - s.start_ms)]
    for i, s in enumerate(kept):
        s.index = i
    return kept
