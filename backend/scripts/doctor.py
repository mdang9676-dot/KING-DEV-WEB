#!/usr/bin/env python3
"""
Kiểm tra môi trường TRƯỚC khi chạy lần đầu:   python scripts/doctor.py [--net]
In ra từng mục ✓ (ổn) / ⚠ (nên xem) / ✗ (phải sửa) kèm cách khắc phục. Thoát mã 1 nếu có mục ✗.
Không cần cài sẵn các gói của dự án (chỉ dùng thư viện chuẩn) nên chạy được cả khi môi trường còn thiếu.
"""
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
OK, WARN, FAIL = "✓", "⚠", "✗"
FFMPEG_FILTERS = ["ass", "delogo", "boxblur", "unsharp", "hqdn3d", "overlay", "colorchannelmixer", "drawbox", "amix", "split", "crop", "scale"]


def parse_ffmpeg_version(text: str) -> tuple[int, int] | None:
    m = re.search(r"ffmpeg version n?(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_env(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def run(cmd: list[str], timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def check(env: dict[str, str], net: bool = False) -> list[tuple[str, str, str]]:
    res: list[tuple[str, str, str]] = []
    add = lambda lvl, title, hint="": res.append((lvl, title, hint))

    v = sys.version_info
    add(OK if v >= (3, 11) else FAIL, f"Python {v.major}.{v.minor}", "" if v >= (3, 11) else "Cần Python 3.11 trở lên.")

    for mod, pkg in [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("sqlalchemy", "sqlalchemy"), ("aiosqlite", "aiosqlite"),
                     ("pydantic_settings", "pydantic-settings"), ("cryptography", "cryptography"), ("multipart", "python-multipart"),
                     ("httpx", "httpx")]:
        add(OK if has(mod) else FAIL, f"gói bắt buộc: {pkg}", "" if has(mod) else "Chạy: pip install -r requirements.txt")

    ff = run(["ffmpeg", "-version"])
    ver = parse_ffmpeg_version(ff)
    if not shutil.which("ffmpeg") or not ff:
        add(FAIL, "FFmpeg", "Chưa cài. Ubuntu: sudo apt install ffmpeg | macOS: brew install ffmpeg | Windows: choco install ffmpeg")
    else:
        add(OK if ver and ver >= (4, 4) else FAIL, f"FFmpeg {'.'.join(map(str, ver)) if ver else '(không đọc được phiên bản)'}",
            "" if ver and ver >= (4, 4) else "Cần FFmpeg >= 4.4 (dùng amix normalize=0).")
        add(OK if shutil.which("ffprobe") else FAIL, "ffprobe", "" if shutil.which("ffprobe") else "Đi kèm FFmpeg; cài lại gói FFmpeg đầy đủ.")
        flt = run(["ffmpeg", "-hide_banner", "-filters"])
        missing = [f for f in FFMPEG_FILTERS if not re.search(rf"^ [A-Z.]+ +{f} +", flt, re.M)]
        add(OK if not missing else FAIL, "bộ lọc FFmpeg cần dùng", "" if not missing else "Thiếu: " + ", ".join(missing) + " (dùng bản FFmpeg đầy đủ, có libass).")
        enc = run(["ffmpeg", "-hide_banner", "-encoders"])
        miss_e = [e for e in ("libx264", "aac") if not re.search(rf" {e} ", enc)]
        add(OK if not miss_e else FAIL, "bộ mã hoá libx264 + aac", "" if not miss_e else "Thiếu: " + ", ".join(miss_e))
        add(OK if "normalize" in run(["ffmpeg", "-hide_banner", "-h", "filter=amix"]) else WARN, "amix có tuỳ chọn normalize",
            "Nếu thiếu, trộn tiếng lồng có thể lỗi: nâng cấp FFmpeg.")

    fc = run(["fc-list", ":lang=vi", "family"]) if shutil.which("fc-list") else ""
    n_fonts = len({l.split(",")[0].strip() for l in fc.splitlines() if l.strip()})
    add(OK if n_fonts else WARN, f"font hỗ trợ tiếng Việt: {n_fonts}", "" if n_fonts else "Cài font: sudo apt install fonts-noto-core fonts-dejavu-core (hoặc bỏ .ttf vào data/fonts).")

    sec = os.environ.get("APP_SECRET") or env.get("APP_SECRET", "")
    add(OK if len(sec) >= 32 else WARN, "APP_SECRET", "" if len(sec) >= 32 else
        "Chưa đặt/ngắn. Server sẽ tự sinh vào data/.app_secret (chỉ nên dùng thử). Tạo: python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
    add(OK if env.get("DEBUG", "false").lower() != "true" else WARN, "DEBUG", "" if env.get("DEBUG", "false").lower() != "true" else "DEBUG=true mở /docs công khai — tắt khi triển khai thật.")
    try:
        d = BACKEND / "data"
        d.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d):
            pass
        free = shutil.disk_usage(d).free / 1e9
        add(OK if free >= 5 else WARN, f"thư mục data ghi được, còn {free:.0f} GB", "" if free >= 5 else "Ít dung lượng: video xuất ra rất nặng.")
    except OSError as e:
        add(FAIL, "thư mục data", f"Không ghi được: {e}")

    # tính năng tuỳ chọn: chỉ báo trạng thái, không coi là lỗi
    opt = [("faster_whisper", "Whisper (nghe giọng nói)", "pip install faster-whisper"), ("edge_tts", "Edge TTS (giọng miễn phí)", "pip install edge-tts"),
           ("yt_dlp", "yt-dlp (lấy video từ link)", "pip install yt-dlp"), ("PIL", "Pillow (OCR)", "pip install Pillow"),
           ("pytesseract", "pytesseract (OCR)", "pip install pytesseract")]
    for mod, name, cmd in opt:
        add(OK if has(mod) else WARN, f"tuỳ chọn: {name}", "" if has(mod) else cmd)
    if has("pytesseract") or shutil.which("tesseract"):
        langs = run(["tesseract", "--list-langs"]) if shutil.which("tesseract") else ""
        add(OK if "vie" in langs else WARN, "Tesseract + gói tiếng Việt (vie)", "" if "vie" in langs else "Cài: sudo apt install tesseract-ocr tesseract-ocr-vie")
    add(OK if has("vieneu") else WARN, "tuỳ chọn: VieNeu-TTS (nhân bản giọng)", "" if has("vieneu") else "pip install -r requirements-voice.txt (chỉ khi cần nhân bản giọng)")

    if net:
        for host in ("huggingface.co", "generativelanguage.googleapis.com", "api.openai.com"):
            try:
                socket.create_connection((host, 443), 4).close()
                add(OK, f"kết nối được {host}")
            except OSError:
                add(WARN, f"không kết nối được {host}", "Cần mạng để tải model/gọi AI lần đầu.")
    return res


def main() -> int:
    envp = BACKEND / ".env"
    env = parse_env(envp.read_text(encoding="utf-8")) if envp.exists() else {}
    if not envp.exists():
        print(f"{WARN} chưa có backend/.env (sao chép từ .env.example)\n")
    res = check(env, net="--net" in sys.argv)
    for lvl, title, hint in res:
        print(f" {lvl} {title}" + (f"\n     → {hint}" if hint and lvl != OK else ""))
    nf, nw = sum(l == FAIL for l, _, _ in res), sum(l == WARN for l, _, _ in res)
    print(f"\n{'SẴN SÀNG' if not nf else 'CẦN SỬA'}: {nf} lỗi, {nw} cảnh báo.")
    return 1 if nf else 0


if __name__ == "__main__":
    sys.exit(main())
