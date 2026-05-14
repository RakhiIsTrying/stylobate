# backend/tests/conftest.py
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient
from jose.utils import base64url_encode

# Generate one ES256 keypair for the whole test session.
_TEST_KEY = ec.generate_private_key(ec.SECP256R1())
_TEST_KID = "test-key-1"


def _public_jwk() -> dict[str, Any]:
    pub_numbers = _TEST_KEY.public_key().public_numbers()
    x = pub_numbers.x.to_bytes(32, "big")
    y = pub_numbers.y.to_bytes(32, "big")
    return {
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "use": "sig",
        "kid": _TEST_KID,
        "x": base64url_encode(x).decode().rstrip("="),
        "y": base64url_encode(y).decode().rstrip("="),
    }


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key-test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from app.config import get_settings
    from app.core.auth import _clear_jwks_cache
    from app.core.supabase_client import get_service_client
    get_settings.cache_clear()
    get_service_client.cache_clear()
    _clear_jwks_cache()
    yield


@pytest.fixture(autouse=True)
def _mock_jwks() -> Generator[None, None, None]:
    with respx.mock(assert_all_called=False, assert_all_mocked=False) as mock:
        mock.get("https://test.supabase.co/auth/v1/.well-known/jwks.json").respond(
            json={"keys": [_public_jwk()]},
        )
        yield


def _make_test_token(sub: str, exp_offset: int = 3600) -> str:
    """Helper used by tests. Signs an ES256 JWT with the test private key."""
    import time

    from jose import jwt

    private_pem = _TEST_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    return jwt.encode(
        {
            "sub": sub,
            "aud": "authenticated",
            "exp": now + exp_offset,
            "iat": now,
            "role": "authenticated",
        },
        private_pem,
        algorithm="ES256",
        headers={"kid": _TEST_KID},
    )


@pytest.fixture
def make_token() -> Any:
    return _make_test_token


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
