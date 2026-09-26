"""
Cấu hình trung tâm cho AI Video Studio backend.
Đọc từ biến môi trường (.env). KHÔNG hardcode API key ở đây.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "AI Video Studio"
    ENV: str = "development"
    DEBUG: bool = True

    # Thư mục dữ liệu
    DATA_DIR: Path = Path("data")
    UPLOAD_DIR: Path = Path("data/uploads")
    JOBS_DIR: Path = Path("data/jobs")
    OUTPUT_DIR: Path = Path("data/outputs")
    ASSETS_DIR: Path = Path("data/assets")   # logo người dùng tải lên
    FONTS_DIR: Path = Path("data/fonts")     # font phụ đề bạn tự thêm (.ttf/.otf)
    VOICES_DIR: Path = Path("data/voices")   # mẫu giọng đã nhân bản (mỗi người dùng một thư mục, KHÔNG bị dọn tự động)

    # Giới hạn upload
    MAX_UPLOAD_MB: int = 2048
    ALLOWED_VIDEO_EXT: set[str] = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    ALLOWED_AUDIO_EXT: set[str] = {".mp3", ".wav", ".m4a", ".aac"}
    ALLOWED_SUB_EXT: set[str] = {".srt", ".vtt", ".ass", ".txt"}

    # Giới hạn logo tải lên
    MAX_LOGO_MB: int = 10

    # Xử lý video (pipeline)
    MAX_CONCURRENT_JOBS: int = 1   # số job pipeline chạy song song (video nặng -> để 1)
    VIDEO_CRF: int = 18            # chất lượng x264: càng thấp càng nét/nặng (16-23 hợp lý)
    VIDEO_PRESET: str = "medium"   # ultrafast..veryslow: chậm hơn = nén tốt hơn

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

    # Redis / Queue (tùy chọn - nếu không có Redis, job chạy inline/foreground)
    REDIS_URL: str | None = None

    # Whisper
    WHISPER_MODEL_SIZE: str = "base"  # tiny/base/small/medium/large-v3
    WHISPER_DEVICE: str = "cpu"       # cpu hoặc cuda
    WHISPER_COMPUTE_TYPE: str = "int8"

    # Translation providers (API key optional - để trống = dùng local/demo)
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    GEMINI_API_KEY: str | None = None
    DEEPSEEK_API_KEY: str | None = None

    # TTS
    GEMINI_TTS_MODEL: str = "gemini-3.1-flash-tts-preview"   # model giọng Gemini (dùng API key AI của người dùng)
    DEFAULT_TTS_PROVIDER: str = "edge_tts"  # edge_tts | piper | vieneu | demo

    # VieNeu-TTS (tự host, Apache-2.0) — giọng có sẵn + NHÂN BẢN GIỌNG. Cần: pip install -r requirements-voice.txt
    VIENEU_MODE: str = "v3turbo"       # v3turbo (48 kHz, chất lượng cao) | v3nano (24 kHz, nhẹ hơn ~3x, chất lượng thấp hơn)
    VIENEU_PRECISION: str = "fp32"     # fp32 | int8 (nhanh ~1.6x nhưng CẦN CPU có VNNI; CPU cũ có thể ra tiếng rè)
    VIENEU_BACKEND: str = ""           # "" = tự chọn (CUDA nếu có, không thì ONNX/CPU) | onnx = ép chạy CPU
    VIENEU_PRELOAD: bool = False       # true = nạp model ngay khi khởi động (tránh chờ ở lần bấm đầu tiên)
    VOICE_CLONING_ENABLED: bool = True   # false = tắt hẳn tạo/dùng giọng nhân bản (người dùng vẫn XOÁ được mẫu của mình)
    VOICE_CONSENT_VERSION: str = "2026-09-v1"   # đổi khi sửa nội dung xác nhận; được lưu kèm mỗi mẫu giọng
    MAX_VOICE_CLONES_PER_USER: int = 10
    MAX_VOICE_SAMPLE_MB: int = 25

    # File retention (giờ) - cleanup job sẽ xóa file cũ hơn mốc này
    RETENTION_HOURS: int = 24          # xoá video tải lên/file tạm sau bấy nhiêu giờ
    OUTPUT_RETENTION_HOURS: int = 72   # xoá video kết quả sau bấy nhiêu giờ (hãy tải về trước!)
    FRONTEND_DIR: Path | None = None   # thư mục giao diện web (mặc định tự tìm ../frontend)

    # CORS: mặc định TẮT (frontend cùng origin với backend). Chỉ điền khi host frontend ở domain khác.
    CORS_ORIGINS: list[str] = []

    # ---------------- Tài khoản ----------------
    APP_SECRET: str | None = None            # bí mật gốc: ký phiên + dẫn xuất khoá mã hoá. NÊN đặt riêng trong Secrets
    SESSION_TTL_HOURS: int = 24 * 7
    COOKIE_NAME: str = "vs_session"
    COOKIE_SECURE: bool | None = None        # None = tự bật khi request là HTTPS
    TRUST_PROXY: bool = False                # true nếu sau reverse proxy (đọc X-Forwarded-For/Proto)
    REGISTRATION_OPEN: bool = True


    def ensure_dirs(self) -> None:
        for d in (self.DATA_DIR, self.UPLOAD_DIR, self.JOBS_DIR, self.OUTPUT_DIR, self.ASSETS_DIR, self.FONTS_DIR, self.VOICES_DIR):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
