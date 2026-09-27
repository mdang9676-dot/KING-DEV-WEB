"""
Service chuyển giọng nói thành văn bản (speech-to-text) dùng faster-whisper.

Model được load 1 lần (singleton) và tái sử dụng giữa các request để tránh
tốn thời gian/tài nguyên load lại mỗi lần.
"""
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings


@dataclass
class TranscriptSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str


WHISPER_SIZES = ("tiny", "base", "small", "medium", "large-v3")


def resolve_model_size(name: str | None) -> str:
    """Cỡ model do người dùng chọn (None = mặc định trong cấu hình). Chỉ nhận giá trị trong danh sách."""
    size = name or settings.WHISPER_MODEL_SIZE
    if size not in WHISPER_SIZES:
        raise ValueError(f"Cỡ model Whisper không hợp lệ: {size!r} (chọn một trong {WHISPER_SIZES})")
    return size


@lru_cache(maxsize=1)      # chỉ giữ 1 model trong RAM; đổi cỡ thì nạp lại (model lớn rất tốn RAM)
def _get_model(size: str):
    # Import trễ (lazy) để app khởi động được ngay cả khi chưa cài faster-whisper
    # (ví dụ khi chỉ chạy demo mode frontend).
    from faster_whisper import WhisperModel
    return WhisperModel(
        size,
        device=settings.WHISPER_DEVICE,
        compute_type=settings.WHISPER_COMPUTE_TYPE,
    )


def transcribe_audio(wav_path: str, language: str | None = None, model_size: str | None = None) -> tuple[list[TranscriptSegment], str]:
    """
    Trả về (danh sách segment có timestamp, mã ngôn ngữ phát hiện được).
    language=None -> tự động phát hiện ngôn ngữ.
    """
    model = _get_model(resolve_model_size(model_size))
    segments_gen, info = model.transcribe(
        wav_path,
        language=language,
        vad_filter=True,  # lọc khoảng lặng, giúp timestamp chính xác hơn
        word_timestamps=False,
    )
    results: list[TranscriptSegment] = []
    for i, seg in enumerate(segments_gen):
        results.append(
            TranscriptSegment(
                index=i,
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                text=seg.text.strip(),
            )
        )
    return results, info.language
