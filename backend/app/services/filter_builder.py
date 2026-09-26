"""
Dựng lệnh FFmpeg cho pipeline "một chạm": che phụ đề/logo cũ -> nâng nét ->
đặt phụ đề mới -> chèn logo của người dùng -> trộn tiếng lồng.

Toàn bộ trong MỘT lần encode (không encode nhiều lượt) để giữ chất lượng và tiết kiệm thời gian.

Module này là hàm thuần (không I/O, không DB, không pydantic) nên test được độc lập.
Mọi giá trị đưa vào chuỗi filter đều là số đã ép kiểu / màu đã validate bằng regex,
tuyệt đối không nối chuỗi từ input người dùng chưa kiểm tra vào lệnh FFmpeg.

Thứ tự lọc (cố ý):
  che vùng (toạ độ gốc) -> khử nhiễu -> scale lên -> làm nét -> phụ đề ASS (vẽ ở độ phân giải đầu ra
  nên chữ sắc nét) -> chèn logo.
"""
import re
from dataclasses import dataclass, field

_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")

COVER_MODES = ("blur", "mosaic", "erase", "solid")


class RenderSpecError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Kiểu dữ liệu đầu vào
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    """Hình chữ nhật CHUẨN HOÁ 0..1 theo khung hình."""
    x: float
    y: float
    w: float
    h: float


@dataclass
class CoverSpec:
    mode: str = "blur"        # blur | mosaic | erase | solid
    strength: int = 60        # 1..100 (độ mờ / cỡ ô mosaic)
    color: str = "#000000"    # chỉ dùng cho solid
    opacity: float = 1.0      # chỉ dùng cho solid


@dataclass
class EnhanceSpec:
    target_height: int = 0    # 0 = giữ nguyên độ phân giải; chỉ phóng to, không thu nhỏ
    sharpen: float = 0.5      # 0..1
    denoise: bool = False


@dataclass
class LogoSpec:
    path: str
    src_w: int                # kích thước ảnh logo gốc (pixel)
    src_h: int
    x: float                  # góc trên-trái, chuẩn hoá theo khung ĐẦU RA
    y: float
    w: float                  # bề rộng logo = w * bề rộng đầu ra
    opacity: float = 1.0


@dataclass
class RenderSpec:
    src_w: int
    src_h: int
    has_audio: bool
    covers: list = field(default_factory=list)   # list[(Rect, CoverSpec)] toạ độ chuẩn hoá theo video GỐC
    enhance: EnhanceSpec | None = None
    ass_path: str | None = None
    fonts_dir: str | None = None      # thư mục font tự thêm (libass dùng để tìm font theo tên)
    logo: LogoSpec | None = None
    dub_audio_path: str | None = None
    original_volume: float = 0.15
    dubbed_volume: float = 1.0
    crf: int = 18
    preset: str = "medium"


@dataclass
class RenderPlan:
    cmd: list
    out_w: int
    out_h: int
    reencodes_video: bool
    filter_complex: str


# ---------------------------------------------------------------------------
# Tiện ích số học
# ---------------------------------------------------------------------------

def _even(n: float, minimum: int = 2) -> int:
    v = int(round(n))
    v -= v % 2
    return max(minimum, v)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def rect_to_px(r: Rect, w: int, h: int, min_size: int = 16) -> tuple[int, int, int, int]:
    """Đổi Rect chuẩn hoá -> pixel (x, y, rw, rh), số chẵn, nằm gọn trong khung, tối thiểu min_size."""
    x = _clamp(r.x, 0.0, 1.0)
    y = _clamp(r.y, 0.0, 1.0)
    rw = _clamp(r.w, 0.0, 1.0 - x)
    rh = _clamp(r.h, 0.0, 1.0 - y)
    px, py = int(x * w), int(y * h)
    pw, ph = int(rw * w), int(rh * h)
    pw = max(min_size, pw)
    ph = max(min_size, ph)
    pw = min(pw, w - px if w - px >= min_size else w)
    ph = min(ph, h - py if h - py >= min_size else h)
    px = min(px, max(0, w - pw))
    py = min(py, max(0, h - ph))
    px -= px % 2
    py -= py % 2
    pw -= pw % 2
    ph -= ph % 2
    return px, py, max(2, pw), max(2, ph)


