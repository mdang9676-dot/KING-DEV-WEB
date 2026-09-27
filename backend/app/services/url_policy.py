"""
Chính sách tải video từ LINK — hỗ trợ MỌI trang, nhưng không để server bị lợi dụng.

Nhận 2 loại link:
  1. "platform": trang mà yt-dlp có trình tải RIÊNG (hơn 1000 trang: YouTube, TikTok, Facebook, X, Instagram, Bilibili, ...).
     Extractor "generic" bị TẮT có chủ đích (nó sẽ tải bất kỳ URL nào -> nguy cơ SSRF).
  2. "direct": link trỏ thẳng tới file video/âm thanh (.mp4, .webm, .mp3...). Tải bằng bộ tải riêng bên dưới.

Chống SSRF (server bị lừa truy cập mạng nội bộ / metadata đám mây 169.254.169.254 ...):
  - Chỉ http/https, cổng 80/443, không có user:pass trong URL.
  - Tên miền phải phân giải ra TOÀN ĐỊA CHỈ CÔNG KHAI (chặn loopback, private, link-local, CGNAT, multicast, IPv6 nội bộ,
    IPv4 nhúng trong IPv6). Nếu BẤT KỲ địa chỉ nào không công khai -> từ chối (chống DNS rebinding kiểu trộn địa chỉ).
  - Bộ tải trực tiếp kiểm tra lại ở MỖI LẦN KẾT NỐI (kể cả sau redirect) và kết nối thẳng tới IP đã kiểm -> không có khoảng
    hở "kiểm tra xong đổi địa chỉ". Tối đa 3 lần chuyển hướng.
GIỚI HẠN CÒN LẠI (nói thẳng): với nhánh yt-dlp, mạng do chính yt-dlp thực hiện; ta kiểm URL ban đầu nhưng không kiểm được
mọi bước bên trong extractor. Khi triển khai thật nên thêm tường lửa chặn đường ra tới dải mạng nội bộ.
Không hỗ trợ: video riêng tư / cần đăng nhập / DRM (không cố vượt), HLS .m3u8 trực tiếp (ffmpeg theo playlist là vector SSRF).
"""
import http.client
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class URLImportError(Exception):
    pass


class UnsupportedURLError(URLImportError):
    pass


class RestrictedContentError(URLImportError):
    """Video private / cần đăng nhập / có DRM -> từ chối, không cố bypass."""


MEDIA_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".flv", ".ts", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
_EXT_BY_CTYPE = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "video/x-matroska": ".mkv",
                 "video/x-msvideo": ".avi", "video/x-flv": ".flv", "video/mp2t": ".ts", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
                 "audio/aac": ".aac", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac", "audio/ogg": ".ogg",
                 "audio/opus": ".opus"}
_OCTET = ("application/octet-stream", "binary/octet-stream")
_UA = "Mozilla/5.0 (compatible; KingDevTool/1.0)"
MAX_REDIRECTS = 3


# ---------------------------------------------------------------------------
# Kiểm tra địa chỉ / URL
# ---------------------------------------------------------------------------

def is_public_ip(ip: str) -> bool:
    """True nếu là địa chỉ INTERNET CÔNG KHAI. Từ chối private, loopback, link-local, CGNAT, multicast, reserved, IPv4-mapped nội bộ."""
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(addr.is_global) and not addr.is_multicast


def _default_resolver(host: str, port: int | None) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)})


def resolve_public(host: str, port: int | None = None, resolver=None) -> list[str]:
    """Phân giải tên miền; ném UnsupportedURLError nếu KHÔNG phân giải được hoặc có BẤT KỲ địa chỉ nào không công khai."""
    try:
        ips = (resolver or _default_resolver)(host, port)
    except (OSError, UnicodeError):
        raise UnsupportedURLError("Không phân giải được tên miền của link này (sai địa chỉ hoặc mạng lỗi).") from None
    if not ips:
        raise UnsupportedURLError("Không phân giải được tên miền của link này.")
    if any(not is_public_ip(ip) for ip in ips):
        raise UnsupportedURLError("Link trỏ tới địa chỉ mạng nội bộ/không công khai nên bị chặn vì lý do an toàn.")
    return list(ips)


