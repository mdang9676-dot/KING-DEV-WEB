"""
Lồng tiếng KHỚP THỜI GIAN.

Khác cách nối tuần tự cũ (dễ lệch dần): mỗi câu được đặt đúng thời điểm start của nó trên
timeline. Nếu giọng đọc dài hơn khoảng cho phép -> tăng tốc bằng atempo (tối đa MAX_SPEEDUP);
nếu vẫn dài hơn thì để tràn nhẹ nhưng KHÔNG chồng lên câu sau (con trỏ timeline chỉ tiến lên).

Timeline được ghép bằng module `wave` thuần Python (không tốn thêm lệnh FFmpeg cho từng khoảng lặng).
"""
import asyncio
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.services.errors import JobCancelled, gather_or_cancel

SAMPLE_RATE = 24000
MAX_SPEEDUP = 1.8          # >1.8x nghe rất khó chịu
STRETCH_LIMIT = 1.4        # cho phép câu dài tới 140% thời lượng gốc (+300ms) nếu còn chỗ trống
SYNTH_CONCURRENCY = 4


@dataclass
class DubPiece:
    start_ms: int
    wav_path: Path
    duration_ms: int = 0


BORROW_FACTOR = 3.0        # chế độ "tự nhiên": tối đa 3 lần thời lượng gốc (+500ms) kể cả khi khoảng lặng rất dài
GAP_MARGIN_MS = 120        # chừa một chút im lặng trước câu kế tiếp


def allowed_slot_ms(start_ms: int, end_ms: int, next_start_ms: int | None,
                    borrow_gap: bool = False, limit_ms: int | None = None) -> int:
    """
    Khoảng thời gian tối đa câu này được phép chiếm.
    - Mặc định (sát khung hình): chỉ giãn nhẹ (STRETCH_LIMIT), câu dài hơn sẽ bị tăng tốc.
    - borrow_gap=True (tự nhiên): được "mượn" khoảng lặng phía sau tới sát câu kế tiếp (hoặc hết video),
      nên ít phải tăng tốc hơn. Không bao giờ nhỏ hơn chế độ mặc định.
    """
    own = max(300, end_ms - start_ms)
    stretch = int(own * STRETCH_LIMIT + 300)
    strict = stretch if next_start_ms is None else min(max(300, next_start_ms - start_ms), stretch)
    if not borrow_gap:
        return strict
    cap = int(own * BORROW_FACTOR + 500)
    room = (next_start_ms - start_ms - GAP_MARGIN_MS) if next_start_ms is not None else ((limit_ms - start_ms) if limit_ms else cap)
    return max(strict, min(max(room, 300), cap))


def tempo_factor(actual_ms: int, allowed_ms: int, max_speedup: float = MAX_SPEEDUP) -> float:
    """Hệ số tăng tốc cần thiết (>=1.0; không bao giờ làm chậm)."""
    if allowed_ms <= 0 or actual_ms <= allowed_ms:
        return 1.0
    return min(max_speedup, actual_ms / allowed_ms)


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return int(w.getnframes() * 1000 / w.getframerate())


