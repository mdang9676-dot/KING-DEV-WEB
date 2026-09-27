"""
Sinh và parse file phụ đề: SRT, VTT, ASS, TXT.
Không phụ thuộc thư viện ngoài phức tạp - tự viết format nhỏ gọn, dễ kiểm thử.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Segment:
    index: int
    start_ms: int
    end_ms: int
    text: str
    translated_text: str | None = None


def _ms_to_srt_time(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ms_to_vtt_time(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_srt(segments: list[Segment], bilingual: bool = False) -> str:
    lines = []
    for seg in segments:
        text = seg.text
        if bilingual and seg.translated_text:
            text = f"{seg.translated_text}\n{seg.text}"
        elif seg.translated_text:
            text = seg.translated_text
        lines.append(
            f"{seg.index + 1}\n"
            f"{_ms_to_srt_time(seg.start_ms)} --> {_ms_to_srt_time(seg.end_ms)}\n"
            f"{text}\n"
        )
    return "\n".join(lines)


def to_vtt(segments: list[Segment], bilingual: bool = False) -> str:
    out = ["WEBVTT", ""]
    for seg in segments:
        text = seg.text
        if bilingual and seg.translated_text:
            text = f"{seg.translated_text}\n{seg.text}"
        elif seg.translated_text:
            text = seg.translated_text
        out.append(f"{_ms_to_vtt_time(seg.start_ms)} --> {_ms_to_vtt_time(seg.end_ms)}")
        out.append(text)
        out.append("")
    return "\n".join(out)


def to_ass(segments: list[Segment], font_name: str = "Arial", font_size: int = 24) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginV
Style: Default,{font_name},{font_size},&H00FFFFFF,&H00000000,&H80000000,0,2,1,2,30

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ms_to_ass(ms: int) -> str:
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, cs = divmod(rem, 1000)
        return f"{h:d}:{m:02d}:{s:02d}.{cs // 10:02d}"

    lines = [header]
    for seg in segments:
        text = ass_escape(seg.translated_text or seg.text)
        lines.append(f"Dialogue: 0,{ms_to_ass(seg.start_ms)},{ms_to_ass(seg.end_ms)},Default,,0,0,0,,{text}")
    return "\n".join(lines)


def to_txt(segments: list[Segment]) -> str:
    return "\n".join(seg.translated_text or seg.text for seg in segments)


def parse_srt(content: str) -> list[Segment]:
    """Parse nội dung SRT thành list Segment (dùng khi user upload sẵn phụ đề)."""
    blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
    segments = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        idx = int(lines[0].strip()) - 1
        start_str, end_str = [x.strip() for x in lines[1].split("-->")]
        text = "\n".join(lines[2:])

        def parse_time(t: str) -> int:
            t = t.replace(",", ".")
            h, m, s = t.split(":")
            sec, ms = s.split(".")
            return int(h) * 3_600_000 + int(m) * 60_000 + int(sec) * 1000 + int(ms)

        segments.append(Segment(index=idx, start_ms=parse_time(start_str), end_ms=parse_time(end_str), text=text))
    return segments


def shift_timing(segments: list[Segment], offset_ms: int) -> list[Segment]:
    """Dịch toàn bộ timing của các segment (dùng cho tính năng sync lại phụ đề)."""
    return [
        Segment(
            index=s.index,
            start_ms=max(0, s.start_ms + offset_ms),
            end_ms=max(0, s.end_ms + offset_ms),
            text=s.text,
            translated_text=s.translated_text,
        )
        for s in segments
    ]


def merge_segments(seg_a: Segment, seg_b: Segment) -> Segment:
    return Segment(
        index=seg_a.index,
        start_ms=seg_a.start_ms,
        end_ms=seg_b.end_ms,
        text=f"{seg_a.text} {seg_b.text}".strip(),
        translated_text=(
            f"{seg_a.translated_text or ''} {seg_b.translated_text or ''}".strip() or None
        ),
    )


def split_segment(seg: Segment, split_ratio: float = 0.5) -> tuple[Segment, Segment]:
    """Tách 1 segment thành 2, chia thời gian theo tỉ lệ split_ratio (0-1)."""
    mid_ms = int(seg.start_ms + (seg.end_ms - seg.start_ms) * split_ratio)
    words = seg.text.split()
    mid_word = max(1, int(len(words) * split_ratio))
    first = Segment(seg.index, seg.start_ms, mid_ms, " ".join(words[:mid_word]))
    second = Segment(seg.index + 1, mid_ms, seg.end_ms, " ".join(words[mid_word:]))
    return first, second


def save_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# ASS có kiểu chữ theo pixel (dùng cho pipeline: đặt phụ đề mới đúng vào vùng
# phụ đề cũ đã che). PlayRes = độ phân giải video ĐẦU RA nên cỡ chữ/toạ độ chính xác.
# ---------------------------------------------------------------------------
import re as _re

_HEX_COLOR_RE = _re.compile(r"^#([0-9a-fA-F]{6})$")
_FONT_RE = _re.compile(r"^[A-Za-z0-9 _.()\-]{1,60}$")


def ass_escape(text: str) -> str:
    """Làm sạch text đưa vào ASS: chặn override tag {\\...}, xuống dòng chuẩn ASS."""
    t = text.replace("\r", "").replace("{", "(").replace("}", ")").replace("\\", "/")
    return t.replace("\n", "\\N")


def hex_to_ass_color(color: str, alpha: int = 0, default: str = "#FFFFFF") -> str:
    """'#RRGGBB' -> '&HAABBGGRR' (ASS dùng thứ tự BGR, alpha 0=đục 255=trong suốt)."""
    m = _HEX_COLOR_RE.match(color or "") or _HEX_COLOR_RE.match(default)
    hexv = m.group(1)
    r, g, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
    a = max(0, min(255, int(alpha)))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _ms_to_ass_time(ms: int) -> str:
    h, rem = divmod(max(0, ms), 3_600_000)
    m, rem = divmod(rem, 60_000)
    sec, msr = divmod(rem, 1000)
    return f"{h:d}:{m:02d}:{sec:02d}.{msr // 10:02d}"


def to_ass_styled(
    segments: list[Segment],
    play_w: int,
    play_h: int,
    font: str = "Arial",
    font_px: int = 36,
    color: str = "#FFFFFF",
    outline_color: str = "#000000",
    outline_px: int = 2,
    bold: bool = True,
    box: bool = False,
    box_color: str = "#000000",
    box_opacity: float = 0.6,
    region: tuple[int, int, int, int] | None = None,
    bilingual: bool = False,
) -> str:
    """
    Sinh ASS với kích thước PlayRes = video đầu ra.
    region=(x, y, w, h) theo pixel VIDEO ĐẦU RA: chữ được căn giữa ngang trong vùng và
    đặt sát đáy vùng (dùng khi che phụ đề cũ rồi đặt phụ đề mới vào đúng chỗ đó).
    """
    font = font if _FONT_RE.match(font or "") else "Arial"
    font_px = max(8, int(font_px))

    if region:
        rx, ry, rw, rh = region
        margin_l = max(0, int(rx))
        margin_r = max(0, int(play_w - (rx + rw)))
        margin_v = max(0, int(play_h - (ry + rh)) + int(rh * 0.08))
    else:
        margin_l = margin_r = int(play_w * 0.05)
        margin_v = int(play_h * 0.05)

    primary = hex_to_ass_color(color)
    outline = hex_to_ass_color(outline_color, default="#000000")
    box_alpha = int(255 * (1 - max(0.0, min(1.0, box_opacity))))
    back = hex_to_ass_color(box_color, alpha=box_alpha, default="#000000")
    border_style = 3 if box else 1
    outline_col = back if box else outline
    outline_w = max(0, int(outline_px)) if not box else max(2, int(font_px * 0.25))

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {int(play_w)}\nPlayResY: {int(play_h)}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_px},{primary},{primary},{outline_col},{back},"
        f"{-1 if bold else 0},0,0,0,100,100,0,0,{border_style},{outline_w},0,2,{margin_l},{margin_r},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    small = max(8, int(font_px * 0.7))
    lines = [header]
    for seg in segments:
        main = seg.translated_text or seg.text
        text = ass_escape(main)
        if bilingual and seg.translated_text and seg.text:
            text = f"{text}\\N{{\\fs{small}}}{ass_escape(seg.text)}"
        end_ms = max(seg.end_ms, seg.start_ms + 300)
        lines.append(
            f"Dialogue: 0,{_ms_to_ass_time(seg.start_ms)},{_ms_to_ass_time(end_ms)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"
