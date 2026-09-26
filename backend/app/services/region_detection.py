"""
Tự động phát hiện vùng phụ đề cứng (hard-sub) trong video.

Ý tưởng (tự cài đặt, không sao chép code của dự án nào):
1. Lấy mẫu N khung hình rải đều khắp video.
2. OCR từng khung hình để lấy vị trí (bounding box) các từ.
3. Gộp các từ cùng hàng thành "dòng chữ" (Line).
4. Gom các dòng theo vị trí dọc (y) qua tất cả frame thành các "dải" (band).
5. Chấm điểm mỗi dải theo đặc điểm của phụ đề thật:
   - xuất hiện ở nhiều frame (nhưng không cần frame nào cũng có, vì có lúc im lặng)
   - NỘI DUNG THAY ĐỔI giữa các frame (phụ đề đổi câu; logo/watermark thì đứng yên)
   - nằm gần giữa theo chiều ngang (phụ đề hay căn giữa; logo hay ở góc)
   - nằm ở nửa dưới khung hình (phổ biến nhất), nhưng vẫn cho phép ở nơi khác
6. Chọn dải điểm cao nhất, gộp thêm dải sát bên (phụ đề 2 dòng), thêm lề, trả về vùng.

Phần logic (find_subtitle_region) là hàm thuần Python, không phụ thuộc OCR/FFmpeg
nên test được độc lập. Không đảm bảo đúng 100%: video có nhiều chữ khác (tiêu đề,
biển hiệu) hoặc phụ đề rất mờ vẫn có thể bị nhận sai — người dùng luôn có thể chỉnh tay.
"""
import asyncio
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    conf: float
    text: str


@dataclass
class Line:
    frame: int
    x1: float
    y1: float
    x2: float
    y2: float
    text: str

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def h(self) -> float:
        return self.y2 - self.y1


@dataclass
class DetectedRegion:
    x: int
    y: int
    width: int
    height: int
    confidence: float          # 0..1
    frames_analyzed: int
    frames_with_text: int
    fallback: bool = False     # True = không tìm thấy, đây là vùng mặc định đoán
    reason: str = ""
    frame_width: int = 0
    frame_height: int = 0
    candidates: list = field(default_factory=list)  # các dải khác (debug/hiển thị)


# ---------------------------------------------------------------------------
# Tiện ích thuần
# ---------------------------------------------------------------------------