def assemble_timeline(pieces: list[DubPiece], out_path: Path, total_ms: int, sample_rate: int = SAMPLE_RATE) -> list[tuple[int, int]]:
    """
    Ghép các đoạn WAV (mono s16 24kHz) lên timeline đúng vị trí start_ms, chèn im lặng ở khoảng trống,
    đệm im lặng tới total_ms. Trả về [(start_thực, end_thực)] tính bằng ms của từng đoạn.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    placements: list[tuple[int, int]] = []
    cursor = 0  # đơn vị: sample
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)

        def write_silence(n: int):
            chunk = b"\x00\x00" * min(n, sample_rate)
            left = n
            while left > 0:
                take = min(left, sample_rate)
                out.writeframes(chunk[: take * 2] if take < sample_rate else chunk)
                left -= take

        for p in sorted(pieces, key=lambda x: x.start_ms):
            want = int(p.start_ms * sample_rate / 1000)
            if want > cursor:
                write_silence(want - cursor)
                cursor = want
            with wave.open(str(p.wav_path), "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, sample_rate):
                    raise ValueError(f"WAV sai định dạng (cần mono/16-bit/{sample_rate}Hz): {p.wav_path.name}")
                frames = w.readframes(w.getnframes())
                n = w.getnframes()
            placements.append((int(cursor * 1000 / sample_rate), int((cursor + n) * 1000 / sample_rate)))
            out.writeframes(frames)
            cursor += n

        total_samples = int(total_ms * sample_rate / 1000)
        if total_samples > cursor:
            write_silence(total_samples - cursor)
    return placements


async def _ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-v", "error", "-nostdin", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-1500:])


async def to_pcm_wav(src: Path, dst: Path, tempo: float = 1.0) -> Path:
    """Chuyển audio bất kỳ -> WAV mono s16 24kHz (chuẩn timeline), có thể tăng tốc bằng atempo."""
    args = ["-i", str(src)]
    if tempo > 1.001:
        args += ["-af", f"atempo={tempo:.4f}"]
    args += ["-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    await _ffmpeg(args)
    return dst


async def build_dub_track(
    segments: list,                    # có .start_ms .end_ms .text .translated_text
    provider,                          # VoiceProvider
    voice_id: str,
    work_dir: Path,
    total_ms: int,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    text_fn: Callable[[str], str] | None = None,       # chuẩn hoá văn bản trước khi đọc (vd số -> chữ)
    borrow_gap: bool = False,                          # True: mượn khoảng lặng phía sau để đỡ phải tăng tốc
) -> Path:
    """Tổng hợp giọng cho từng câu (song song có giới hạn) rồi ghép lên timeline. Trả về đường dẫn WAV."""
    work_dir.mkdir(parents=True, exist_ok=True)
    segs = sorted(segments, key=lambda s: s.start_ms)
    sem = asyncio.Semaphore(SYNTH_CONCURRENCY)
    done = 0
    pieces: list[DubPiece] = []

    async def one(i: int, seg) -> DubPiece | None:
        nonlocal done
        text = (getattr(seg, "translated_text", None) or seg.text or "").strip()
        if text and text_fn:
            text = text_fn(text).strip()
        if not text:
            return None
        async with sem:
            if is_cancelled and await is_cancelled():
                raise JobCancelled()
            raw = work_dir / f"tts_{i:04d}.audio"
            last_err = None
            for attempt in range(3):               # TTS mạng có thể lỗi thoáng qua
                try:
                    await provider.synthesize(text, voice_id, raw)
                    last_err = None
                    break
                except Exception as e:             # noqa: BLE001
                    last_err = e
                    await asyncio.sleep(0.8 * (attempt + 1))
            if last_err:
                raise RuntimeError(f"TTS lỗi ở câu {i + 1}: {last_err}")
            wav = work_dir / f"seg_{i:04d}.wav"
            await to_pcm_wav(raw, wav)
            nxt = segs[i + 1].start_ms if i + 1 < len(segs) else None
            slot = allowed_slot_ms(seg.start_ms, seg.end_ms, nxt, borrow_gap, total_ms)
            dur = wav_duration_ms(wav)
            f = tempo_factor(dur, slot)
            if f > 1.001:
                fit = work_dir / f"seg_{i:04d}_fit.wav"
                await to_pcm_wav(wav, fit, tempo=f)
                wav = fit
                dur = wav_duration_ms(wav)
        done += 1
        if on_progress:
            await on_progress(done / max(1, len(segs)))
        return DubPiece(seg.start_ms, wav, dur)

    results = await gather_or_cancel(*(one(i, s) for i, s in enumerate(segs)))
    pieces = [p for p in results if p]
    if not pieces:
        raise ValueError("Không có câu nào có văn bản để lồng tiếng")
    out = work_dir / "dub_track.wav"
    assemble_timeline(pieces, out, total_ms)
    return out
