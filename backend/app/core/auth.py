# backend/app/core/auth.py
from typing import Any, cast

import httpx
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings


class AuthError(Exception):
    pass


_jwks_cache: dict[str, Any] | None = None


def _jwks_url() -> str:
    settings = get_settings()
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _fetch_jwks() -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is None:
        resp = httpx.get(_jwks_url(), timeout=5.0)
        resp.raise_for_status()
        _jwks_cache = cast(dict[str, Any], resp.json())
    return _jwks_cache


def _clear_jwks_cache() -> None:
    """Test helper. Force re-fetch on next decode."""
    global _jwks_cache
    _jwks_cache = None


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        if not kid:
            raise AuthError("token missing kid header")

        jwks = _fetch_jwks()
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key is None:
            # Token's kid not in our cached JWKS. Force refresh and retry once
            # in case the project rotated keys.
            _clear_jwks_cache()
            jwks = _fetch_jwks()
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if key is None:
                raise AuthError(f"no JWKS key found for kid={kid}")

        result: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "ES256")],
            audience="authenticated",
        )
        return result
    except JWTError as e:
        raise AuthError(str(e)) from e
    except httpx.HTTPError as e:
        raise AuthError(f"JWKS fetch failed: {e}") from e


def get_current_token(
    authorization: str | None = Header(default=None),
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return authorization.split(" ", 1)[1]


def get_current_user(
    token: str = Depends(get_current_token),
) -> dict[str, Any]:
    try:
        return decode_jwt(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e


CurrentUser = Depends(get_current_user)
CurrentToken = Depends(get_current_token)
