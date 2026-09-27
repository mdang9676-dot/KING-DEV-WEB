"""Chính sách tải link: chống SSRF, nhận mọi trang có trình tải riêng, tải file trực tiếp an toàn (máy chủ HTTP cục bộ thật)."""
import asyncio
import contextlib
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.services import url_import_service as uis
from app.services import url_policy as up

PUB = lambda host, port: ["93.184.216.34"]                    # "công khai"
PRIV = lambda host, port: ["10.0.0.5"]


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e)
    return None


def test_is_public_ip():
    for ok in ("8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"):
        assert up.is_public_ip(ok), ok
    for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1",
                "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "not-an-ip", ""):
        assert not up.is_public_ip(bad), bad


def test_validate_rejects_unsafe_urls():
    bad = ["", "ftp://a.com/x", "javascript:alert(1)", "file:///etc/passwd", "http://", "https://user:pw@a.com/x", "https://a.com:22/x",
           "http://a.com:8080/x", "https://a.com:99999/x", "x" * 3000]
    for u in bad:
        assert raises(up.UnsupportedURLError, up.validate_public_url, u, PUB) is not None, u
    for u in ("http://localhost/x", "http://127.0.0.1/x", "http://[::1]/x", "http://169.254.169.254/latest/meta-data/",
              "http://2130706433/", "http://0x7f.1/", "http://intranet.corp/video.mp4"):          # mọi dạng biến thể trỏ về nội bộ
        loops = lambda h, p: ["127.0.0.1"] if h in ("localhost", "2130706433", "0x7f.1") else (["::1"] if h == "::1" else PRIV(h, p))
        assert raises(up.UnsupportedURLError, up.validate_public_url, u, loops) is not None, u


def test_mixed_public_and_private_addresses_are_rejected():
    mixed = lambda h, p: ["93.184.216.34", "10.0.0.5"]                                             # kiểu DNS rebinding trộn địa chỉ
    assert "nội bộ" in raises(up.UnsupportedURLError, up.validate_public_url, "https://evil.example/x.mp4", mixed)


def test_unresolvable_host_is_unsupported_not_crash():
    def boom(h, p):
        raise OSError("nxdomain")
    assert "phân giải" in raises(up.UnsupportedURLError, up.validate_public_url, "https://nope.example/x", boom)
    assert up.validate_public_url("https://ok.example/x?q=1", PUB).hostname == "ok.example"


class _IE:
    def __init__(self, name, pat):
        self.IE_NAME, self._pat = name, pat

    def suitable(self, url):
        return self._pat in url


def test_usable_extractors_drop_generic_and_unsupported():
    cls = [_IE("TikTok", "tiktok.com"), _IE("generic", ""), _IE("commonmistakes", ""), _IE("unsupported:drm", "netflix.com"),
           _IE("Unsupported:piracy", "x"), _IE("BiliBili", "bilibili.com")]
    names = [c.IE_NAME for c in up.usable_extractors(cls)]
    assert names == ["TikTok", "BiliBili"]


def test_match_extractor_and_broken_extractor_does_not_break_matching():
    class Broken:
        IE_NAME = "Broken"

        def suitable(self, url):
            raise RuntimeError("regex hỏng")

    cls = [Broken(), _IE("TikTok", "tiktok.com")]
    assert up.match_extractor("https://www.tiktok.com/@a/video/1", cls) == "TikTok"
    assert up.match_extractor("https://unknown.example/x", cls) is None


def test_classify_platform_direct_and_rejections():
    cls = [_IE("TikTok", "tiktok.com"), _IE("youtube", "youtube.com")]
    m = lambda u: up.match_extractor(u, cls)
    d = up.classify_url("https://vm.tiktok.com/abc", PUB, m)
    assert (d.kind, d.label) == ("platform", "TikTok")
    assert up.classify_url("https://cdn.example.org/a/b/clip.MP4?token=1", PUB, m).kind == "direct"        # có đuôi media
    assert up.classify_url("https://cdn.example.org/stream/123", PUB, m, prober=lambda u: "video/mp4").kind == "direct"   # không đuôi: HEAD
    msg = raises(up.UnsupportedURLError, up.classify_url, "https://blog.example.org/post/1", PUB, m, lambda u: "text/html")
    assert msg and "Chưa hỗ trợ" in msg
    assert raises(up.UnsupportedURLError, up.classify_url, "http://intranet.corp/clip.mp4", PRIV, m)          # nội bộ bị chặn dù có đuôi .mp4
    assert raises(up.UnsupportedURLError, up.classify_url, "https://www.tiktok.com/@a/video/1", PRIV, m)      # kể cả link "nền tảng"


def test_classify_when_ytdlp_missing_still_allows_direct_links():
    def missing(u):
        raise up.URLImportError("yt-dlp chưa được cài đặt")
    assert up.classify_url("https://a.example/x.mp4", PUB, missing).kind == "direct"
    msg = raises(up.UnsupportedURLError, up.classify_url, "https://a.example/page", PUB, missing, lambda u: "text/html")
    assert "yt-dlp" in msg


