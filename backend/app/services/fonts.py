"""
Danh sách kiểu chữ cho phụ đề: font hệ thống hỗ trợ tiếng Việt (qua fontconfig) + font BẠN tự thêm vào thư mục
data/fonts (.ttf/.otf/.ttc). Tên họ font đọc trực tiếp từ bảng 'name' của file (không cần thư viện ngoài).
Lưu ý bản quyền: chỉ thêm font bạn có quyền dùng (nên chọn font mã nguồn mở OFL, vd Be Vietnam Pro, Roboto, Lora...).
"""
import shutil
import struct
import subprocess
from pathlib import Path

FONT_EXT = {".ttf", ".otf", ".ttc"}


def read_font_family(path: Path) -> str | None:
    """Đọc tên họ font (nameID 16, không có thì 1) từ file TrueType/OpenType/TTC."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            if head == b"ttcf":                           # bộ sưu tập: lấy font đầu tiên
                f.seek(12)
                f.seek(struct.unpack(">I", f.read(4))[0])
                head = f.read(4)
            else:
                f.seek(0)
                head = f.read(4)
            if head not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
                return None
            num = struct.unpack(">H", f.read(2))[0]
            f.read(6)
            name_off = None
            for _ in range(min(num, 64)):
                tag, _cs, off, _ln = struct.unpack(">4sIII", f.read(16))
                if tag == b"name":
                    name_off = off
                    break
            if name_off is None:
                return None
            f.seek(name_off)
            _fmt, count, str_off = struct.unpack(">HHH", f.read(6))
            recs = [struct.unpack(">HHHHHH", f.read(12)) for _ in range(min(count, 400))]
            best = None
            for plat, _enc, lang, nid, ln, off in recs:
                if nid not in (16, 1) or plat not in (1, 3):
                    continue
                score = (2 if nid == 16 else 1) * 10 + (5 if plat == 3 else 0) + (3 if lang in (0, 0x409) else 0)
                if best is None or score > best[0]:
                    best = (score, plat, ln, off)
            if not best:
                return None
            _, plat, ln, off = best
            f.seek(name_off + str_off + off)
            raw = f.read(ln)
            return (raw.decode("utf-16-be") if plat == 3 else raw.decode("mac_roman")).strip() or None
    except (OSError, struct.error, UnicodeDecodeError):
        return None


def custom_families(fonts_dir: Path) -> list[str]:
    if not fonts_dir.is_dir():
        return []
    out = {read_font_family(p) for p in fonts_dir.iterdir() if p.suffix.lower() in FONT_EXT}
    return sorted(x for x in out if x)


def system_families(vietnamese_only: bool = True) -> list[str]:
    """Họ font cài trên máy chủ (fontconfig). Mặc định chỉ lấy font hỗ trợ tiếng Việt."""
    if not shutil.which("fc-list"):
        return []
    args = ["fc-list", ":lang=vi" if vietnamese_only else ":", "family"]
    try:
        raw = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    fams = set()
    for line in raw.splitlines():
        fams.add(line.split(",")[0].strip())          # "Noto Sans,Noto Sans Regular" -> "Noto Sans"
    return sorted(f for f in fams if f)


def list_fonts(fonts_dir: Path, limit: int = 300) -> list[dict]:
    custom = custom_families(fonts_dir)
    sysf = [f for f in system_families() if f not in custom]
    return ([{"family": f, "custom": True} for f in custom] + [{"family": f, "custom": False} for f in sysf])[:limit]
