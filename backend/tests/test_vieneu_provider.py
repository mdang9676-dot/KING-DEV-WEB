"""VieNeuProvider: kiểm thử với engine GIẢ (không cần cài vieneu/numpy) — kiểm tra phần tích hợp của ta, không kiểm tra chất lượng giọng."""
import asyncio
import json
import shutil
import tempfile
import wave
from pathlib import Path

import pytest

from app.services import tts_providers as tp
from app.services import voice_clones as vc

UID = "a" * 32


class FakeEngine:
    def __init__(self):
        self.added, self.calls = [], []

    def list_preset_voices(self):
        return [("Adam", "Adam"), ("Phạm Tuyên", "pham_tuyen_id"), ("Đức Trí", "duc_tri")]

    def add_voice(self, name, path, **kw):
        self.added.append((name, path))

    def infer(self, text, voice=None, ref_audio=None, **kw):
        self.calls.append({"text": text, "voice": voice, "ref_audio": ref_audio})
        return [0.0, 0.5, -0.5, 1.5, -1.5] * 480          # 2400 mẫu (có giá trị vượt ngưỡng để thử cắt)


def _mk_clone(base: Path, cid: str = "b" * 32) -> Path:
    d = vc.user_dir(base, UID)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.wav").write_bytes(b"RIFFfake")
    return d


def _prov(base: Path, eng, mode="v3turbo"):
    return tp.VieNeuProvider(clones_dir=vc.user_dir(base, UID), owner=UID, mode=mode, engine=eng)


def test_slug_makes_ascii_ids():
    assert tp._slug("Phạm Tuyên") == "pham_tuyen"
    assert tp._slug("Đức Trí") == "duc_tri"
    assert tp._slug("!!!") == "voice"


def test_list_voices_returns_preset_ids_that_fit_api_pattern():
    import re
    voices = asyncio.run(_prov(Path(tempfile.mkdtemp()), FakeEngine()).list_voices())
    assert [v["id"] for v in voices] == ["preset:adam", "preset:pham_tuyen", "preset:duc_tri"]
    assert all(re.match(r"^[A-Za-z0-9_.:\-]{1,80}$", v["id"]) for v in voices)     # khớp DubIn.voice_id
    assert not any(v["cloned"] for v in voices)


def test_preset_synthesis_writes_valid_wav_at_engine_rate():
    d = Path(tempfile.mkdtemp())
    eng = FakeEngine()
    out = asyncio.run(_prov(d, eng).synthesize("Xin chào", "preset:pham_tuyen", d / "o.audio"))
    with wave.open(str(out), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 48000)
        assert w.getnframes() == 2400
    assert eng.calls[0]["voice"] == "pham_tuyen_id"        # dùng voice_id do engine trả về, không phải slug


def test_nano_mode_uses_24k():
    d = Path(tempfile.mkdtemp())
    out = asyncio.run(_prov(d, FakeEngine(), mode="v3nano").synthesize("x", "preset:adam", d / "o.wav"))
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 24000


def test_unknown_preset_and_bad_prefix_rejected():
    d = Path(tempfile.mkdtemp())
    p = _prov(d, FakeEngine())
    with pytest.raises(ValueError):
        asyncio.run(p.synthesize("x", "preset:khong_co", d / "o.wav"))
    with pytest.raises(ValueError):
        asyncio.run(p.synthesize("x", "vi-VN-HoaiMyNeural", d / "o.wav"))


def test_clone_voice_is_registered_once_and_reused():
    base = Path(tempfile.mkdtemp())
    _mk_clone(base)
    eng = FakeEngine()
    p = _prov(base, eng)
    for t in ("câu một", "câu hai", "câu ba"):
        asyncio.run(p.synthesize(t, "clone:" + "b" * 32, base / "o.wav"))
    assert len(eng.added) == 1                              # mã hoá mẫu giọng đúng 1 lần
    assert eng.added[0][0] == f"u{UID[:8]}_{'b' * 32}"
    assert {c["voice"] for c in eng.calls} == {eng.added[0][0]}


def test_clone_of_another_user_or_missing_or_traversal_is_rejected():
    base = Path(tempfile.mkdtemp())
    _mk_clone(base)                                          # mẫu thuộc UID
    other = tp.VieNeuProvider(clones_dir=vc.user_dir(base, "c" * 32), owner="c" * 32, engine=FakeEngine())
    with pytest.raises(ValueError):                          # người khác không dùng được mẫu của UID
        asyncio.run(other.synthesize("x", "clone:" + "b" * 32, base / "o.wav"))
    p = _prov(base, FakeEngine())
    for bad in ("clone:" + "d" * 32, "clone:../../etc/passwd", "clone:" + "B" * 32):
        with pytest.raises(ValueError):
            asyncio.run(p.synthesize("x", bad, base / "o.wav"))


def test_engine_without_add_voice_falls_back_to_ref_audio():
    class Old:                                               # engine cũ: không có add_voice
        calls = []

        def list_preset_voices(self):
            return []

        def infer(self, text, voice=None, ref_audio=None, **kw):
            self.calls.append(ref_audio)
            return [0.1] * 100
    base = Path(tempfile.mkdtemp())
    d = _mk_clone(base)
    eng = Old()
    asyncio.run(_prov(base, eng).synthesize("x", "clone:" + "b" * 32, base / "o.wav"))
    assert eng.calls == [str(d / ("b" * 32 + ".wav"))]


def test_factory_knows_vieneu():
    assert tp.get_voice_provider("vieneu").name == "vieneu"
    assert tp.get_voice_provider("vieneu").supports_cloning is True


def test_dub_track_end_to_end_with_48k_voice():
    """Giọng 48 kHz của VieNeu phải đi qua build_dub_track (đổi về 24 kHz chuẩn timeline) và đặt đúng vị trí câu."""
    if not shutil.which("ffmpeg"):
        return
    from dataclasses import dataclass
    from app.services import dubbing_service as ds

    @dataclass
    class Seg:
        start_ms: int
        end_ms: int
        text: str
        translated_text: str | None = None

    class Tone(FakeEngine):
        def infer(self, text, voice=None, ref_audio=None, **kw):     # 1 giây tín hiệu 48 kHz
            return [0.3] * 48000

    base = Path(tempfile.mkdtemp())
    _mk_clone(base)
    segs = [Seg(0, 1500, "a", "Xin chào"), Seg(4000, 5500, "b", "Tạm biệt")]
    out = asyncio.run(ds.build_dub_track(segs, _prov(base, Tone()), "clone:" + "b" * 32, base / "dub", 7000))
    assert abs(ds.wav_duration_ms(out) - 7000) <= 20
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == ds.SAMPLE_RATE


def test_forget_removes_cloned_voice_from_engine_memory():
    eng, removed = FakeEngine(), []
    eng.remove_voice = lambda name: removed.append(name)
    base = Path(tempfile.mkdtemp()); _mk_clone(base)
    asyncio.run(_prov(base, eng).synthesize("Xin chào", "clone:" + "b" * 32, base / "o.wav"))
    name = f"u{UID[:8]}_{'b' * 32}"
    tp._ENGINES[("test",)] = eng
    try:
        assert (id(eng), name) in tp._REGISTERED
        tp.forget_vieneu_clone(UID, "b" * 32)
        assert removed == [name] and (id(eng), name) not in tp._REGISTERED
        tp.forget_vieneu_clone(UID, "b" * 32)                     # gọi lại không lỗi, không gỡ hai lần
        assert removed == [name]
    finally:
        tp._ENGINES.pop(("test",), None)
