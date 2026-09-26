"""
Background task chạy định kỳ để xóa file tạm cũ hơn RETENTION_HOURS.
Giúp tránh tích tụ video/audio tạm chiếm hết dung lượng đĩa server.
"""
import asyncio
import shutil
import time

from app.core.config import settings


async def cleanup_once():
    now = time.time()
    plan = (
        (settings.UPLOAD_DIR, settings.RETENTION_HOURS),
        (settings.JOBS_DIR, settings.RETENTION_HOURS),
        (settings.OUTPUT_DIR, settings.OUTPUT_RETENTION_HOURS),
    )
    for base_dir, hours in plan:
        if not base_dir.exists():
            continue
        cutoff = now - hours * 3600
        for entry in base_dir.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
            except FileNotFoundError:
                continue


async def cleanup_loop(interval_sec: int = 3600):
    """Chạy cleanup mỗi interval_sec giây (mặc định 1 giờ) cho đến khi bị cancel."""
    while True:
        try:
            await cleanup_once()
        except Exception:
            pass  # cleanup lỗi không được làm sập app chính
        await asyncio.sleep(interval_sec)