def test_safe_connection_blocks_private_ips_and_odd_ports():
    up._SafeHTTPConnection._resolver = staticmethod(PRIV)
    try:
        assert "nội bộ" in raises(up.UnsupportedURLError, up._SafeHTTPConnection("evil.test", 80).connect)
        assert "cổng" in raises(up.UnsupportedURLError, up._SafeHTTPConnection("evil.test", 22).connect)
        assert "cổng" in raises(up.UnsupportedURLError, up._SafeHTTPSConnection("evil.test", 6379).connect)
    finally:
        up._SafeHTTPConnection._resolver = None
    assert up._Redirect.max_redirections == 3


@contextlib.contextmanager
def serve():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _head(self, code, ctype, length=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def do_GET(self):
            try:
                p = self.path
                if p == "/ok.mp4":
                    self._head(200, "video/mp4", 300_000); self.wfile.write(b"\x00" * 300_000)
                elif p == "/noext":
                    self._head(200, "video/webm", 50_000); self.wfile.write(b"\x01" * 50_000)
                elif p == "/page":
                    self._head(200, "text/html", 5); self.wfile.write(b"<html>")
                elif p == "/octet.mp4":
                    self._head(200, "application/octet-stream", 1000); self.wfile.write(b"\x02" * 1000)
                elif p == "/octet":
                    self._head(200, "application/octet-stream", 1000); self.wfile.write(b"\x02" * 1000)
                elif p == "/big":
                    self._head(200, "video/mp4", 5_000_000)
                elif p == "/nolen":
                    self._head(200, "video/mp4")
                    for _ in range(20):
                        self.wfile.write(b"\x03" * 100_000)
                elif p == "/trunc":
                    self._head(200, "video/mp4", 100_000); self.wfile.write(b"\x04" * 40_000)
                elif p == "/empty":
                    self._head(200, "video/mp4", 0)
                elif p == "/forbidden":
                    self._head(403, "text/plain", 0)
                elif p == "/missing":
                    self._head(404, "text/plain", 0)
                else:
                    self._head(500, "text/plain", 0)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


PLAIN = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # bộ mở KHÔNG có chốt an toàn — chỉ dùng để thử máy chủ cục bộ


def test_safe_opener_refuses_local_server_but_plain_one_works():
    with serve() as base:
        assert raises(up.UnsupportedURLError, up.safe_opener().open, base + "/ok.mp4") is not None        # bị chặn (loopback + cổng lạ)
        assert raises(up.UnsupportedURLError, up.download_direct, base + "/ok.mp4", Path(tempfile.mkdtemp()), 10**7) is not None
        assert raises(up.UnsupportedURLError, up.probe_content_type, base + "/ok.mp4") is not None


def test_download_direct_success_progress_and_extension():
    with serve() as base:
        d = Path(tempfile.mkdtemp()); seen = []
        f = up.download_direct(base + "/ok.mp4", d, 10**7, seen.append, opener=PLAIN)
        assert f.name == "video.mp4" and f.stat().st_size == 300_000 and seen and seen[-1] <= 0.99 and seen == sorted(seen)
        g = up.download_direct(base + "/noext", d, 10**7, opener=PLAIN)                                  # đuôi lấy từ Content-Type
        assert g.name == "video.webm" and g.stat().st_size == 50_000
        assert up.download_direct(base + "/octet.mp4", d, 10**7, opener=PLAIN).name == "video.mp4"      # octet-stream cần đuôi media
        assert not list(d.glob(".*.part"))                                                               # không để file dở dang


def test_download_direct_rejections_and_cleanup():
    with serve() as base:
        d = Path(tempfile.mkdtemp())
        cases = {"/page": "không phải file video", "/octet": "không phải file video", "/big": "quá lớn", "/nolen": "vượt quá giới hạn",
                 "/trunc": "ngắt giữa chừng", "/empty": "rỗng", "/forbidden": "đăng nhập", "/missing": "404", "/boom": "HTTP 500"}
        for path, want in cases.items():
            msg = raises(up.URLImportError, up.download_direct, base + path, d, 1_000_000, opener=PLAIN)
            assert msg and want in msg, (path, msg)
        assert not list(d.glob("video*")) and not list(d.glob(".*.part"))                                # thất bại thì không để lại file
        assert isinstance(raises(up.RestrictedContentError, up.download_direct, base + "/forbidden", d, 10**6, opener=PLAIN), str)


def test_import_service_routes_direct_links_and_keeps_old_exports():
    assert uis.UnsupportedURLError is up.UnsupportedURLError and uis.RestrictedContentError is up.RestrictedContentError
    orig_c, orig_d = uis.classify_url, uis.download_direct
    uis.classify_url = lambda url, *a, **k: up.UrlDecision("direct", "Link file trực tiếp")
    called = {}
    uis.download_direct = lambda url, out, mx, prog=None: (called.update(url=url, mx=mx), Path(out) / "video.mp4")[1]
    try:
        m = asyncio.run(uis.fetch_metadata("https://cdn.example.com/a%20b/My%20Clip.mp4?x=1"))
        assert (m.kind, m.title, m.duration_sec, m.provider) == ("direct", "My Clip.mp4", 0.0, "Link file trực tiếp")
        out = asyncio.run(uis.download_video("https://cdn.example.com/c.mp4", Path(tempfile.mkdtemp()), None, None, "direct"))
        assert out.name == "video.mp4" and called["url"].endswith("c.mp4") and called["mx"] > 0
    finally:
        uis.classify_url, uis.download_direct = orig_c, orig_d
