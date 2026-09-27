from pathlib import Path

from app.core.security import (detect_provider, is_allowed_url, platform_label, safe_join, sanitize_filename,
                               validate_extension)


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    r = sanitize_filename("phụ đề tiếng việt!!.srt")
    assert r.endswith(".srt") and all(ord(c) < 128 for c in r) and " " not in r


def test_safe_join_blocks_traversal(tmp_path=None):
    base = Path("/tmp/uploads_test")
    base.mkdir(exist_ok=True)
    try:
        safe_join(base, "..", "..", "etc", "passwd")
        assert False, "phải chặn path traversal"
    except ValueError:
        pass
    assert str(safe_join(base, "job1", "v.mp4")).startswith(str(base.resolve()))


def test_validate_extension():
    assert validate_extension("a.MP4".lower(), {".mp4"}) and not validate_extension("a.exe", {".mp4"})


def test_allowed_platforms_including_subdomains():
    ok = ["https://www.youtube.com/watch?v=x", "https://youtu.be/x", "https://m.youtube.com/watch?v=x",
          "https://vimeo.com/1", "https://www.tiktok.com/@a/video/1", "https://vm.tiktok.com/abc",
          "https://www.facebook.com/watch?v=1", "https://fb.watch/abc", "https://x.com/a/status/1",
          "https://twitter.com/a/status/1", "https://www.instagram.com/reel/x", "https://www.bilibili.com/video/BV1",
          "https://www.dailymotion.com/video/x", "https://v.redd.it/abc"]
    assert all(is_allowed_url(u) for u in ok), [u for u in ok if not is_allowed_url(u)]


def test_rejects_lookalikes_internal_and_bad_schemes():
    bad = ["https://evilyoutube.com/watch?v=x", "https://youtube.com.evil.com/x", "https://notx.com/a",
           "http://169.254.169.254/latest/meta-data", "http://localhost:8000/api", "http://127.0.0.1/x",
           "ftp://youtube.com/x", "javascript:alert(1)", "file:///etc/passwd", "not a url",
           "https://user:pass@youtube.com/x", "https://some-pirate-site.example/video"]
    assert not any(is_allowed_url(u) for u in bad), [u for u in bad if is_allowed_url(u)]


def test_detect_provider_and_label():
    assert detect_provider("https://youtu.be/x") == "youtube"
    assert detect_provider("https://x.com/a/status/1") == "twitter"
    assert detect_provider("https://example.com/v.mp4") is None
    assert platform_label("https://vm.tiktok.com/x") == "TikTok"
