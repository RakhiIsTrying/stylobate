import os
from unittest.mock import patch

from app.config import Settings


def test_settings_load_from_env() -> None:
    env = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "anon",
        "SUPABASE_SERVICE_ROLE_KEY": "service",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000,https://stylobate.app",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=False):
        s = Settings(_env_file=None)
    assert s.supabase_url == "https://test.supabase.co"
    assert s.supabase_anon_key == "anon"
    assert s.cors_origins == ["http://localhost:3000", "https://stylobate.app"]


def test_settings_default_cors() -> None:
    env = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "anon",
        "SUPABASE_SERVICE_ROLE_KEY": "service",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings(_env_file=None)
    assert s.cors_origins == ["http://localhost:3000"]