def _has_alnum(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _normalize(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _boxes_to_lines(boxes: list[Box], frame_idx: int, min_conf: float) -> list[Line]:
    """Gộp các từ (Box) cùng hàng trong 1 frame thành các dòng (Line)."""
    valid = [b for b in boxes if b.conf >= min_conf and b.w > 0 and b.h > 0 and _has_alnum(b.text)]
    valid.sort(key=lambda b: b.y + b.h / 2)

    groups: list[list[Box]] = []
    cur: list[Box] = []
    for b in valid:
        cy = b.y + b.h / 2
        if cur:
            cur_cy = sum(c.y + c.h / 2 for c in cur) / len(cur)
            cur_h = median(c.h for c in cur)
            if abs(cy - cur_cy) <= 0.6 * cur_h:
                cur.append(b)
                continue
            groups.append(cur)
        cur = [b]
    if cur:
        groups.append(cur)

    lines = []
    for g in groups:
        g.sort(key=lambda b: b.x)
        lines.append(Line(
            frame=frame_idx,
            x1=min(b.x for b in g), y1=min(b.y for b in g),
            x2=max(b.x + b.w for b in g), y2=max(b.y + b.h for b in g),
            text=" ".join(b.text for b in g),
        ))
    return lines


@dataclass
class _Band:
    lines: list[Line]
    score: float = 0.0
    presence: float = 0.0
    distinct_ratio: float = 0.0

    @property
    def cy(self) -> float:
        return sum(l.cy for l in self.lines) / len(self.lines)

    @property
    def med_h(self) -> float:
        return median(l.h for l in self.lines)

    @property
    def frames(self) -> set[int]:
        return {l.frame for l in self.lines}


def _cluster_bands(lines: list[Line], tol: float) -> list[_Band]:
    lines = sorted(lines, key=lambda l: l.cy)
    bands: list[_Band] = []
    cur: list[Line] = []
    for l in lines:
        if cur:
            mean_cy = sum(x.cy for x in cur) / len(cur)
            if abs(l.cy - mean_cy) <= tol:
                cur.append(l)
                continue
            bands.append(_Band(cur))
        cur = [l]
    if cur:
        bands.append(_Band(cur))
    return bands


def _score_band(band: _Band, n_frames: int, fw: int, fh: int) -> None:
    frames = band.frames
    band.presence = len(frames) / max(1, n_frames)

    norm_texts = {_normalize(l.text) for l in band.lines if _normalize(l.text)}
    band.distinct_ratio = min(1.0, len(norm_texts) / max(1, len(frames)))

    # Chữ đứng yên (logo/watermark/banner) -> distinct_ratio thấp -> bị hạ điểm mạnh.
    presence_norm = min(1.0, band.presence / 0.5)  # 50% frame có chữ là "đủ nhiều"
    content_factor = 0.35 + 0.65 * band.distinct_ratio

    mean_cx = sum(l.cx for l in band.lines) / len(band.lines)
    offset_norm = min(1.0, abs(mean_cx - fw / 2) / (fw / 2))
    center_factor = 1.0 - 0.6 * offset_norm

    rel_y = band.cy / fh
    if rel_y >= 0.6:
        pos_factor = 1.0
    elif rel_y >= 0.4:
        pos_factor = 0.85
    else:
        pos_factor = 0.6

    band.score = presence_norm * content_factor * center_factor * pos_factor


def default_bottom_region(fw: int, fh: int, reason: str, frames_analyzed: int = 0) -> DetectedRegion:
    return DetectedRegion(
        x=int(fw * 0.05), y=int(fh * 0.75),
        width=int(fw * 0.90), height=int(fh * 0.22),
        confidence=0.0, frames_analyzed=frames_analyzed, frames_with_text=0,
        fallback=True, reason=reason, frame_width=fw, frame_height=fh,
    )


def find_subtitle_region(
    frames_boxes: list[list[Box]],
    frame_w: int,
    frame_h: int,
    min_conf: float = 40.0,
    min_frames: int = 3,
    min_score: float = 0.12,
) -> DetectedRegion:
    """
    Hàm lõi: nhận danh sách box OCR của từng frame, trả về vùng phụ đề ước lượng.
    Nếu không đủ dữ liệu -> trả vùng mặc định (fallback=True) để UI báo cho người dùng.
    """
    n_frames = len(frames_boxes)
    if n_frames == 0 or frame_w <= 0 or frame_h <= 0:
        return default_bottom_region(max(frame_w, 1), max(frame_h, 1), "Không có frame để phân tích")

    lines: list[Line] = []
    frames_with_text = 0
    for i, boxes in enumerate(frames_boxes):
        ls = _boxes_to_lines(boxes, i, min_conf)
        # Lọc dòng có chiều cao bất thường (quá nhỏ = nhiễu, quá lớn = tiêu đề/poster)
        ls = [l for l in ls if 0.015 * frame_h <= l.h <= 0.15 * frame_h]
        if ls:
            frames_with_text += 1
        lines.extend(ls)

    if not lines:
        return default_bottom_region(frame_w, frame_h, "OCR không thấy chữ nào trong các frame mẫu", n_frames)

    tol = 0.035 * frame_h
    bands = _cluster_bands(lines, tol)
    for b in bands:
        _score_band(b, n_frames, frame_w, frame_h)

    eligible = [b for b in bands if len(b.frames) >= min_frames]
    if not eligible:
        return default_bottom_region(frame_w, frame_h,
                                     f"Không có dải chữ nào xuất hiện ở ít nhất {min_frames} frame", n_frames)

    eligible.sort(key=lambda b: b.score, reverse=True)
    best = eligible[0]
    if best.score < min_score:
        return default_bottom_region(frame_w, frame_h,
                                     "Các dải chữ tìm được đều giống logo/chữ tĩnh, không giống phụ đề", n_frames)

    # Dải tốt nhất mà nội dung gần như không đổi qua các frame => chữ tĩnh (logo/banner/
    # watermark), không phải phụ đề. Thà báo "không chắc" còn hơn chọn nhầm.
    if best.distinct_ratio < 0.25 and len(best.frames) >= 6:
        return default_bottom_region(
            frame_w, frame_h,
            "Chỉ thấy chữ đứng yên (giống logo/banner), không thấy chữ thay đổi như phụ đề",
            n_frames,
        )

    # Phụ đề 2 dòng: gộp thêm dải sát bên có điểm đủ tốt
    chosen = [best]
    for other in eligible[1:]:
        close = abs(other.cy - best.cy) <= 2.2 * best.med_h
        if close and other.score >= 0.35 * best.score:
            chosen.append(other)

    all_lines = [l for b in chosen for l in b.lines]
    x1s = sorted(l.x1 for l in all_lines)
    x2s = sorted(l.x2 for l in all_lines)
    y1s = sorted(l.y1 for l in all_lines)
    y2s = sorted(l.y2 for l in all_lines)

    x1 = _percentile(x1s, 3)
    x2 = _percentile(x2s, 97)
    y1 = _percentile(y1s, 2)
    y2 = _percentile(y2s, 98)

    pad_x = 0.03 * frame_w
    pad_y = max(6.0, 0.6 * best.med_h)
    x1 = max(0, x1 - pad_x)
    x2 = min(frame_w, x2 + pad_x)
    y1 = max(0, y1 - pad_y)
    y2 = min(frame_h, y2 + pad_y)

    width = max(16, int(x2 - x1))
    height = max(16, int(y2 - y1))
    x = int(min(x1, frame_w - width))
    y = int(min(y1, frame_h - height))

    others = [
        {"y_center": round(b.cy), "score": round(b.score, 3), "frames": len(b.frames)}
        for b in eligible[1:4] if b not in chosen
    ]

    return DetectedRegion(
        x=max(0, x), y=max(0, y), width=width, height=height,
        confidence=round(min(1.0, best.score), 3),
        frames_analyzed=n_frames, frames_with_text=frames_with_text,
        fallback=False, reason="", frame_width=frame_w, frame_height=frame_h,
        candidates=others,
    )


# ---------------------------------------------------------------------------
# Phần I/O: lấy mẫu frame + OCR (cần FFmpeg + engine OCR)
# ---------------------------------------------------------------------------

def _png_size(path: Path) -> tuple[int, int]:
    """Đọc kích thước ảnh PNG từ header, không cần Pillow."""
    with open(path, "rb") as f:
        head = f.read(24)
    w, h = struct.unpack(">II", head[16:24])
    return w, h


async def _sample_frames(video_path: Path, out_dir: Path, duration: float, samples: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    n = max(1, samples)
    for i in range(n):
        ts = 0.0 if duration <= 0 else duration * (i + 0.5) / n
        out = out_dir / f"detect_{i:03d}.png"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", str(out),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode == 0 and out.exists():
            paths.append(out)
    if not paths:
        raise RuntimeError("Không lấy được frame nào từ video (FFmpeg lỗi hoặc file hỏng).")
    return paths


async def auto_detect_region(
    video_path: Path,
    work_dir: Path,
    language: str = "vi",
    engine_name: str = "tesseract",
    samples: int = 24,
    orig_width: int | None = None,
    orig_height: int | None = None,
) -> DetectedRegion:
    """Chạy toàn bộ: lấy mẫu frame -> OCR box -> find_subtitle_region -> quy về toạ độ video gốc."""
    from app.services import ocr_service, ffmpeg_service

    meta = await ffmpeg_service.probe(video_path)
    duration = meta.duration_sec
    ow = orig_width or meta.width
    oh = orig_height or meta.height

    frames_dir = work_dir / "detect_frames"
    try:
        frame_paths = await _sample_frames(video_path, frames_dir, duration, samples)
        img_w, img_h = _png_size(frame_paths[0])
        ow = ow or img_w
        oh = oh or img_h

        engine = ocr_service.get_ocr_engine(engine_name)

        def _ocr_all() -> list[list[Box]]:
            result = []
            for p in frame_paths:
                raw = engine.detect_boxes(p, language)
                result.append([Box(int(x), int(y), int(w), int(h), float(c), str(t)) for x, y, w, h, c, t in raw])
            return result

        frames_boxes = await asyncio.to_thread(_ocr_all)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)

    region = find_subtitle_region(frames_boxes, img_w, img_h)

    # Quy đổi từ toạ độ ảnh đã scale về toạ độ video gốc
    sx = ow / img_w
    sy = oh / img_h
    region.x = int(region.x * sx)
    region.y = int(region.y * sy)
    region.width = max(16, int(region.width * sx))
    region.height = max(16, int(region.height * sy))
    region.frame_width = ow
    region.frame_height = oh
    return region
