"""
Dependency dùng chung — xác thực thật qua session cookie (JWT HS256, xem app.core.auth_utils
và app.core.crypto), cộng thêm các tiện ích client_ip / is_https / err mà auth.py và voices.py
cần (đăng ký, đăng nhập, giới hạn số lần thử theo IP, cookie Secure khi chạy HTTPS).

Lưu ý kiến trúc: main.py định tuyến /api/auth/* là công khai, mọi router nghiệp vụ còn lại
bắt buộc đăng nhập qua dependency require_user. Vì vậy get_current_user PHẢI kiểm tra phiên
thật (không được luôn trả về một user cố định) — nếu không toàn bộ phần "bắt buộc đăng nhập"
ở main.py và cơ chế AUTH_REQUIRED mà frontend (api.js) dựa vào để tự đưa người dùng về màn
hình đăng nhập khi phiên hết hạn sẽ vô nghĩa.
"""
from contextvars import ContextVar

from fastapi import Depends, HTTPException, Request

from app.core import auth_utils, crypto
from app.core.config import settings
from app.models.db import User, async_session

# Dependency là async nên ContextVar đặt ở đây nhìn thấy được trong endpoint cùng request.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def err(status_code: int, code: str, message: str) -> HTTPException:
    """Lỗi chuẩn hoá dùng khắp router: frontend (frontend/js/api.js -> parseError) đọc
    data.detail.code và data.detail.message, không phải detail dạng chuỗi thường của FastAPI."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def client_ip(request: Request) -> str:
    """IP thật của client, dùng để giới hạn số lần đăng ký/đăng nhập sai (auth_utils.Throttle).
    Chỉ tin header X-Forwarded-For khi TRUST_PROXY=true (chạy sau reverse proxy như Render/Nginx),
    nếu không client có thể tự set header này để né rate-limit."""
    if settings.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    """Request có đang chạy trên HTTPS không — quyết định cookie phiên có gắn cờ Secure hay
    không (_set_session trong auth.py). Khi TRUST_PROXY=true, TLS thường được kết thúc ở
    reverse proxy nên phải nhìn header X-Forwarded-Proto thay vì request.url.scheme."""
    if settings.TRUST_PROXY and request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return request.url.scheme == "https"


async def get_current_user(request: Request) -> User:
    """Đọc cookie phiên (settings.COOKIE_NAME), xác minh chữ ký JWT bằng APP_SECRET, trả về
    User tương ứng. Lỗi 401 dùng code "AUTH_REQUIRED" — frontend so khớp đúng chuỗi này
    (api.js: err.code === 'AUTH_REQUIRED') để tự động hiện lại màn hình đăng nhập."""
    token = request.cookies.get(settings.COOKIE_NAME)
    if not token:
        raise err(401, "AUTH_REQUIRED", "Bạn cần đăng nhập.")
    payload = auth_utils.decode_token(token, crypto.get_app_secret())
    if not payload:
        raise err(401, "AUTH_REQUIRED", "Phiên đăng nhập đã hết hạn, hãy đăng nhập lại.")
    async with async_session() as s:
        user = await s.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise err(401, "AUTH_REQUIRED", "Tài khoản không tồn tại hoặc đã bị khoá.")
    return user


async def require_user(user: User = Depends(get_current_user)) -> User:
    """Dùng làm dependency chặn ở cấp router (main.py: dependencies=[Depends(require_user)]).
    Giữ nguyên tên/kiểu trả về như trước để không phải sửa các router khác; điểm khác là giờ
    thật sự đòi hỏi phiên đăng nhập hợp lệ, đồng thời set current_user_id cho get_owned dùng."""
    current_user_id.set(user.id)
    return user


async def get_owned(session, model, obj_id: str):
    """Trả về bản ghi nếu thuộc người dùng hiện tại, ngược lại None (caller trả 404 như 'không tồn tại')."""
    obj = await session.get(model, obj_id)
    uid = current_user_id.get()
    if obj is None or uid is None or getattr(obj, "user_id", None) != uid:
        return None
    return obj
