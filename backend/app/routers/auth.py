"""
/api/auth/*  — đăng ký, đăng nhập bằng email + mật khẩu, đăng xuất.

Bảo mật: mật khẩu băm scrypt; phiên là JWT ký HMAC trong cookie httpOnly + SameSite=Lax (+Secure khi HTTPS);
đăng nhập báo lỗi chung chung và giới hạn số lần thử theo IP+email. Chưa có xác minh email (cần SMTP).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core import auth_utils, crypto
from app.core.config import settings
from app.core.deps import client_ip, err, get_current_user, is_https
from app.models.db import async_session, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_throttle = auth_utils.Throttle(limit=5, window=600)     # 5 lần sai / 10 phút / (IP, email)
_register_throttle = auth_utils.Throttle(limit=10, window=3600)  # 10 lần đăng ký / giờ / IP


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    name: str | None = Field(default=None, max_length=120)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


def _set_session(response: Response, request: Request, user: User) -> None:
    ttl = settings.SESSION_TTL_HOURS * 3600
    token = auth_utils.issue_token(user.id, crypto.get_app_secret(), ttl)
    response.set_cookie(
        settings.COOKIE_NAME, token, max_age=ttl, httponly=True, samesite="lax",
        secure=is_https(request), path="/",
    )


async def _user_payload(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name,
    }


@router.get("/config")
async def auth_config():
    """Cấu hình công khai cho trang đăng nhập."""
    return {
        "registration_open": settings.REGISTRATION_OPEN,
    }


@router.post("/register")
async def register(body: RegisterIn, request: Request, response: Response):
    if not settings.REGISTRATION_OPEN:
        raise err(403, "REGISTRATION_CLOSED", "Hiện không mở đăng ký tài khoản mới.")
    ip = client_ip(request)
    if _register_throttle.blocked(ip):
        raise err(429, "TOO_MANY_ATTEMPTS", "Đăng ký quá nhiều lần, vui lòng thử lại sau.")
    _register_throttle.fail(ip)   # đếm mọi lượt đăng ký (không chỉ lượt lỗi)

    email = auth_utils.normalize_email(body.email)
    if not auth_utils.is_valid_email(email):
        raise err(400, "BAD_EMAIL", "Email không hợp lệ.")
    problem = auth_utils.password_problem(body.password)
    if problem:
        raise err(400, "WEAK_PASSWORD", problem)

    async with async_session() as s:
        if (await s.execute(select(User).where(User.email == email))).scalar_one_or_none():
            raise err(409, "EMAIL_EXISTS", "Email này đã được đăng ký. Hãy đăng nhập.")
        user = User(email=email, name=(body.name or "").strip() or None,
                    password_hash=auth_utils.hash_password(body.password), last_login_at=datetime.utcnow())
        s.add(user)
        await s.commit()
    _set_session(response, request, user)
    return await _user_payload(user)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    email = auth_utils.normalize_email(body.email)
    tk = f"{client_ip(request)}|{email}"
    if _login_throttle.blocked(tk):
        response.headers["Retry-After"] = str(_login_throttle.retry_after(tk))
        raise err(429, "TOO_MANY_ATTEMPTS", f"Sai quá nhiều lần. Thử lại sau {_login_throttle.retry_after(tk)} giây.")

    async with async_session() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        ok = auth_utils.verify_password(body.password, user.password_hash if user else None)
        if not user or not ok or not user.is_active:
            _login_throttle.fail(tk)
            raise err(401, "BAD_CREDENTIALS",
                      "Email hoặc mật khẩu không đúng.")
        user.last_login_at = datetime.utcnow()
        await s.commit()
    _login_throttle.reset(tk)
    _set_session(response, request, user)
    return await _user_payload(user)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(settings.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return await _user_payload(user)
