from __future__ import annotations

from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from supabase import Client, create_client
except ImportError:  # pragma: no cover - optional dependency
    Client = None  # type: ignore
    create_client = None  # type: ignore

_client: Optional["Client"] = None


def get_supabase_client() -> Optional["Client"]:
    """
    Initialize and cache a Supabase client when credentials are provided.
    Returns None when Supabase is not configured so the app can fall back to the
    in-memory repository without crashing.
    """
    global _client
    if _client is not None:
        return _client

    if not (settings.supabase_url and settings.supabase_anon_key):
        logger.info("Supabase credentials not configured; using in-memory storage.")
        return None

    if create_client is None:
        logger.warning("supabase package not installed; cannot initialize Supabase client.")
        return None

    try:
        _client = create_client(settings.supabase_url, settings.supabase_anon_key)
        logger.info("Supabase client initialized.")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Failed to initialize Supabase client: %s", exc)
        _client = None
    return _client
