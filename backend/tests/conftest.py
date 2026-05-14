# backend/tests/conftest.py
from collections.abc import AsyncGenerator, Generator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key-test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-test")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret-for-unit-tests-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # Bust lru_cache so each test re-reads env and rebuilds clients
    from app.config import get_settings
    from app.core.supabase_client import get_service_client
    get_settings.cache_clear()
    get_service_client.cache_clear()
    yield


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
