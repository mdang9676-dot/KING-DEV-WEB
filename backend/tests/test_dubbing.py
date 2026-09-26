import asyncio
import shutil
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from app.services import dubbing_service as ds


def test_tempo_and_slot():
    assert ds.tempo_factor(1000, 1500) == 1.0
    assert abs(ds.tempo_factor(3000, 1500) - ds.MAX_SPEEDUP) < 1e-9
    assert ds.allowed_slot_ms(3000, 4000, 4500) == 1500
    assert ds.allowed_slot_ms(0, 2000, None) == int(2000 * ds.STRETCH_LIMIT + 300)


def _wav(path: Path, ms: int, sr=24000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x01\x00" * int(sr * ms / 1000))


def test_timeline_places_pieces_at_start_and_never_overlaps():
    d = Path(tempfile.mkdtemp())
    for i, ms in enumerate((1500, 1700, 1000)):
        _wav(d / f"{i}.wav", ms)
    pieces = [ds.DubPiece(0, d / "0.wav"), ds.DubPiece(3000, d / "1.wav"), ds.DubPiece(4500, d / "2.wav")]
    pl = ds.assemble_timeline(pieces, d / "t.wav", 8000)
    assert pl[0][0] == 0 and 2990 <= pl[1][0] <= 3010
    assert pl[2][0] >= pl[1][1]                                  # câu 3 bị đẩy lùi, không chồng lên câu 2
    assert ds.wav_duration_ms(d / "t.wav") >= 7995


def test_build_dub_track_speeds_up_long_sentence():
    if not shutil.which("ffmpeg"):
        return

    @dataclass
    class Seg:
        start_ms: int; end_ms: int; text: str; translated_text: str | None = None

    class Fake:
        async def synthesize(self, text, voice_id, out_path, rate=1.0):
            dur = {"a": 1.5, "b": 3.0, "c": 1.0}[text]
            p = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-f", "wav", str(out_path))
            await p.communicate()

    d = Path(tempfile.mkdtemp())
    segs = [Seg(0, 2000, "x", "a"), Seg(3000, 4000, "y", "b"), Seg(4500, 6000, "z", "c")]
    out = asyncio.run(ds.build_dub_track(segs, Fake(), "v", d, 8000))
    assert ds.wav_duration_ms(out) >= 7995
    assert (d / "seg_0001_fit.wav").exists() and not (d / "seg_0000_fit.wav").exists()


def test_borrow_gap_gives_more_room_but_never_less():
    strict = ds.allowed_slot_ms(3000, 4000, 8000)                       # 1s câu, khoảng lặng dài 5s phía sau
    natural = ds.allowed_slot_ms(3000, 4000, 8000, borrow_gap=True)
    assert strict == 1700 and natural == 3500 and natural > strict       # 3*1000+500, bị chặn bởi trần
    assert ds.allowed_slot_ms(3000, 4000, 4500, borrow_gap=True) == ds.allowed_slot_ms(3000, 4000, 4500)   # hết chỗ: như cũ
    assert ds.allowed_slot_ms(3000, 4000, 4300, borrow_gap=True) >= ds.allowed_slot_ms(3000, 4000, 4300)   # không bao giờ nhỏ hơn
    assert ds.allowed_slot_ms(0, 2000, None, borrow_gap=True, limit_ms=60000) == 6500                     # câu cuối: tới trần
    assert ds.allowed_slot_ms(0, 2000, None, borrow_gap=True, limit_ms=3000) == max(3100, 3000)           # gần hết video: chỉ tới hết video


def test_natural_mode_avoids_speedup_when_gap_available():
    if not shutil.which("ffmpeg"):
        return

    @dataclass
    class Seg:
        start_ms: int; end_ms: int; text: str; translated_text: str | None = None

    class Fake:
        async def synthesize(self, text, voice_id, out_path, rate=1.0):
            p = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3.0", "-f", "wav", str(out_path))
            await p.communicate()

    segs = [Seg(0, 1000, "x", "a"), Seg(9000, 10000, "y", "b")]            # câu 3s trong khung 1s, còn 8s im lặng phía sau
    d1, d2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    asyncio.run(ds.build_dub_track(segs, Fake(), "v", d1, 12000, borrow_gap=False))
    asyncio.run(ds.build_dub_track(segs, Fake(), "v", d2, 12000, borrow_gap=True))
    assert (d1 / "seg_0000_fit.wav").exists()                               # chế độ sát: bị tăng tốc
    assert not (d2 / "seg_0000_fit.wav").exists()                           # chế độ tự nhiên: giữ nguyên tốc độ đọc
    assert ds.wav_duration_ms(d2 / "seg_0000.wav") >= 2990