def validate_public_url(url: str, resolver=None) -> urllib.parse.ParseResult:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise UnsupportedURLError("Link không hợp lệ.")
    try:
        p = urllib.parse.urlparse(url.strip())
        port = p.port
    except ValueError:
        raise UnsupportedURLError("Link không hợp lệ.") from None
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsupportedURLError("Chỉ nhận link http/https.")
    if p.username or p.password:
        raise UnsupportedURLError("Link không được chứa tài khoản/mật khẩu.")
    if port not in (None, 80, 443):
        raise UnsupportedURLError("Chỉ nhận link dùng cổng web tiêu chuẩn (80/443).")
    resolve_public(p.hostname, port, resolver)
    return p


# ---------------------------------------------------------------------------
# Nhận diện trang có trình tải riêng (yt-dlp)
# ---------------------------------------------------------------------------

def usable_extractors(classes) -> list:
    """Bỏ extractor 'generic' (tải URL bất kỳ), 'commonmistakes' và nhóm 'unsupported:*' (trang DRM/vi phạm bản quyền đã biết)."""
    out = []
    for c in classes:
        name = str(getattr(c, "IE_NAME", "")).lower()
        if name in ("generic", "commonmistakes") or name.startswith("unsupported"):
            continue
        out.append(c)
    return out


@lru_cache(maxsize=1)
def _extractor_classes():
    from yt_dlp.extractor import gen_extractor_classes      # ImportError nếu chưa cài yt-dlp
    return tuple(usable_extractors(gen_extractor_classes()))


def match_extractor(url: str, classes=None) -> str | None:
    """Tên extractor riêng khớp URL, hoặc None. Ném URLImportError nếu chưa cài yt-dlp."""
    if classes is None:
        try:
            classes = _extractor_classes()
        except ImportError as e:
            raise URLImportError("yt-dlp chưa được cài đặt. Chạy: pip install yt-dlp") from e
    for ie in classes:
        try:
            if ie.suitable(url):
                return str(ie.IE_NAME)
        except Exception:                                    # noqa: BLE001  (một extractor lỗi không được làm hỏng cả bước)
            continue
    return None


# ---------------------------------------------------------------------------
# Bộ mở kết nối AN TOÀN (kiểm tra IP ở MỖI lần kết nối, kể cả sau redirect)
# ---------------------------------------------------------------------------

