# backend/app/core/auth.py
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings


class AuthError(Exception):
    pass


def decode_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        raise AuthError("SUPABASE_JWT_SECRET not configured")
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as e:
        raise AuthError(str(e)) from e


def get_current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return decode_jwt(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e


CurrentUser = Depends(get_current_user)
