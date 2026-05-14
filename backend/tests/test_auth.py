# backend/tests/test_auth.py
import time
import uuid

import pytest
from jose import jwt

SECRET = "test-secret-for-unit-tests-only"  # nosec


def _make_token(sub: str | None = None, exp_offset: int = 3600) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": sub or str(uuid.uuid4()),
        "aud": "authenticated",
        "exp": now + exp_offset,
        "iat": now,
        "role": "authenticated",
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.mark.asyncio
async def test_decode_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.core.auth import decode_jwt
    sub = str(uuid.uuid4())
    token = _make_token(sub=sub)
    payload = decode_jwt(token)
    assert payload["sub"] == sub
    assert payload["aud"] == "authenticated"


@pytest.mark.asyncio
async def test_decode_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.core.auth import AuthError, decode_jwt
    token = _make_token(exp_offset=-10)
    with pytest.raises(AuthError):
        decode_jwt(token)


@pytest.mark.asyncio
async def test_decode_bad_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.core.auth import AuthError, decode_jwt
    bad = jwt.encode({"sub": "x", "aud": "authenticated"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(AuthError):
        decode_jwt(bad)
