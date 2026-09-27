"""
Wrapper an toàn quanh FFmpeg / FFprobe.

Nguyên tắc bảo mật quan trọng:
- KHÔNG bao giờ build command bằng string concatenation rồi chạy qua shell=True.
- Luôn dùng subprocess với list argument (asyncio.create_subprocess_exec),
  để input người dùng (tên file, url...) không thể "thoát" ra thành shell command.
"""
import asyncio
import json
import shlex
from pathlib import Path
from dataclasses import dataclass


class FFmpegError(RuntimeError):
    pass


@dataclass
class VideoMeta:
    duration_sec: float
    width: int | None
    height: int | None
    has_audio: bool


async def _run(cmd: list[str]) -> tuple[str, str]:
    """Chạy subprocess bằng argument list (an toàn, không qua shell)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(stderr.decode(errors="ignore")[-4000:])
    return stdout.decode(errors="ignore"), stderr.decode(errors="ignore")


async def probe(file_path: Path) -> VideoMeta:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(file_path),
    ]
    out, _ = await _run(cmd)
    data = json.loads(out)
    duration = float(data.get("format", {}).get("duration", 0.0))
    width = height = None
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and width is None:
            width = stream.get("width")
            height = stream.get("height")
        if stream.get("codec_type") == "audio":
            has_audio = True
    return VideoMeta(duration_sec=duration, width=width, height=height, has_audio=has_audio)


async def extract_audio(video_path: Path, out_wav: Path, sample_rate: int = 16000) -> Path:
    """Trích audio ra WAV mono 16kHz (chuẩn input cho Whisper)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-ac", "1", "-ar", str(sample_rate), "-vn",
        str(out_wav),
    ]
    await _run(cmd)
    return out_wav


def _ass_escape_path(p: Path) -> str:
    """Escape đường dẫn để dùng an toàn trong filter subtitles=... của FFmpeg."""
    s = str(p.resolve())
    s = s.replace("\\", "\\\\").replace(":", "\\:")
    return s


PRESETS = {
    "youtube": {"font_size": 24, "margin_v": 40},
    "tiktok": {"font_size": 32, "margin_v": 120},
    "shorts": {"font_size": 32, "margin_v": 120},
    "movie": {"font_size": 22, "margin_v": 30},
    "anime": {"font_size": 26, "margin_v": 35},
    "social_media": {"font_size": 28, "margin_v": 60},
}


async def burn_subtitle(
    video_path: Path,
    subtitle_path: Path,
    out_path: Path,
    preset: str = "youtube",
    font_name: str = "Arial",
    font_color: str = "&HFFFFFF&",
) -> Path:
    """Burn phụ đề vào video bằng filter subtitles/ass của FFmpeg."""
    p = PRESETS.get(preset, PRESETS["youtube"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sub_escaped = _ass_escape_path(subtitle_path)
    style = f"FontName={font_name},FontSize={p['font_size']},MarginV={p['margin_v']},PrimaryColour={font_color}"
    vf = f"subtitles='{sub_escaped}':force_style='{style}'"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", vf,
        "-c:a", "copy",
        str(out_path),
    ]
    await _run(cmd)
    return out_path


async def blur_region(
    video_path: Path,
    out_path: Path,
    x: int, y: int, w: int, h: int,
) -> Path:
    """Fallback đơn giản: làm mờ (blur) một vùng chữ nhật cố định trong toàn bộ video
    (dùng cho remove-subtitle / remove-logo khi không có model inpainting)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop_blur = f"[0:v]crop={w}:{h}:{x}:{y},boxblur=10:2[blurred]"
    overlay = f"[0:v][blurred]overlay={x}:{y}[out]"
    filter_complex = f"{crop_blur};{overlay}"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "0:a?",
        "-c:a", "copy",
        str(out_path),
    ]
    await _run(cmd)
    return out_path


async def mix_audio(
    original_video: Path,
    dubbed_audio: Path,
    out_path: Path,
    original_volume: float = 0.15,
    dubbed_volume: float = 1.0,
) -> Path:
    """Trộn audio lồng tiếng (dubbed) với audio gốc đã giảm âm lượng (duck)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[0:a]volume={original_volume}[a0];"
        f"[1:a]volume={dubbed_volume}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(original_video),
        "-i", str(dubbed_audio),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        str(out_path),
    ]
    await _run(cmd)
    return out_path


# ---------------------------------------------------------------------------
# Chạy FFmpeg có tiến trình thật + huỷ được
# ---------------------------------------------------------------------------
import collections
from typing import Awaitable, Callable


class FFmpegCancelled(RuntimeError):
    pass


async def run_with_progress(
    cmd: list[str],
    duration_sec: float,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """
    Chạy lệnh FFmpeg (đã có sẵn `-progress pipe:1 -nostats`), đọc out_time để báo tiến trình 0..1.
    - on_progress(frac): gọi mỗi lần FFmpeg báo tiến độ.
    - is_cancelled(): nếu trả True -> kill tiến trình và raise FFmpegCancelled.
    Đọc song song stderr để tránh tắc pipe (deadlock) khi FFmpeg ghi nhiều log.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stderr_tail: collections.deque = collections.deque(maxlen=60)

    async def _drain_stderr():
        assert proc.stderr is not None
        async for line in proc.stderr:
            stderr_tail.append(line.decode(errors="ignore").rstrip())

    stderr_task = asyncio.create_task(_drain_stderr())
    total_us = max(1.0, duration_sec * 1_000_000)

    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="ignore").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                # (out_time_ms thực chất cũng là micro-giây trong FFmpeg)
                try:
                    us = float(line.split("=", 1)[1])
                except ValueError:
                    continue
                if on_progress:
                    await on_progress(max(0.0, min(1.0, us / total_us)))
            if is_cancelled and await is_cancelled():
                proc.kill()
                await proc.wait()
                raise FFmpegCancelled("Job đã bị huỷ")
        await proc.wait()
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    finally:
        await stderr_task

    if proc.returncode != 0:
        raise FFmpegError("\n".join(stderr_tail)[-4000:])
    if on_progress:
        await on_progress(1.0)


async def grab_frame_jpeg(video_path: Path, t_sec: float, max_width: int = 960) -> bytes:
    """Lấy 1 khung hình JPEG tại thời điểm t (dùng cho xem trước khi chọn vùng/logo)."""
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{max(0.0, t_sec):.3f}", "-i", str(video_path),
        "-frames:v", "1", "-vf", f"scale='min({int(max_width)},iw)':-2",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0 or not out:
        raise FFmpegError(err.decode(errors="ignore")[-2000:] or "Không lấy được khung hình")
    return out
