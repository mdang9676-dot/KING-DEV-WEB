"""
Mã hoá bí mật cấu hình + băm key.

- APP_SECRET: lấy từ biến môi trường/.env; nếu chưa có (chế độ dev) sẽ tự sinh và lưu vào
  data/.app_secret (quyền 600) để phiên đăng nhập không mất khi khởi động lại.
- encrypt_text/decrypt_text: Fernet (AES-128-CBC + HMAC-SHA256) với khoá dẫn xuất từ APP_SECRET.

Giới hạn cần hiểu đúng: mã hoá này bảo vệ bí mật khi nó nằm trong file/log/ảnh chụp màn hình/repo.
Nó KHÔNG chống được kẻ đã có APP_SECRET (vì server buộc phải giải mã để dùng). Vì vậy hãy để
APP_SECRET trong kho bí mật của nền tảng (Replit Secrets, Docker secret...) tách khỏi file .env.
"""
import base64
import hashlib
import hmac
import os
import secrets as _stdlib_secrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretDecryptError(ValueError):
    pass


@lru_cache(maxsize=1)
def get_app_secret() -> bytes:
    if settings.APP_SECRET:
        if len(settings.APP_SECRET) < 24:
            raise RuntimeError("APP_SECRET quá ngắn (cần >= 24 ký tự ngẫu nhiên).")
        return settings.APP_SECRET.encode("utf-8")
    path: Path = settings.DATA_DIR / ".app_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    value = _stdlib_secrets.token_urlsafe(48)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
    return value.encode("utf-8")


def _derive(purpose: bytes, secret: bytes | None = None) -> bytes:
    """Dẫn xuất khoá con theo mục đích, để khoá mã hoá và khoá băm không dùng chung."""
    return hmac.new(secret or get_app_secret(), b"videostudio|" + purpose, hashlib.sha256).digest()


def _fernet(secret: bytes | None = None) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(b"fernet", secret)))


def encrypt_text(text: str, secret: bytes | None = None) -> str:
    return _fernet(secret).encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_text(token: str, secret: bytes | None = None) -> str:
    try:
        return _fernet(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise SecretDecryptError("Không giải mã được (sai APP_SECRET hoặc dữ liệu bị sửa)") from e
