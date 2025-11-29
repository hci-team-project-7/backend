from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI

from app.core.config import settings

_client: Optional[AsyncOpenAI] = None

if settings.openai_api_key:
    _client = AsyncOpenAI(api_key=settings.openai_api_key)


def get_client() -> Optional[AsyncOpenAI]:
    """Returns AsyncOpenAI client if api key is configured."""
    return _client
