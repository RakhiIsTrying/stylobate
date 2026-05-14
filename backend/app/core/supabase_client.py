# backend/app/core/supabase_client.py
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache(maxsize=1)
def get_service_client() -> Client:
    """Service-role client. Bypasses RLS. Use only on server, never expose."""
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_user_client(jwt: str) -> Client:
    """Per-request client scoped to the authenticated user via RLS."""
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(jwt)
    return client
