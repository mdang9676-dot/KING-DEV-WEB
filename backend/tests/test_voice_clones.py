"""Lưu/kiểm tra mẫu giọng nhân bản: bắt buộc consent, kiểm nội dung thật bằng ffprobe, cô lập theo người dùng."""
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services import voice_clones as vc

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="cần ffmpeg")
UID = "a" * 32


def _audio(path: Path, seconds: float, silent: bool = False) -> Path:
    spec = f"anullsrc=r=24000:cl=mono:d={seconds}" if silent else f"sine=frequency=300:duration={seconds}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", spec, str(path)], check=True)
    return path


def _dir():
    return vc.user_dir(Path(tempfile.mkdtemp()), UID)


def test_consent_is_mandatory_even_if_route_forgets():
    d = _dir(); src = _audio(Path(tempfile.mkdtemp()) / "a.wav", 4)
    for bad in (False, None, "true", 1):
        with pytest.raises(vc.CloneError, match="xác nhận"):
            asyncio.run(vc.create_clone(d, src, "Tôi", bad))
    assert vc.list_clones(d) == []


def test_create_list_delete_roundtrip_and_normalizes_audio():
    d = _dir(); src = _audio(Path(tempfile.mkdtemp()) / "a.mp3", 20)
    c = asyncio.run(vc.create_clone(d, src, "  Giọng   của tôi \n", True))
    assert c["name"] == "Giọng của tôi" and 2.5 <= c["duration_sec"] <= vc.KEEP_SEC + 0.2      # 20s bị cắt còn ≤ 12s
    wav = vc.clone_path(d, c["id"])
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate", "-of", "csv=p=0", str(wav)],
                          capture_output=True, text=True).stdout.strip()
    assert info == f"{vc.SAMPLE_RATE},1"
    assert [x["id"] for x in vc.list_clones(d)] == [c["id"]]
    assert '"consent_at"' in (d / f"{c['id']}.json").read_text(encoding="utf-8")            # có lưu bằng chứng xác nhận
    assert vc.delete_clone(d, c["id"]) is True and vc.list_clones(d) == []
    assert vc.delete_clone(d, c["id"]) is False


def test_rejects_too_short_silent_and_non_audio():
    d = _dir(); tmp = Path(tempfile.mkdtemp())
    with pytest.raises(vc.CloneError, match="quá ngắn"):
        asyncio.run(vc.create_clone(d, _audio(tmp / "s.wav", 1), "x", True))
    with pytest.raises(vc.CloneError):
        asyncio.run(vc.create_clone(d, _audio(tmp / "q.wav", 6, silent=True), "x", True))    # toàn im lặng
    txt = tmp / "fake.wav"; txt.write_text("đây không phải âm thanh")
    with pytest.raises(vc.CloneError):
        asyncio.run(vc.create_clone(d, txt, "x", True))
    assert vc.list_clones(d) == [] and not list(d.glob(".*part*"))                           # không để rác dở dang


def test_limit_per_user():
    d = _dir(); src = _audio(Path(tempfile.mkdtemp()) / "a.wav", 4)
    asyncio.run(vc.create_clone(d, src, "1", True, max_clones=1))
    with pytest.raises(vc.CloneError, match="giới hạn"):
        asyncio.run(vc.create_clone(d, src, "2", True, max_clones=1))


def test_ids_and_names_are_validated():
    d = _dir()
    for bad in ("../x", "A" * 32, "abc", "", "a" * 31):
        with pytest.raises(vc.CloneError):
            vc.clone_path(d, bad)
        assert vc.delete_clone(d, bad) is False
    with pytest.raises(vc.CloneError):
        vc.user_dir(Path("/tmp"), "../../etc")
    with pytest.raises(vc.CloneError):
        vc.sanitize_name("   \x00\x07 ")
    assert vc.sanitize_name("x" * 100) == "x" * vc.NAME_MAX


def test_delete_removes_preview_and_audit_is_stored_but_never_listed():
    import json
    d = _dir(); src = _audio(Path(tempfile.mkdtemp()) / "a.wav", 4)
    c = asyncio.run(vc.create_clone(d, src, "Tôi", True, audit={"consent_version": "v9", "consent_ip_hash": "abc123"}))
    meta = json.loads((d / f"{c['id']}.json").read_text(encoding="utf-8"))
    assert meta["consent_version"] == "v9" and meta["consent_ip_hash"] == "abc123" and meta["consent_at"]
    assert set(vc.list_clones(d)[0]) == {"id", "name", "created_at", "duration_sec"}          # không lộ dấu vết đồng ý ra API
    prev = d / f"{c['id']}.preview.wav"; prev.write_bytes(b"RIFFpreview")
    assert len(vc.list_clones(d)) == 1                                                        # bản nghe thử không bị coi là một giọng
    assert vc.delete_clone(d, c["id"]) is True
    assert not prev.exists() and not list(d.glob(f"{c['id']}*"))                              # xoá giọng = xoá sạch mọi dữ liệu dẫn xuất
