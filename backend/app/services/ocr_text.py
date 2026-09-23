"""
Xử lý văn bản OCR (ý tưởng: bộ chuẩn hoá chữ Trung + gộp khối OCR của các app dịch phụ đề trực tiếp).

- normalize_ocr_text: làm sạch chữ OCR (NFKC, bỏ khoảng trắng thừa giữa ký tự CJK, bỏ ký tự rác ở đầu/cuối).
- texts_match: so khớp "mờ" — OCR hay nhảy vài ký tự giữa các khung nên không thể so bằng dấu ==.
- merge_ocr_frames: gộp các khung liên tiếp thành từng câu; chọn bản chữ xuất hiện NHIỀU NHẤT (bỏ phiếu đa số)
  thay vì bản của khung đầu; chịu được 1 khung nhấp nháy/mất chữ giữa chừng.
- detect_script_lang: đoán ngôn ngữ/chữ viết từ ký tự Unicode (không cần mạng, không cần model).
- frames_similar: hai ảnh crop gần như y hệt thì khỏi OCR lại (nhanh hơn nhiều với cảnh tĩnh).
"""
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from app.services.subtitle_service import Segment

_CJK_CLASS = "\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af"
_CJK_GAP = re.compile(rf"(?<=[{_CJK_CLASS}]) +(?=[{_CJK_CLASS}])")
_EDGE_JUNK = re.compile(r"^[\s\-–—_|·•~`^*#\\/]+|[\s\-–—_|·•~`^*#\\/]+$")


def normalize_ocr_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"\s+", " ", t.replace("\n", " ")).strip()
    t = _CJK_GAP.sub("", t)                        # OCR hay chèn dấu cách giữa các chữ Hán
    t = _EDGE_JUNK.sub("", t)
    return t if any(ch.isalnum() for ch in t) else ""


def _key(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def texts_match(a: str, b: str, threshold: float = 0.82) -> bool:
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if min(len(ka), len(kb)) < 4:                  # câu quá ngắn: phải giống hệt mới gộp
        return False
    return SequenceMatcher(None, ka, kb).ratio() >= threshold


def merge_ocr_frames(raw: list[tuple[int, str]], interval_ms: int, min_dur_ms: int = 500,
                     gap_tolerance: int = 1, threshold: float = 0.82) -> list[Segment]:
    """raw = [(timestamp_ms, chữ_OCR_thô)] theo thứ tự thời gian -> danh sách Segment."""
    segments: list[Segment] = []
    cur: dict | None = None

    def close():
        nonlocal cur
        if cur is None:
            return
        end = cur["last"] + interval_ms
        if end - cur["start"] >= min_dur_ms:
            counts = Counter(cur["variants"])
            best = max(counts, key=lambda v: (counts[v], len(v)))   # đa số, hoà thì lấy bản dài hơn
            segments.append(Segment(len(segments), cur["start"], end, best))
        cur = None

    blanks = 0
    for ts, text in raw:
        n = normalize_ocr_text(text)
        if not n:
            blanks += 1
            if cur is not None and blanks > gap_tolerance:
                close()
            continue
        if cur is not None and texts_match(n, cur["ref"], threshold):
            cur["variants"].append(n)
            cur["last"] = ts
        else:
            close()
            cur = {"start": ts, "last": ts, "ref": n, "variants": [n]}
        blanks = 0
    close()
    return segments


# ---------------------------------------------------------------------------
# Nhận diện chữ viết / ngôn ngữ
# ---------------------------------------------------------------------------
_VI_MARKS = set("ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
LANG_FAMILY = {"zh": "han", "ja": "han", "ko": "hangul", "ru": "cyrillic", "th": "thai", "ar": "arabic", "hi": "devanagari"}
LANG_LABEL = {"zh": "tiếng Trung", "ja": "tiếng Nhật", "ko": "tiếng Hàn", "ru": "tiếng Nga", "th": "tiếng Thái",
              "ar": "tiếng Ả Rập", "hi": "tiếng Hindi", "vi": "tiếng Việt", "en": "chữ Latin (Anh/Pháp/Đức…)"}


def script_family(lang: str | None) -> str | None:
    if not lang:
        return None
    return LANG_FAMILY.get(lang.split("-")[0], "latin")


def detect_script_lang(text: str) -> str | None:
    """Trả về mã gần đúng: zh/ja/ko/ru/th/ar/hi/vi/en(=chữ Latin). None nếu quá ít chữ để kết luận."""
    c = Counter()
    for ch in text or "":
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7AF: c["ko"] += 1
        elif 0x3040 <= o <= 0x30FF: c["ja"] += 1
        elif 0x3400 <= o <= 0x9FFF: c["han"] += 1
        elif 0x0400 <= o <= 0x04FF: c["ru"] += 1
        elif 0x0E00 <= o <= 0x0E7F: c["th"] += 1
        elif 0x0600 <= o <= 0x06FF: c["ar"] += 1
        elif 0x0900 <= o <= 0x097F: c["hi"] += 1
        elif ch.isalpha():
            c["latin"] += 1
            if ch in _VI_MARKS: c["vi"] += 1
    total = sum(v for k, v in c.items() if k != "vi")
    if total < 4:
        return None
    if c["ja"] >= 0.1 * total: return "ja"        # có kana => tiếng Nhật (dù có nhiều Hán tự)
    for key in ("ko", "ru", "th", "ar", "hi"):
        if c[key] >= 0.4 * total: return key
    if c["han"] >= 0.4 * total: return "zh"
    if c["latin"] >= 0.4 * total: return "vi" if c["vi"] >= 0.03 * c["latin"] else "en"
    return None


# ---------------------------------------------------------------------------
# Bỏ qua khung y hệt
# ---------------------------------------------------------------------------
def frames_similar(a: Path, b: Path, threshold: float = 1.5) -> bool:
    """True nếu 2 ảnh crop gần như giống hệt (độ lệch trung bình < threshold/255). Cần Pillow; thiếu thì trả False."""
    try:
        from PIL import Image, ImageChops, ImageStat
        with Image.open(a) as ia, Image.open(b) as ib:
            if ia.size != ib.size:
                return False
            sa = ia.convert("L").resize((64, 16))
            sb = ib.convert("L").resize((64, 16))
            return ImageStat.Stat(ImageChops.difference(sa, sb)).mean[0] < threshold
    except Exception:                                    # noqa: BLE001
        return False
