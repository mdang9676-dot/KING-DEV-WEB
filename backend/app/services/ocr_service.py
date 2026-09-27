"""
Subtitle Extraction (OCR): trích phụ đề "cứng" (đã burn sẵn vào hình) ra SRT.

Pipeline:
Video -> sample N frame/giây trong vùng phụ đề (crop) -> OCR từng frame
-> gộp các frame có text giống nhau liên tiếp thành 1 segment (start=frame đầu,
end=frame cuối trước khi text đổi) -> xuất Segment list dùng lại subtitle_service.

Dùng Tesseract (pytesseract) làm engine mặc định vì nhẹ, cài đơn giản
(apt install tesseract-ocr), không cần GPU. PaddleOCR/EasyOCR có thể cắm thêm
sau như một OCREngine khác cùng interface, cho độ chính xác cao hơn với
tiếng Việt có dấu.
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.services.errors import JobCancelled
from app.services.ocr_text import frames_similar, merge_ocr_frames
from app.services.subtitle_service import Segment


@dataclass
class Region:
    x: int
    y: int
    width: int
    height: int


class OCREngine(ABC):
    name: str = "base"

    @abstractmethod
    def image_to_text(self, image_path: Path, lang: str = "eng") -> str:
        ...

    def detect_boxes(self, image_path: Path, lang: str = "eng") -> list[tuple]:
        """Trả về list (x, y, w, h, conf 0-100, text) cho từng từ/cụm chữ trong ảnh.
        Dùng cho tự động phát hiện vùng phụ đề."""
        raise NotImplementedError(f"Engine {self.name} chưa hỗ trợ detect_boxes")


class TesseractEngine(OCREngine):
    """Engine mặc định, dùng binary tesseract có sẵn trên hệ thống qua pytesseract."""
    name = "tesseract"

    # Map ngôn ngữ ứng dụng -> mã ngôn ngữ traineddata của Tesseract
    LANG_MAP = {"en": "eng", "vi": "vie", "ja": "jpn", "ko": "kor", "zh": "chi_sim"}

    def image_to_text(self, image_path: Path, lang: str = "eng") -> str:
        import pytesseract
        from PIL import Image

        from PIL import ImageOps

        tess_lang = self.LANG_MAP.get(lang, lang)
        img = Image.open(image_path).convert("L")
        img = ImageOps.autocontrast(img)
        if img.height < 90:                       # khung phụ đề nhỏ -> phóng to giúp đọc chính xác hơn
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        text = pytesseract.image_to_string(img, lang=tess_lang, config="--psm 6")
        return text.strip()

    def detect_boxes(self, image_path: Path, lang: str = "eng") -> list[tuple]:
        import pytesseract
        from PIL import Image

        tess_lang = self.LANG_MAP.get(lang, lang)
        data = pytesseract.image_to_data(
            Image.open(image_path), lang=tess_lang, output_type=pytesseract.Output.DICT
        )
        boxes = []
        for i, txt in enumerate(data["text"]):
            try:
                conf = float(data["conf"][i])
            except (ValueError, TypeError):
                conf = -1.0
            if conf < 0 or not str(txt).strip():
                continue
            boxes.append((data["left"][i], data["top"][i], data["width"][i], data["height"][i], conf, str(txt).strip()))
        return boxes


class PaddleOCREngine(OCREngine):
    """Engine thay thế, chính xác hơn cho tiếng Á Đông/tiếng Việt có dấu.
    Cần cài: pip install paddleocr paddlepaddle"""
    name = "paddleocr"

    def __init__(self):
        self._ocr = None

    def _get_ocr(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(use_angle_cls=True, lang="vi", show_log=False)
        return self._ocr

    def image_to_text(self, image_path: Path, lang: str = "vi") -> str:
        ocr = self._get_ocr()
        result = ocr.ocr(str(image_path), cls=True)
        if not result or not result[0]:
            return ""
        lines = [line[1][0] for line in result[0]]
        return " ".join(lines).strip()

    def detect_boxes(self, image_path: Path, lang: str = "vi") -> list[tuple]:
        ocr = self._get_ocr()
        result = ocr.ocr(str(image_path), cls=True)
        if not result or not result[0]:
            return []
        boxes = []
        for poly, (text, conf) in result[0]:
            xs = [pt[0] for pt in poly]
            ys = [pt[1] for pt in poly]
            x, y = min(xs), min(ys)
            boxes.append((x, y, max(xs) - x, max(ys) - y, float(conf) * 100.0, text))
        return boxes


class RapidOCREngine(OCREngine):
    """PP-OCR chạy bằng ONNX Runtime (nhẹ hơn PaddleOCR, chính xác hơn Tesseract với chữ Trung/Nhật/Hàn).
    Cài: pip install rapidocr onnxruntime  (mô hình tự tải ở lần chạy đầu). Hỗ trợ cả API mới (rapidocr) và cũ (rapidocr_onnxruntime)."""
    name = "rapidocr"

    def __init__(self):
        self._eng = None
        self._new = True

    def _get(self):
        if self._eng is None:
            try:
                from rapidocr import RapidOCR
                self._eng, self._new = RapidOCR(), True
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR
                self._eng, self._new = RapidOCR(), False
        return self._eng

    def _run(self, image_path: Path) -> list[tuple]:
        """-> [(x, y, w, h, conf 0-100, text)]"""
        eng, out = self._get(), []
        if self._new:
            r = eng(str(image_path))
            txts, scores, boxes = getattr(r, "txts", None), getattr(r, "scores", None), getattr(r, "boxes", None)
            for t, sc, bx in zip(txts or [], scores if scores is not None else [], boxes if boxes is not None else []):
                out.append((*self._bbox(bx), float(sc) * 100.0, str(t)))
        else:
            res, _ = eng(str(image_path))
            for bx, t, sc in (res or []):
                out.append((*self._bbox(bx), float(sc) * 100.0, str(t)))
        return out

    @staticmethod
    def _bbox(poly) -> tuple:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    def image_to_text(self, image_path: Path, lang: str = "vi") -> str:
        rows = sorted(self._run(image_path), key=lambda r: (round(r[1] / 20), r[0]))   # trên->dưới, trái->phải
        return " ".join(r[5] for r in rows if r[4] >= 50).strip()

    def detect_boxes(self, image_path: Path, lang: str = "vi") -> list[tuple]:
        return self._run(image_path)


def get_ocr_engine(name: str = "tesseract") -> OCREngine:
    engines = {"tesseract": TesseractEngine, "paddleocr": PaddleOCREngine, "rapidocr": RapidOCREngine}
    return engines.get(name, TesseractEngine)()


async def extract_frames(video_path: Path, out_dir: Path, region: Region, fps: float = 2.0) -> list[Path]:
    """Trích N frame/giây, đã crop sẵn vào vùng chứa phụ đề, dùng FFmpeg."""
    import asyncio
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps},crop={region.width}:{region.height}:{region.x}:{region.y}"
    pattern = str(out_dir / "frame_%06d.png")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(video_path), "-vf", vf, pattern,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Trích frame thất bại: {stderr.decode(errors='ignore')[-2000:]}")
    return sorted(out_dir.glob("frame_*.png"))


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


async def extract_subtitle_from_video(
    video_path: Path,
    work_dir: Path,
    region: Region,
    lang: str = "vi",
    fps: float = 2.0,
    engine_name: str = "tesseract",
    min_segment_duration_ms: int = 500,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    skip_similar: bool = True,
) -> list[Segment]:
    """
    Chạy toàn bộ pipeline OCR extraction, trả về danh sách Segment (có timestamp).
    Đây là OCR-based, không đảm bảo chính xác 100% - phụ thuộc chất lượng video,
    font, độ tương phản chữ/nền.
    """
    frames_dir = work_dir / "ocr_frames"
    frames = await extract_frames(video_path, frames_dir, region, fps)
    engine = get_ocr_engine(engine_name)

    frame_interval_ms = int(1000 / fps)
    raw_texts: list[tuple[int, str]] = []          # (timestamp_ms, chữ OCR thô)
    total = max(1, len(frames))
    prev_path, prev_text = None, ""
    for i, frame_path in enumerate(frames):
        if skip_similar and prev_path is not None and frames_similar(prev_path, frame_path):
            text = prev_text                       # khung gần như y hệt khung trước -> dùng lại, khỏi OCR lại
        else:
            # OCR là tác vụ nặng đồng bộ -> chạy trong thread để KHÔNG chặn event loop
            text = await asyncio.to_thread(engine.image_to_text, frame_path, lang)
        raw_texts.append((i * frame_interval_ms, text))
        prev_path, prev_text = frame_path, text
        if i % 10 == 0:
            if is_cancelled and await is_cancelled():
                raise JobCancelled()
            if on_progress:
                await on_progress((i + 1) / total)

    # Gộp mờ (chịu được OCR nhảy chữ) + bỏ phiếu đa số để chọn bản chữ đúng nhất
    return merge_ocr_frames(raw_texts, frame_interval_ms, min_segment_duration_ms)
