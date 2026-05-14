# backend/tests/test_auth.py
import time
import uuid
from collections.abc import Callable

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt as jose_jwt


@pytest.mark.asyncio
async def test_decode_valid_token(make_token: Callable[..., str]) -> None:
    from app.core.auth import decode_jwt
    sub = str(uuid.uuid4())
    token = make_token(sub=sub)
    payload = decode_jwt(token)
    assert payload["sub"] == sub
    assert payload["aud"] == "authenticated"


@pytest.mark.asyncio
async def test_decode_expired_token(make_token: Callable[..., str]) -> None:
    from app.core.auth import AuthError, decode_jwt
    token = make_token(sub=str(uuid.uuid4()), exp_offset=-10)
    with pytest.raises(AuthError):
        decode_jwt(token)


@pytest.mark.asyncio
async def test_decode_bad_signature() -> None:
    from app.core.auth import AuthError, decode_jwt
    # Sign with a DIFFERENT private key — the JWKS public key won't match
    wrong_key = ec.generate_private_key(ec.SECP256R1())
    wrong_pem = wrong_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    bad = jose_jwt.encode(
        {"sub": "x", "aud": "authenticated", "exp": now + 3600, "iat": now},
        wrong_pem,
        algorithm="ES256",
        # same kid as mocked JWKS — signature mismatch, not kid mismatch
        headers={"kid": "test-key-1"},
    )
    with pytest.raises(AuthError):
        decode_jwt(bad)
