"""
Định nghĩa bảng database tối thiểu: Project, Video, Job, SubtitleSegment.
Dùng SQLAlchemy async + SQLite (mặc định) hoặc PostgreSQL qua DATABASE_URL.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, Enum, Boolean
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import settings


def new_id() -> str:
    return uuid.uuid4().hex


class Base(AsyncAttrs, DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    TRANSCRIBING = "TRANSCRIBING"
    TRANSLATING = "TRANSLATING"
    GENERATING_AUDIO = "GENERATING_AUDIO"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), default="Untitled Project")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    videos: Mapped[list["Video"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), default="upload")  # upload | url
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="videos")
    jobs: Mapped[list["Job"]] = relationship(back_populates="video", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"))
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    job_type: Mapped[str] = mapped_column(String(50))  # transcribe | translate | burn | dub | remove_sub | remove_logo
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    stage: Mapped[str | None] = mapped_column(String(200), nullable=True)  # mô tả bước đang chạy
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    video: Mapped["Video"] = relationship(back_populates="jobs")
    segments: Mapped[list["SubtitleSegment"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class SubtitleSegment(Base):
    __tablename__ = "subtitle_segments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    speaker: Mapped[str | None] = mapped_column(String(50), nullable=True)

    job: Mapped["Job"] = relationship(back_populates="segments")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserAISettings(Base):
    """API key AI của người dùng (để dịch/lồng tiếng). Key được MÃ HOÁ (Fernet) — không lưu chữ thường."""
    __tablename__ = "user_ai_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    key_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


def _migrate(sync_conn):
    """Migration nhẹ: thêm cột mới vào DB cũ (create_all không tự ALTER bảng đã tồn tại)."""
    from sqlalchemy import inspect, text
    insp = inspect(sync_conn)
    if "jobs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("jobs")}
        if "stage" not in cols:
            sync_conn.execute(text("ALTER TABLE jobs ADD COLUMN stage VARCHAR(200)"))
        if "user_id" not in cols:
            sync_conn.execute(text("ALTER TABLE jobs ADD COLUMN user_id VARCHAR(32)"))
    if "videos" in insp.get_table_names():
        vcols = {c["name"] for c in insp.get_columns("videos")}
        if "user_id" not in vcols:
            sync_conn.execute(text("ALTER TABLE videos ADD COLUMN user_id VARCHAR(32)"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)


async def get_session():
    async with async_session() as session:
        yield session
