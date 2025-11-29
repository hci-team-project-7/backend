from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings


class SupabaseClientStub:
    def __init__(self) -> None:
        self.enabled = False

    def table(self, name: str) -> "SupabaseClientStub":
        return self

    async def insert(self, data: Any) -> Any:
        return data

    async def update(self, data: Any) -> Any:
        return data

    async def select(self, *args: Any, **kwargs: Any) -> Any:
        return []


def get_supabase_client() -> Optional[SupabaseClientStub]:
    """Returns a stub unless credentials are provided."""
    if settings.supabase_url and settings.supabase_anon_key:
        # Real client could be initialized here.
        return SupabaseClientStub()
    return None
