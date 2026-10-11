"""Password hashing, login tokens and a tiny rate limiter."""
import os
import time
from dataclasses import dataclass

import bcrypt
import jwt
import redis

TOKEN_TTL_SECONDS = 12 * 3600
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set and at least 32 characters long")
    return secret


@dataclass
class Claims:
    user_id: int
    name: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    # Always do one bcrypt check, even for unknown emails, so timing doesn't reveal who has an account.
    return bcrypt.checkpw(password.encode(), (hashed or _DUMMY_HASH).encode()) and hashed is not None


def make_token(user_id: int, name: str) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "name": name, "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> Claims:
    data = jwt.decode(token, jwt_secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    return Claims(user_id=int(data["sub"]), name=str(data.get("name", "")))


def rate_limit(r: redis.Redis, key: str, limit: int, window_seconds: int) -> bool:
    """Return True if the request is allowed. If Redis is down we allow it (fail open)."""
    try:
        count = r.incr(f"rl:{key}")
        if count == 1:
            r.expire(f"rl:{key}", window_seconds)
        return count <= limit
    except redis.RedisError:
        return True
