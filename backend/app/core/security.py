"""
Các hàm bảo mật dùng chung:
- sanitize_filename: loại bỏ ký tự nguy hiểm khỏi tên file người dùng upload.
- safe_join: ngăn path traversal khi ghép đường dẫn từ input người dùng.
- validate_extension: kiểm tra đuôi file nằm trong whitelist.
- ALLOWLIST domain cho URL import (không cho phép domain tùy ý -> tránh SSRF /
  tránh trở thành công cụ tải nội dung tùy ý không kiểm soát).
"""
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Tên hiển thị đẹp cho các nền tảng phổ biến. KHÔNG còn là cổng chặn: việc chấp nhận link do services/url_policy.py quyết định
# (mọi trang yt-dlp có trình tải riêng + link file trực tiếp, kèm chống SSRF). Bản quyền là trách nhiệm của người dùng.
PLATFORMS: dict[str, tuple[str, str]] = {   # domain -> (slug, tên hiển thị)
    "youtube.com": ("youtube", "YouTube"), "youtu.be": ("youtube", "YouTube"),
    "vimeo.com": ("vimeo", "Vimeo"),
    "facebook.com": ("facebook", "Facebook"), "fb.watch": ("facebook", "Facebook"),
    "tiktok.com": ("tiktok", "TikTok"), "douyin.com": ("douyin", "Douyin"),
    "twitter.com": ("twitter", "X (Twitter)"), "x.com": ("twitter", "X (Twitter)"),
    "instagram.com": ("instagram", "Instagram"),
    "dailymotion.com": ("dailymotion", "Dailymotion"),
    "bilibili.com": ("bilibili", "Bilibili"), "b23.tv": ("bilibili", "Bilibili"),
    "reddit.com": ("reddit", "Reddit"), "v.redd.it": ("reddit", "Reddit"),
    "twitch.tv": ("twitch", "Twitch"), "streamable.com": ("streamable", "Streamable"),
    "ok.ru": ("okru", "OK.ru"), "vk.com": ("vk", "VK"), "kuaishou.com": ("kuaishou", "Kuaishou"),
}
ALLOWED_URL_HOSTS = set(PLATFORMS)


def _platform(url: str) -> tuple[str, str] | None:
    try:
        p = urlparse(url)
    except Exception:
        return None
    if len(url) > 2048 or p.scheme not in ("http", "https") or p.username or p.password:
        return None
    host = (p.hostname or "").lower().rstrip(".")
    for dom, info in PLATFORMS.items():
        if host == dom or host.endswith("." + dom):
            return info
    return None


def is_allowed_url(url: str) -> bool:
    """URL phải thuộc nền tảng trong allowlist (kể cả subdomain như m.youtube.com, vm.tiktok.com)."""
    return _platform(url) is not None


def detect_provider(url: str) -> str | None:
    info = _platform(url)
    return info[0] if info else None


def platform_label(url: str) -> str | None:
    info = _platform(url)
    return info[1] if info else None


def sanitize_filename(filename: str) -> str:
    """Chuẩn hoá tên file: bỏ dấu, bỏ đường dẫn, bỏ ký tự đặc biệt."""
    filename = Path(filename).name  # bỏ mọi phần thư mục (chống ../../etc/passwd)
    filename = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    stem = Path(filename).stem or "file"
    suffix = Path(filename).suffix.lower()
    stem = _FILENAME_SAFE_RE.sub("_", stem)[:100]
    return f"{stem}{suffix}"


def safe_join(base_dir: Path, *parts: str) -> Path:
    """Ghép path an toàn, đảm bảo kết quả luôn nằm trong base_dir."""
    base_dir = base_dir.resolve()
    candidate = base_dir.joinpath(*parts).resolve()
    if base_dir not in candidate.parents and candidate != base_dir:
        raise ValueError("Path traversal bị chặn")
    return candidate


def validate_extension(filename: str, allowed: set[str]) -> bool:
    return Path(filename).suffix.lower() in allowed