def output_size(src_w: int, src_h: int, enhance: EnhanceSpec | None) -> tuple[int, int]:
    """Kích thước đầu ra. Chỉ phóng to (không thu nhỏ); luôn chẵn."""
    if enhance and enhance.target_height and enhance.target_height > src_h:
        oh = _even(enhance.target_height)
        ow = _even(src_w * oh / src_h)
        return ow, oh
    return _even(src_w), _even(src_h)


def _color_to_ffmpeg(color: str, opacity: float) -> str:
    m = _COLOR_RE.match(color or "")
    if not m:
        raise RenderSpecError(f"Màu không hợp lệ: {color!r} (cần dạng #RRGGBB)")
    return f"0x{m.group(1).upper()}@{_clamp(opacity, 0.0, 1.0):.2f}"


def escape_filter_path(path: str) -> str:
    """Escape đường dẫn dùng trong filter ass='...' của FFmpeg."""
    s = str(path).replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'")
    return s


# ---------------------------------------------------------------------------
# Các kiểu che
# ---------------------------------------------------------------------------

def cover_chain(cur: str, nxt: str, px: tuple[int, int, int, int], spec: CoverSpec,
                frame_w: int, frame_h: int, uid: str) -> str:
    x, y, w, h = px
    strength = int(_clamp(spec.strength, 1, 100))

    if spec.mode == "blur":
        max_r = max(1, min(w, h) // 2 - 1)
        r = int(_clamp(4 + strength * 0.5, 1, max_r))
        cr = int(_clamp(r // 2, 1, max(1, min(w, h) // 4 - 1)))
        return (
            f"[{cur}]split[{uid}a][{uid}b];"
            f"[{uid}b]crop={w}:{h}:{x}:{y},boxblur=luma_radius={r}:luma_power=3:"
            f"chroma_radius={cr}:chroma_power=3[{uid}c];"
            f"[{uid}a][{uid}c]overlay={x}:{y}[{nxt}]"
        )

    if spec.mode == "mosaic":
        block = int(_clamp(6 + strength * 0.5, 4, 64))
        pw, ph = max(2, w // block), max(2, h // block)
        return (
            f"[{cur}]split[{uid}a][{uid}b];"
            f"[{uid}b]crop={w}:{h}:{x}:{y},scale={pw}:{ph}:flags=bilinear,scale={w}:{h}:flags=neighbor[{uid}c];"
            f"[{uid}a][{uid}c]overlay={x}:{y}[{nxt}]"
        )

    if spec.mode == "erase":
        # delogo nội suy từ viền quanh vùng; yêu cầu vùng KHÔNG chạm mép khung hình
        ex = max(1, x)
        ey = max(1, y)
        ew = min(w, frame_w - ex - 1)
        eh = min(h, frame_h - ey - 1)
        if ew < 4 or eh < 4:
            raise RenderSpecError("Vùng xoá quá sát mép khung hình, hãy chọn vùng nằm trong khung.")
        return f"[{cur}]delogo=x={ex}:y={ey}:w={ew}:h={eh}[{nxt}]"

    if spec.mode == "solid":
        col = _color_to_ffmpeg(spec.color, spec.opacity)
        return f"[{cur}]drawbox=x={x}:y={y}:w={w}:h={h}:color={col}:t=fill[{nxt}]"

    raise RenderSpecError(f"Kiểu che không hỗ trợ: {spec.mode!r} (chọn {COVER_MODES})")


# ---------------------------------------------------------------------------
# Dựng toàn bộ lệnh
# ---------------------------------------------------------------------------

def build_render_plan(spec: RenderSpec, video_path: str, out_path: str) -> RenderPlan:
    if spec.src_w <= 0 or spec.src_h <= 0:
        raise RenderSpecError("Không đọc được kích thước video gốc")
    if spec.dub_audio_path is None and spec.original_volume < 0:
        raise RenderSpecError("Âm lượng không hợp lệ")

    out_w, out_h = output_size(spec.src_w, spec.src_h, spec.enhance)
    chains: list[str] = []
    cur = "0:v"
    counter = 0

    def new_label() -> str:
        nonlocal counter
        counter += 1
        return f"v{counter}"

    # 1) che các vùng (toạ độ video gốc)
    for i, (rect, cover) in enumerate(spec.covers):
        px = rect_to_px(rect, spec.src_w, spec.src_h)
        nxt = new_label()
        chains.append(cover_chain(cur, nxt, px, cover, spec.src_w, spec.src_h, uid=f"c{i}"))
        cur = nxt

    # 2) khử nhiễu -> scale -> làm nét
    enh = spec.enhance
    if enh:
        parts = []
        if enh.denoise:
            parts.append("hqdn3d=2:1.5:3:3")
        if (out_w, out_h) != (_even(spec.src_w), _even(spec.src_h)):
            parts.append(f"scale={out_w}:{out_h}:flags=lanczos")
        sharpen = _clamp(enh.sharpen, 0.0, 1.0)
        if sharpen > 0:
            amount = 0.3 + sharpen * 1.2          # 0.3 .. 1.5 (luma)
            parts.append(f"unsharp=5:5:{amount:.2f}:5:5:0.0")
        if parts:
            nxt = new_label()
            chains.append(f"[{cur}]{','.join(parts)}[{nxt}]")
            cur = nxt

    # 3) phụ đề ASS (PlayRes = độ phân giải đầu ra)
    if spec.ass_path:
        nxt = new_label()
        fdir = f":fontsdir='{escape_filter_path(spec.fonts_dir)}'" if spec.fonts_dir else ""
        chains.append(f"[{cur}]ass='{escape_filter_path(spec.ass_path)}'{fdir}[{nxt}]")
        cur = nxt

    # Chỉ số input: 0 = video, sau đó lần lượt audio lồng, logo
    next_input = 1
    dub_idx = None
    logo_idx = None
    if spec.dub_audio_path:
        dub_idx = next_input
        next_input += 1
    if spec.logo:
        logo_idx = next_input
        next_input += 1

    # 4) logo của người dùng
    if spec.logo:
        lg = spec.logo
        if lg.src_w <= 0 or lg.src_h <= 0:
            raise RenderSpecError("Không đọc được kích thước logo")
        lw = min(_even(out_w * _clamp(lg.w, 0.02, 1.0)), out_w)
        lh = max(2, int(lw * lg.src_h / lg.src_w))
        lh = min(lh - lh % 2 if lh > 2 else 2, out_h)
        ox = int(_clamp(lg.x, 0.0, 1.0) * out_w)
        oy = int(_clamp(lg.y, 0.0, 1.0) * out_h)
        ox = max(0, min(ox, out_w - lw))
        oy = max(0, min(oy, out_h - lh))
        opacity = _clamp(lg.opacity, 0.0, 1.0)
        logo_filter = f"[{logo_idx}:v]scale={lw}:{lh},format=rgba"
        if opacity < 0.999:
            logo_filter += f",colorchannelmixer=aa={opacity:.3f}"
        chains.append(f"{logo_filter}[lg]")
        nxt = new_label()
        chains.append(f"[{cur}][lg]overlay={ox}:{oy}:format=auto[{nxt}]")
        cur = nxt

    # 5) âm thanh
    audio_label = None
    if dub_idx is not None:
        dv = _clamp(spec.dubbed_volume, 0.0, 4.0)
        ov = _clamp(spec.original_volume, 0.0, 4.0)
        if spec.has_audio and ov > 0:
            chains.append(
                f"[0:a]volume={ov:.3f}[ao];[{dub_idx}:a]volume={dv:.3f}[ad];"
                f"[ao][ad]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )
        else:
            chains.append(f"[{dub_idx}:a]volume={dv:.3f}[aout]")
        audio_label = "aout"

    reencode = cur != "0:v"
    filter_complex = ";".join(chains)

    cmd = ["ffmpeg", "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", str(video_path)]
    if spec.dub_audio_path:
        cmd += ["-i", str(spec.dub_audio_path)]
    if spec.logo:
        cmd += ["-i", str(spec.logo.path)]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex]

    cmd += ["-map", f"[{cur}]" if reencode else "0:v:0"]
    cmd += ["-map", f"[{audio_label}]" if audio_label else "0:a:0?"]

    if reencode:
        cmd += ["-c:v", "libx264", "-crf", str(int(_clamp(spec.crf, 10, 30))),
                "-preset", spec.preset if re.fullmatch(r"[a-z]{4,9}", spec.preset or "") else "medium",
                "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path)]

    return RenderPlan(cmd=cmd, out_w=out_w, out_h=out_h, reencodes_video=reencode, filter_complex=filter_complex)
