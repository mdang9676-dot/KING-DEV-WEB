"""
Import video từ LINK (mọi trang): yt-dlp cho các trang có trình tải riêng + bộ tải file trực tiếp.

Chính sách an toàn và cách phân loại link nằm trong services/url_policy.py (đọc phần đầu file đó trước khi sửa).
- KHÔNG cố truy cập video private, DRM hay bypass paywall; KHÔNG cấu hình cookie/đăng nhập.
- KHÔNG dùng shell. yt-dlp gọi qua Python API; extractor "generic" bị tắt.
- Giới hạn thời lượng + dung lượng để tránh lạm dụng tài nguyên.
"""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.services.url_policy import (   # noqa: F401  (re-export: router/test import các tên này từ đây)
    RestrictedContentError, URLImportError, UnsupportedURLError, UrlDecision, classify_url, download_direct,
)


@dataclass
class VideoMetadata:
    title: str
    duration_sec: float
    thumbnail: str | None
    provider: str
    available_formats: list[dict]
    kind: str = "platform"          # platform (yt-dlp) | direct (file trực tiếp)


MAX_DURATION_SEC = 4 * 3600  # 4 giờ - giới hạn hợp lý để tránh lạm dụng tài nguyên


def _get_ydl_opts(extra: dict | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "allowed_extractors": ["default", "-generic"],   # không cho extractor "generic" tải URL tùy ý
        # KHÔNG set cookiefile / cookiesfrombrowser ở đây: không cố truy cập
        # nội dung riêng tư hay vượt qua yêu cầu đăng nhập.
    }
    if extra:
        opts.update(extra)
    return opts


async def fetch_metadata(url: str) -> VideoMetadata:
    """Kiểm tra link + lấy thông tin (tiêu đề, thời lượng, ảnh bìa) mà KHÔNG tải video. Ném UnsupportedURLError nếu không hỗ trợ."""
    decision = await asyncio.to_thread(classify_url, url)        # có phân giải DNS/HEAD nên chạy trong thread
    if decision.kind == "direct":
        from pathlib import PurePosixPath
        from urllib.parse import unquote, urlparse
        name = PurePosixPath(unquote(urlparse(url).path)).name or "Video"
        return VideoMetadata(title=name[:120], duration_sec=0.0, thumbnail=None, provider=decision.label,
                             available_formats=[], kind="direct")
    provider = decision.label

    try:
        import yt_dlp
    except ImportError as e:
        raise URLImportError("yt-dlp chưa được cài đặt. Chạy: pip install yt-dlp") from e

    def _extract():
        with yt_dlp.YoutubeDL(_get_ydl_opts({"skip_download": True})) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(_extract)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if "private" in msg or "sign in" in msg or "login" in msg or "drm" in msg:
            raise RestrictedContentError(
                "Video này là private/cần đăng nhập hoặc có bảo vệ DRM. "
                "Hệ thống không hỗ trợ tải nội dung bị hạn chế truy cập."
            ) from e
        raise URLImportError(f"Không thể lấy thông tin video: {e}") from e

    duration = float(info.get("duration") or 0)
    if duration > MAX_DURATION_SEC:
        raise URLImportError(f"Video vượt quá giới hạn thời lượng cho phép ({MAX_DURATION_SEC/3600:.0f} giờ).")

    formats = [
        {
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "resolution": f.get("resolution") or f"{f.get('height', '?')}p",
            "filesize_approx": f.get("filesize") or f.get("filesize_approx"),
        }
        for f in info.get("formats", [])
        if f.get("vcodec") != "none"
    ]

    return VideoMetadata(
        title=info.get("title", "Untitled"),
        duration_sec=duration,
        thumbnail=info.get("thumbnail"),
        provider=provider,
        available_formats=formats,
        kind="platform",
    )


async def download_video(url: str, out_dir: Path, format_id: str | None = None, on_progress=None, kind: str | None = None) -> Path:
    """Tải video đã qua kiểm tra chính sách vào thư mục job riêng. kind: 'platform' | 'direct' (None = tự phân loại lại)."""
    from app.core.config import settings
    if kind is None:
        kind = (await asyncio.to_thread(classify_url, url)).kind
    if kind == "direct":
        return await asyncio.to_thread(download_direct, url, out_dir, settings.MAX_UPLOAD_MB * 1024 * 1024, on_progress)
    await asyncio.to_thread(classify_url, url)                   # luôn kiểm lại (DNS có thể đã đổi từ lúc kiểm tra ban đầu)

    try:
        import yt_dlp
    except ImportError as e:
        raise URLImportError("yt-dlp chưa được cài đặt.") from e

    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "%(id)s.%(ext)s")

    fmt = format_id or "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b"
    finished = 0

    def hook(d):
        """Chạy trong thread tải: quy đổi tiến độ (video + audio là 2 luồng) về 0..0.99."""
        nonlocal finished
        if not on_progress:
            return
        if d.get("status") == "finished":
            finished += 1
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        frac = (d.get("downloaded_bytes", 0) / total) if (d.get("status") == "downloading" and total) else 0.0
        streams = 2 if "+" in fmt else 1
        on_progress(min(0.99, (min(finished, streams - 1) + frac) / streams if streams > 1 else max(frac, 0.0)))

    opts = _get_ydl_opts({
        "format": fmt,
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "max_filesize": settings.MAX_UPLOAD_MB * 1024 * 1024,   # bảo vệ ổ đĩa
    })

    def _download():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        filename = await asyncio.to_thread(_download)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if "private" in msg or "sign in" in msg or "drm" in msg:
            raise RestrictedContentError("Nội dung bị hạn chế truy cập, không thể tải.") from e
        raise URLImportError(f"Tải video thất bại: {e}") from e

    result_path = Path(filename)
    if result_path.suffix != ".mp4":
        # merge_output_format=mp4 thường đảm bảo điều này, nhưng double-check
        mp4_path = result_path.with_suffix(".mp4")
        if mp4_path.exists():
            result_path = mp4_path
    return result_path
