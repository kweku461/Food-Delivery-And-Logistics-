from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings
from app.errors import AuthenticationError, BadRequestError


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:   # bcrypt limit
        raise BadRequestError("Password must be at most 72 bytes long")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, role: str, expires_minutes: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": exp}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid token")