class _SafeHTTPConnection(http.client.HTTPConnection):
    _resolver = None

    def connect(self):
        if self.port not in (80, 443):
            raise UnsupportedURLError("Chỉ nhận link dùng cổng web tiêu chuẩn (80/443).")
        ip = resolve_public(self.host, self.port, self._resolver)[0]
        self.sock = socket.create_connection((ip, self.port), self.timeout, self.source_address)   # kết nối thẳng tới IP đã kiểm


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    _resolver = None

    def connect(self):
        if self.port not in (80, 443):
            raise UnsupportedURLError("Chỉ nhận link dùng cổng web tiêu chuẩn (80/443).")
        ip = resolve_public(self.host, self.port, self._resolver)[0]
        sock = socket.create_connection((ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)                    # SNI + xác minh chứng chỉ theo tên miền


class _HTTPH(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class _HTTPSH(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPSConnection, req, context=self._context)


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS


def safe_opener() -> urllib.request.OpenerDirector:
    # ProxyHandler({}) tắt proxy từ biến môi trường: nếu không, việc kiểm tra sẽ áp lên proxy chứ không phải trang đích.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _HTTPH, _HTTPSH, _Redirect)


# ---------------------------------------------------------------------------
# Link file trực tiếp
# ---------------------------------------------------------------------------

def url_ext(url: str) -> str:
    return Path(urllib.parse.urlparse(url).path).suffix.lower()


def probe_content_type(url: str, opener=None, timeout: float = 10) -> str | None:
    """HEAD (nếu bị từ chối thì GET rồi bỏ) để biết Content-Type mà không tải file."""
    opener = opener or safe_opener()
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": _UA, "Range": "bytes=0-0"} if method == "GET" else {"User-Agent": _UA})
        try:
            with opener.open(req, timeout=timeout) as r:
                return (r.headers.get_content_type() or "").lower()
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (400, 403, 405, 501):
                continue
            return None
        except UnsupportedURLError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            return None
    return None


@dataclass
class UrlDecision:
    kind: str                 # "platform" | "direct"
    label: str                # tên hiển thị
    extractor: str | None = None


def classify_url(url: str, resolver=None, matcher=None, prober=None) -> UrlDecision:
    """Quyết định cách tải link, hoặc ném UnsupportedURLError (thông điệp tiếng Việt). Đồng bộ (có DNS/HEAD) -> chạy trong thread."""
    from app.core.security import platform_label
    validate_public_url(url, resolver)

    ytdlp_missing = False
    try:
        name = (matcher or match_extractor)(url)
    except URLImportError:
        name, ytdlp_missing = None, True
    if name:
        return UrlDecision("platform", platform_label(url) or name.split(":")[0], name)

    if url_ext(url) in MEDIA_EXT:
        return UrlDecision("direct", "Link file trực tiếp", None)
    ctype = (prober or probe_content_type)(url)
    if ctype and (ctype.startswith(("video/", "audio/"))):
        return UrlDecision("direct", "Link file trực tiếp", None)
    hint = " (máy chủ chưa cài yt-dlp nên chỉ tải được link file trực tiếp)" if ytdlp_missing else ""
    raise UnsupportedURLError(
        "Chưa hỗ trợ link này: không có trình tải riêng cho trang này và nó không phải link file video trực tiếp"
        f" (.mp4, .webm…){hint}. Hãy dùng link video cụ thể hoặc tải file về rồi UPVIDEO.")


def download_direct(url: str, out_dir: Path, max_bytes: int, on_progress=None, opener=None,
                    timeout: float = 20, max_seconds: float = 3600) -> Path:
    """Tải file video/âm thanh từ link trực tiếp về out_dir/video.<đuôi>. Đồng bộ -> chạy trong thread."""
    if opener is None:
        validate_public_url(url)
        opener = safe_opener()
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "video/*,audio/*;q=0.9,*/*;q=0.5"})
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RestrictedContentError("Link này cần đăng nhập/quyền truy cập nên không tải được.") from None
        if e.code == 404:
            raise URLImportError("Không tìm thấy file (lỗi 404).") from None
        raise URLImportError(f"Máy chủ của link trả lỗi HTTP {e.code}.") from None
    except URLImportError:
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise URLImportError(f"Không kết nối được tới link: {e}") from None

    with resp:
        ctype = (resp.headers.get_content_type() or "").lower()
        ext = url_ext(url)
        if not (ctype.startswith(("video/", "audio/")) or (ctype in _OCTET and ext in MEDIA_EXT)):
            raise UnsupportedURLError(f"Link này không phải file video/âm thanh trực tiếp (Content-Type: {ctype or 'không rõ'}).")
        ext = ext if ext in MEDIA_EXT else _EXT_BY_CTYPE.get(ctype, ".mp4")
        length = resp.headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else None
        if total is not None and total > max_bytes:
            raise URLImportError(f"File quá lớn ({total / 1e6:.0f}MB, tối đa {max_bytes / 1e6:.0f}MB).")

        out_dir.mkdir(parents=True, exist_ok=True)
        dest, tmp = out_dir / f"video{ext}", out_dir / f".video{ext}.part"
        got, t0 = 0, time.monotonic()
        try:
            with open(tmp, "wb") as f:
                while chunk := resp.read(256 * 1024):
                    got += len(chunk)
                    if got > max_bytes:
                        raise URLImportError(f"File vượt quá giới hạn {max_bytes / 1e6:.0f}MB, đã dừng tải.")
                    if time.monotonic() - t0 > max_seconds:
                        raise URLImportError("Tải quá lâu, đã dừng.")
                    f.write(chunk)
                    if on_progress and total:
                        on_progress(min(0.99, got / total))
            if got == 0:
                raise URLImportError("File tải về rỗng.")
            if total is not None and got < total:
                raise URLImportError("Tải bị ngắt giữa chừng, hãy thử lại.")
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return dest
