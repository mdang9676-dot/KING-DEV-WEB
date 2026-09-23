"""
Dependency dùng chung — CHẾ ĐỘ 1 NGƯỜI DÙNG (đã bỏ đăng nhập).

Không còn kiểm tra phiên/cookie: mọi request đều dùng chung một user "local" duy nhất,
tự tạo ngầm trong DB lần đầu cần tới. get_owned vẫn lọc theo user_id như cũ nên không
phải sửa lại các router khác — chỉ có điều giờ luôn là cùng một người dùng.
"""
from contextvars import ContextVar

from app.models.db import async_session, User

# Dependency là async nên ContextVar đặt ở đây nhìn thấy được trong endpoint cùng request.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)

LOCAL_USER_ID = "local"
LOCAL_USER_EMAIL = "local@local"


async def _get_or_create_local_user() -> User:
    async with async_session() as s:
        user = await s.get(User, LOCAL_USER_ID)
        if user is None:
            user = User(id=LOCAL_USER_ID, email=LOCAL_USER_EMAIL, name="Local", password_hash="")
            s.add(user)
            await s.commit()
    return user


async def get_current_user() -> User:
    """Không còn đăng nhập: luôn trả về user local duy nhất."""
    return await _get_or_create_local_user()


async def require_user() -> User:
    """Giữ tên/kiểu cũ để các router khác không phải sửa — luôn thành công, không cần đăng nhập."""
    user = await get_current_user()
    current_user_id.set(user.id)
    return user


async def get_owned(session, model, obj_id: str):
    """Trả về bản ghi nếu thuộc người dùng hiện tại, ngược lại None (caller trả 404 như 'không tồn tại')."""
    obj = await session.get(model, obj_id)
    uid = current_user_id.get()
    if obj is None or uid is None or getattr(obj, "user_id", None) != uid:
        return None
    return obj
