"""
Tiện ích xác thực dùng thuần thư viện chuẩn (dễ kiểm thử, ít phụ thuộc):
- Băm mật khẩu bằng scrypt (có salt ngẫu nhiên, so sánh hằng-thời-gian).
- Token phiên dạng JWT HS256 tối giản (ký HMAC-SHA256, có hạn).
- Kiểm tra email/mật khẩu, giới hạn số lần thử (chống dò mật khẩu/dò key).
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 2 ** 14, 8, 1, 32
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------- mật khẩu ----------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Luôn tốn thời gian tương đương kể cả khi stored rỗng (giảm rò rỉ 'tài khoản có tồn tại không')."""
    if not stored or not stored.startswith("scrypt$"):
        hashlib.scrypt(b"x", salt=b"0" * 16, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
        return False
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode("utf-8"), salt=_b64d(salt), n=int(n), r=int(r), p=int(p), dklen=len(_b64d(dk)))
        return hmac.compare_digest(calc, _b64d(dk))
    except (ValueError, TypeError):
        return False


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email))


def password_problem(password: str) -> str | None:
    """Trả về thông báo lỗi nếu mật khẩu yếu, None nếu ổn."""
    if len(password) < 8:
        return "Mật khẩu phải có ít nhất 8 ký tự."
    if len(password) > 128:
        return "Mật khẩu quá dài (tối đa 128 ký tự)."
    if password.isdigit() or password.isalpha():
        return "Mật khẩu cần có cả chữ và số (hoặc ký tự khác)."
    if password.lower() in {"12345678", "password", "matkhau123", "password1", "qwerty123"}:
        return "Mật khẩu quá phổ biến."
    return None


# ---------------- token phiên (JWT HS256) ----------------

def issue_token(user_id: str, secret: bytes, ttl_seconds: int) -> str:
    now = int(time.time())
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64e(json.dumps({"sub": user_id, "iat": now, "exp": now + ttl_seconds}, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def decode_token(token: str, secret: bytes) -> dict | None:
    """Trả về payload nếu chữ ký đúng và chưa hết hạn, ngược lại None. Chỉ chấp nhận HS256."""
    try:
        header_b, payload_b, sig_b = token.split(".")
        header = json.loads(_b64d(header_b))
        if header.get("alg") != "HS256":       # chặn tấn công alg=none / đổi thuật toán
            return None
        expected = hmac.new(secret, f"{header_b}.{payload_b}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b)):
            return None
        payload = json.loads(_b64d(payload_b))
        if int(payload.get("exp", 0)) < int(time.time()) or not payload.get("sub"):
            return None
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


# ---------------- giới hạn số lần thử ----------------

class Throttle:
    """Cửa sổ trượt trong RAM: tối đa `limit` lần THẤT BẠI trong `window` giây cho mỗi khoá."""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str) -> deque:
        q = self._hits[key]
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.limit

    def fail(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def retry_after(self, key: str) -> int:
        q = self._prune(key)
        return int(self.window - (time.monotonic() - q[0])) + 1 if q else 0
