"""
Giới hạn số lần TẠO VIDEO THÀNH CÔNG theo gói (đúng yêu cầu: "tính theo số lần tạo video thành
công" — job lỗi/huỷ không tính). Gói Free: giới hạn trọn đời, không reset. Gói trả phí (vip/pro/
promax): giới hạn theo ngày, tự reset 24h kể từ lần tạo gần nhất mốc reset đã qua; gói hết hạn
(plan_expires_at) tự coi như rơi về free mà không cần job nền riêng để hạ cấp.

check_quota() gọi TRƯỚC khi nhận job (routers/pipeline.py) để chặn sớm, không tốn tài nguyên
render một video rồi mới báo hết lượt.
consume_quota() gọi đúng 1 lần SAU KHI job pipeline đạt JobStatus.COMPLETED
(services/pipeline_runner.py) — đây là nơi duy nhất tính "thành công".
"""
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.deps import err
from app.models.db import User, async_session


def plan_active(user: User) -> bool:
    return (
        user.plan in settings.PLAN_DAILY_LIMIT
        and user.plan_expires_at is not None
        and user.plan_expires_at > datetime.utcnow()
    )


def remaining_quota(user: User) -> tuple[int, int]:
    """Trả về (đã dùng, giới hạn) áp dụng ngay lúc này."""
    if plan_active(user):
        used = user.video_count_period
        if user.video_count_reset_at and user.video_count_reset_at <= datetime.utcnow():
            used = 0  # đã qua mốc reset, consume_quota sẽ ghi nhận lại 0 ở lần tạo kế tiếp
        return used, settings.PLAN_DAILY_LIMIT[user.plan]
    return user.video_count_total, settings.FREE_VIDEO_LIFETIME_LIMIT


def check_quota(user: User) -> None:
    """Raise lỗi 403 nếu người dùng đã hết lượt tạo video. Gọi trước khi tạo job."""
    used, limit = remaining_quota(user)
    if used >= limit:
        if plan_active(user):
            raise err(403, "QUOTA_EXCEEDED",
                      f"Gói {user.plan.upper()} đã dùng hết {limit} lượt tạo video hôm nay. Lượt mới sẽ có sau khi qua mốc reset 24h.")
        raise err(403, "QUOTA_EXCEEDED",
                  f"Gói Free chỉ tạo tối đa {limit} video (trọn đời). Hãy nâng cấp gói ở mục Ví để tạo thêm.")


async def consume_quota(user_id: str) -> None:
    """Gọi đúng 1 lần khi 1 job tạo video HOÀN TẤT (JobStatus.COMPLETED)."""
    async with async_session() as s:
        user = await s.get(User, user_id)
        if not user:
            return
        now = datetime.utcnow()
        if plan_active(user):
            if not user.video_count_reset_at or user.video_count_reset_at <= now:
                user.video_count_period = 0
                user.video_count_reset_at = now + timedelta(hours=24)
            user.video_count_period += 1
        else:
            user.video_count_total += 1
        await s.commit()
